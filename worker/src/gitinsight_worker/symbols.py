"""tree-sitter로 Python 소스에서 함수, 메서드, 클래스의 줄 범위를 뽑습니다.

줄 번호는 이 모듈의 결과에서만 나옵니다. LLM은 여기서 만든 symbol_id 목록 중에서 고르기만 합니다.
- 줄은 1부터 셉니다. 데코레이터 줄은 범위에 넣지 않고 `def`/`class` 줄부터 시작합니다.
- 모듈 최상위와 클래스 안의 정의만 SymbolRef로 내보냅니다.
  함수 안에 중첩된 정의는 면접 질문 재료로 쓰지 않지만 symbol_count에는 셉니다.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

import tree_sitter_python
from tree_sitter import Language, Node, Parser

from gitinsight_contracts.common import SymbolKind, SymbolRef

logger = logging.getLogger(__name__)

# SymbolRef.symbol_id의 최대 길이입니다. 넘으면 계약 검증에 실패하므로 그 심볼은 건너뜁니다.
MAX_SYMBOL_ID_LENGTH = 200

_PYTHON = Language(tree_sitter_python.language())
_FUNCTION = "function_definition"
_CLASS = "class_definition"

Container = Literal["module", "class", "function"]


@dataclass(frozen=True)
class ExtractedFile:
    symbols: list[SymbolRef]
    # 함수 안에 중첩된 정의까지 센 정의 수입니다.
    definition_count: int


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


def extract_python(source: bytes, path: str, commit_sha: str) -> ExtractedFile:
    tree = Parser(_PYTHON).parse(source)
    symbols: list[SymbolRef] = []
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
    return ExtractedFile(symbols=symbols, definition_count=count)
