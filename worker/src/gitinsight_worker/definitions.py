"""주제 코드가 쓰는 다른 파일의 레포 정의를 요약합니다 (#66).

같은 파일의 심볼은 관련 심볼로 이미 LLM에 보이지만, 다른 파일의 정의는 보이지 않습니다.
그래서 주제 코드가 다른 파일 클래스의 속성 이름을 추측해서 찾는 것 같은 연결 결함을 LLM이 짚지 못했습니다.

- 주제 코드 범위의 식별자 중 다른 파일의 최상위 함수·클래스 이름과 같은 것을 고릅니다 (이름 기준이라 틀릴 수 있음).
- 함수의 반환 타입이 다른 파일의 레포 클래스면 그 클래스도 한 단계 따라갑니다 (예: get_vector_service() -> VectorService).
  Optional[...], 문자열 전방 참조 안의 클래스 이름도 찾습니다.
- 요약에는 시그니처, 클래스의 self 속성 이름, 메서드 시그니처만 넣고 본문은 넣지 않습니다. 입력 토큰을 제한하기 위해서입니다.
- 요약은 레포 코드에서 온 값이라 프롬프트에서 untrusted_block 안에만 넣습니다. 저장하지 않습니다.
- Python 문법으로 읽으므로 Python 파일끼리만 연결합니다. TypeScript·JavaScript 주제에는 붙이지 않습니다 (#72).
"""

import logging
import re
from collections.abc import Iterator
from pathlib import Path

from tree_sitter import Node

from gitinsight_contracts.common import SymbolKind, SymbolRef
from gitinsight_worker.candidates import is_inactive_path, is_test_path, shared_dirs
from gitinsight_worker.languages import PYTHON, language_of
from gitinsight_worker.symbols import identifier_lines, parse_python

logger = logging.getLogger(__name__)

# 주제 하나에 붙이는 다른 파일 정의 수와 요약 하나의 길이 상한입니다.
MAX_DEFINITIONS = 3
MAX_SUMMARY_CHARS = 1_200
# 클래스 요약에 넣는 메서드 수 상한입니다.
MAX_METHODS = 15

_SPACES = re.compile(r"\s+")
_OPEN_PAREN = re.compile(r"\(\s+")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CLOSE_PAREN = re.compile(r",?\s+\)|,\)")


def top_level_index(symbols: list[SymbolRef]) -> dict[str, list[SymbolRef]]:
    """이름별 Python 최상위 함수·클래스입니다. 테스트와 지금 쓰지 않는 경로(legacy 등)는 뺍니다."""
    index: dict[str, list[SymbolRef]] = {}
    shared = shared_dirs(s.path for s in symbols)
    for symbol in symbols:
        top_level = "." not in symbol.symbol_id.split("::", 1)[1]
        if (
            top_level
            and language_of(symbol.path) == PYTHON
            and symbol.kind in (SymbolKind.FUNCTION, SymbolKind.CLASS)
            and not is_test_path(symbol.path)
            and not is_inactive_path(symbol.path, shared)
        ):
            index.setdefault(symbol.name, []).append(symbol)
    return index


def _unique(index: dict[str, list[SymbolRef]], name: str, exclude_path: str) -> SymbolRef | None:
    # 같은 이름이 다른 파일 여러 곳에 있으면 어느 것인지 몰라 붙이지 않습니다.
    found = [s for s in index.get(name, []) if s.path != exclude_path]
    return found[0] if len(found) == 1 else None


def _definition_node(root: Node, symbol: SymbolRef) -> Node | None:
    wanted = "class_definition" if symbol.kind is SymbolKind.CLASS else "function_definition"
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == wanted and node.start_point.row + 1 == symbol.start_line:
            return node
        stack.extend(node.children)
    return None


def _text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


def _header(node: Node) -> str:
    """정의 머리(def·class 줄)를 한 줄로 만듭니다. 본문은 넣지 않습니다."""
    body = node.child_by_field_name("body")
    end = body.start_byte if body is not None else node.end_byte
    source = node.text or b""
    header = source[: end - node.start_byte].decode("utf-8", errors="replace")
    header = _SPACES.sub(" ", header).strip().rstrip(":").strip()
    # 여러 줄 시그니처를 한 줄로 합칠 때 생긴 "( self", ", )"를 정리합니다.
    return _CLOSE_PAREN.sub(")", _OPEN_PAREN.sub("(", header))


