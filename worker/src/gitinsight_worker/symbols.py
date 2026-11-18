"""tree-sitter로 Python 소스에서 함수, 메서드, 클래스의 줄 범위를 뽑습니다.

줄 번호는 이 모듈의 결과에서만 나옵니다. LLM은 여기서 만든 symbol_id 목록 중에서 고르기만 합니다.
- 줄은 1부터 셉니다. 데코레이터 줄은 범위에 넣지 않고 `def`/`class` 줄부터 시작합니다.
- 모듈 최상위와 클래스 안의 정의만 SymbolRef로 내보냅니다.
  함수 안에 중첩된 정의는 면접 질문 재료로 쓰지 않지만 symbol_count에는 셉니다.
- 같은 파싱 결과로 파일 안의 이름 참조(식별자)를 셉니다 (#58). 이름만 보므로 정확하지 않습니다.
  같은 이름의 다른 코드가 있으면 많게, 문자열로 부르는 동적 호출이 있으면 적게 셉니다.
"""

import logging
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Literal

import tree_sitter_python
from tree_sitter import Language, Node, Parser, Tree

from gitinsight_contracts.common import SymbolKind, SymbolRef

logger = logging.getLogger(__name__)

# SymbolRef.symbol_id의 최대 길이입니다. 넘으면 계약 검증에 실패하므로 그 심볼은 건너뜁니다.
MAX_SYMBOL_ID_LENGTH = 200

_PYTHON = Language(tree_sitter_python.language())
_FUNCTION = "function_definition"
_CLASS = "class_definition"
_DECORATED = "decorated_definition"
# import 문 안의 이름은 가져오기만 한 것이라 참조로 세지 않습니다. 실제로 쓰는 곳에서 셉니다.
_IMPORTS = frozenset({"import_statement", "import_from_statement", "future_import_statement"})

Container = Literal["module", "class", "function"]


@dataclass(frozen=True)
class ExtractedFile:
    symbols: list[SymbolRef]
    # 함수 안에 중첩된 정의까지 센 정의 수입니다.
    definition_count: int
    # 파일 안에서 이름별로 참조한 횟수입니다. 정의 자신의 이름과 import 문은 빼고 셉니다.
    references: Counter[str] = field(default_factory=Counter[str])
    # 심볼 자신의 줄 범위 안에서 자기 이름을 참조한 횟수입니다(재귀 호출 등). 레포 전체 참조 수에서 뺍니다.
    self_references: dict[str, int] = field(default_factory=dict[str, int])
    # 데코레이터가 붙었거나 특수 메서드(__init__ 등)라 프레임워크나 언어가 부를 수 있는 심볼입니다.
    # 이름으로 찾은 참조가 없어도 쓰이지 않는다고 볼 수 없습니다.
    implicitly_called: frozenset[str] = frozenset()


def _line_range(node: Node) -> tuple[int, int]:
    start = node.start_point.row + 1
    end_row, end_column = node.end_point
    # 노드가 다음 줄 맨 앞에서 끝나면 마지막 줄은 그 앞줄입니다.
    end = end_row + 1 if end_column > 0 else end_row
    return start, max(start, end)


def _walk(root: Node) -> Iterator[tuple[Node, tuple[str, ...], Container]]:
    """정의 노드를 소스 순서로 돌려줍니다. 깊은 식에서 재귀 한도에 걸리지 않게 스택을 씁니다."""
    stack: list[tuple[Node, tuple[str, ...], Container]] = [(root, (), "module")]
    while stack:
        node, scope, container = stack.pop()
        inner_scope, inner_container = scope, container
        if node.type in (_FUNCTION, _CLASS):
            name_node = node.child_by_field_name("name")
            # 문법 오류로 이름이 없는 정의는 건너뛰고 안쪽만 계속 봅니다.
            if name_node is not None and name_node.text is not None:
                name = name_node.text.decode("utf-8", errors="replace")
                inner_scope = (*scope, name)
                inner_container = "class" if node.type == _CLASS else "function"
                yield node, inner_scope, container
        for child in reversed(node.children):
            stack.append((child, inner_scope, inner_container))


def parse_python(source: bytes) -> Tree:
    return Parser(_PYTHON).parse(source)


def identifier_lines(root: Node) -> Iterator[tuple[str, int]]:
    """정의 이름과 import 문을 뺀 식별자를 소스 순서대로 (이름, 줄 번호)로 돌려줍니다."""
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        if node.type in _IMPORTS:
            continue
        if node.type == "identifier" and node.text is not None:
            parent = node.parent
            is_definition_name = (
                parent is not None
                and parent.type in (_FUNCTION, _CLASS)
                and parent.child_by_field_name("name") == node
            )
            if not is_definition_name:
                yield node.text.decode("utf-8", errors="replace"), node.start_point.row + 1
            continue
        # 소스 순서대로 돌려주도록 뒤에서부터 쌓습니다.
        stack.extend(reversed(node.children))


def _implicitly_called(node: Node, name: str) -> bool:
    parent = node.parent
    decorated = parent is not None and parent.type == _DECORATED
    dunder = name.startswith("__") and name.endswith("__")
    return decorated or dunder


def extract_python(source: bytes, path: str, commit_sha: str) -> ExtractedFile:
    tree = parse_python(source)
    symbols: list[SymbolRef] = []
    implicit: set[str] = set()
    count = 0
    for node, qualified, container in _walk(tree.root_node):
        count += 1
        if container == "function":
            continue
        symbol_id = f"{path}::{'.'.join(qualified)}"
        name = qualified[-1]
        if len(symbol_id) > MAX_SYMBOL_ID_LENGTH or len(name) > MAX_SYMBOL_ID_LENGTH:
            logger.info("symbol_skipped reason=id_too_long path=%s", path)
            continue
        if node.type == _CLASS:
            kind = SymbolKind.CLASS
        elif container == "class":
            kind = SymbolKind.METHOD
        else:
            kind = SymbolKind.FUNCTION
        start, end = _line_range(node)
        if _implicitly_called(node, name):
            implicit.add(symbol_id)
        symbols.append(
            SymbolRef(
                symbol_id=symbol_id,
                name=name,
                kind=kind,
                path=path,
                start_line=start,
                end_line=end,
                commit_sha=commit_sha,
            )
        )
    references: Counter[str] = Counter()
    lines_by_name: dict[str, list[int]] = {}
    own_names = {s.name for s in symbols}
    for name, line in identifier_lines(tree.root_node):
        references[name] += 1
        if name in own_names:
            lines_by_name.setdefault(name, []).append(line)
    self_references = {
        s.symbol_id: sum(
            s.start_line <= line <= s.end_line for line in lines_by_name.get(s.name, [])
        )
        for s in symbols
    }
    return ExtractedFile(
        symbols=symbols,
        definition_count=count,
        references=references,
        self_references=self_references,
        implicitly_called=frozenset(implicit),
    )