def _self_attributes(class_node: Node) -> list[str]:
    """클래스 안에서 self.<이름> = ... 으로 만드는 속성과 클래스 수준 변수 이름입니다."""
    names: list[str] = []
    body = class_node.child_by_field_name("body")
    for child in body.children if body is not None else []:
        if child.type == "expression_statement" and child.children:
            target = child.children[0].child_by_field_name("left")
            if target is not None and target.type == "identifier":
                names.append(_text(target))
    stack = [class_node]
    while stack:
        node = stack.pop()
        if node.type in ("assignment", "augmented_assignment"):
            left = node.child_by_field_name("left")
            obj = left.child_by_field_name("object") if left is not None else None
            if left is not None and left.type == "attribute" and _text(obj) == "self":
                names.append(_text(left.child_by_field_name("attribute")))
        # 안쪽 클래스의 self는 다른 객체입니다.
        stack.extend(c for c in node.children if c.type != "class_definition")
    return list(dict.fromkeys(n for n in names if n))


def _methods(class_node: Node) -> Iterator[Node]:
    body = class_node.child_by_field_name("body")
    for child in body.children if body is not None else []:
        if child.type == "decorated_definition":
            child = child.child_by_field_name("definition") or child
        if child.type == "function_definition":
            yield child


def summarize(source: bytes, symbol: SymbolRef) -> str | None:
    """정의 하나의 요약입니다. 찾지 못하면 None입니다."""
    node = _definition_node(parse_python(source).root_node, symbol)
    if node is None:
        return None
    lines = [f"# {symbol.path}", _header(node) + ":"]
    if symbol.kind is SymbolKind.CLASS:
        attributes = _self_attributes(node)
        lines.append(f"    # 속성: {', '.join(attributes) if attributes else '(없음)'}")
        methods = list(_methods(node))
        lines += [f"    {_header(m)}: ..." for m in methods[:MAX_METHODS]]
        if len(methods) > MAX_METHODS:
            lines.append(f"    # 메서드 {len(methods) - MAX_METHODS}개 생략")
    else:
        lines[-1] += " ..."
    text = "\n".join(lines)
    return text if len(text) <= MAX_SUMMARY_CHARS else text[:MAX_SUMMARY_CHARS] + "\n# ... (생략)"


def _return_type_names(source: bytes, symbol: SymbolRef) -> list[str]:
    """함수의 반환 타입 표기에 나오는 이름들입니다 (예: -> Optional["VectorService"]).

    문자열로 쓴 전방 참조도 읽도록 표기 글자에서 이름 모양을 모두 뽑습니다.
    Optional, list 같은 이름도 섞여 나오므로, 레포 클래스인지는 부르는 쪽이 색인으로 가립니다.
    """
    node = _definition_node(parse_python(source).root_node, symbol)
    annotation = node.child_by_field_name("return_type") if node is not None else None
    return _NAME.findall(_text(annotation))


def _return_class(
    root: Path, function: SymbolRef, index: dict[str, list[SymbolRef]], target_path: str
) -> SymbolRef | None:
    """함수의 반환 타입 중 다른 파일의 레포 클래스인 첫 번째입니다.

    주제 코드와 같은 파일의 클래스는 관련 심볼로 이미 보이므로 붙이지 않습니다.
    """
    for name in _return_type_names((root / function.path).read_bytes(), function):
        symbol = _unique(index, name, target_path)
        if symbol is not None and symbol.kind is SymbolKind.CLASS:
            return symbol
    return None


def external_definitions(
    root: Path, target: SymbolRef, index: dict[str, list[SymbolRef]]
) -> list[SymbolRef]:
    """주제 코드가 쓰는 다른 파일의 정의를 쓰는 순서대로 최대 MAX_DEFINITIONS개 돌려줍니다."""
    source = (root / target.path).read_bytes()
    used = [
        name
        for name, line in identifier_lines(parse_python(source).root_node)
        if target.start_line <= line <= target.end_line
    ]
    found: list[SymbolRef] = []
    for name in dict.fromkeys(used):
        symbol = _unique(index, name, target.path)
        if symbol is None or symbol in found:
            continue
        found.append(symbol)
        if symbol.kind is SymbolKind.FUNCTION:
            # 반환 타입이 레포 클래스면 그 클래스의 속성과 메서드도 봐야 연결을 판단할 수 있습니다.
            cls = _return_class(root, symbol, index, target.path)
            if cls is not None and cls not in found:
                found.append(cls)
    return found[:MAX_DEFINITIONS]


def external_definitions_text(
    root: Path, target: SymbolRef, index: dict[str, list[SymbolRef]]
) -> str:
    """프롬프트에 넣을 요약 묶음입니다. 붙일 정의가 없으면 빈 문자열입니다."""
    if language_of(target.path) != PYTHON:
        return ""
    summaries: list[str] = []
    try:
        for symbol in external_definitions(root, target, index):
            summary = summarize((root / symbol.path).read_bytes(), symbol)
            if summary is not None:
                summaries.append(summary)
    except OSError as error:
        logger.info("external_definitions_failed error=%s", type(error).__name__)
        return ""
    return "\n\n".join(summaries)
