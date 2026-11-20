"""tree-sitter로 Java 소스에서 클래스와 메서드의 줄 범위를 뽑습니다 (#182).

원칙은 Python(symbols.py), TypeScript·JavaScript(script_symbols.py)와 같습니다.
- 줄은 1부터 셉니다. 어노테이션(`@Service`, `@GetMapping`, `@Override` 등) 줄은 범위에 넣지 않고,
  `public`, `class` 같은 선언 줄부터 시작합니다. 어노테이션은 `modifiers` 노드 안에 있어서 따로 건너뜁니다.
- 타입(class, interface, enum, record) → class. 파일 최상위와 타입 안의 타입(내부 클래스)을 내보냅니다.
  내부 클래스는 `파일::바깥클래스.안쪽클래스`입니다.
- 타입 안의 메서드와 생성자 → method (`파일::클래스.메서드`). 생성자의 이름은 클래스 이름입니다.
- 본문 없는 메서드(추상 메서드, 인터페이스의 선언만 있는 메서드)는 내보내지 않고 세지도 않습니다.
- 메서드 안의 정의(익명 클래스, 지역 클래스, 람다)는 내보내지 않지만 symbol_count에는 셉니다.
- 같은 이름이 한 타입에 다시 나오면(오버로드) 처음 것만 내보냅니다. symbol_id가 같아지기 때문입니다.

이름 참조 세기(#58)와 다른 파일 정의 요약(#66)은 Python 문법 기준이라 여기서는 하지 않습니다. `references`는 비어 있습니다.
"""

import logging
from collections.abc import Iterator

import tree_sitter_java
from tree_sitter import Language, Node, Parser, Tree

from gitinsight_contracts.common import SymbolKind, SymbolRef
from gitinsight_worker.symbols import MAX_SYMBOL_ID_LENGTH, ExtractedFile, line_range

logger = logging.getLogger(__name__)

_JAVA = Language(tree_sitter_java.language())

TYPE_DECLARATIONS = frozenset(
    {
        "class_declaration",
        "interface_declaration",
        "enum_declaration",
        "record_declaration",
        "annotation_type_declaration",
    }
)
METHOD_DECLARATIONS = frozenset({"method_declaration", "constructor_declaration"})
# 타입의 본문 노드입니다. enum은 상수 뒤의 선언이 enum_body_declarations 안에 있습니다.
TYPE_BODIES = frozenset(
    {"class_body", "interface_body", "enum_body", "enum_body_declarations", "annotation_type_body"}
)
_ANNOTATIONS = frozenset({"annotation", "marker_annotation"})
_COMMENTS = frozenset({"line_comment", "block_comment"})


def parse_java(source: bytes) -> Tree:
    return Parser(_JAVA).parse(source)


def start_line(node: Node) -> int:
    """어노테이션과 주석을 뺀 첫 줄입니다. `modifiers` 안의 어노테이션도 건너뜁니다."""
    for child in node.children:
        if child.type in _COMMENTS:
            continue
        if child.type == "modifiers":
            for part in child.children:
                if part.type not in _ANNOTATIONS and part.type not in _COMMENTS:
                    return part.start_point.row + 1
            continue
        return child.start_point.row + 1
    return node.start_point.row + 1


def _name(node: Node) -> str | None:
    name = node.child_by_field_name("name")
    if name is None or name.text is None:
        return None
    return name.text.decode("utf-8", errors="replace")


def _members(body: Node) -> Iterator[Node]:
    """타입 본문의 선언입니다. enum 상수 뒤의 선언 묶음도 펼칩니다."""
    for child in body.children:
        if child.type == "enum_body_declarations":
            yield from child.children
        else:
            yield child


def _walk(root: Node) -> Iterator[tuple[Node, tuple[str, ...], bool]]:
    """타입과 메서드 선언을 소스 순서로 (노드, 이름 경로, 내보낼지)로 돌려줍니다.

    타입 안에 바로 있는 선언만 내보내고, 메서드 안의 정의는 셀 수만 있게 내보내지 않음으로 돌려줍니다.
    """
    stack: list[tuple[Node, tuple[str, ...], bool]] = [(root, (), True)]
    while stack:
        node, scope, exportable = stack.pop()
        children: list[tuple[Node, tuple[str, ...], bool]] = []
        if node.type in TYPE_DECLARATIONS or node.type in METHOD_DECLARATIONS:
            name = _name(node)
            if name is None:
                continue
            path = (*scope, name)
            yield node, path, exportable
            body = node.child_by_field_name("body")
            if body is None:
                continue
            if node.type in TYPE_DECLARATIONS:
                children = [(m, path, exportable) for m in _members(body)]
            else:
                # 메서드 안의 정의(지역 클래스, 익명 클래스의 메서드)는 내보내지 않습니다.
                children = [(c, path, False) for c in body.children]
        else:
            children = [(c, scope, exportable) for c in node.children]
        stack.extend(reversed(children))


def extract_java(source: bytes, path: str, commit_sha: str) -> ExtractedFile:
    tree = parse_java(source)
    symbols: list[SymbolRef] = []
    seen: set[str] = set()
    count = 0
    for node, qualified, exportable in _walk(tree.root_node):
        is_type = node.type in TYPE_DECLARATIONS
        if not is_type and node.child_by_field_name("body") is None:
            # 본문 없는 메서드는 물어볼 동작이 없어 내보내지도 세지도 않습니다.
            continue
        count += 1
        if not exportable or (not is_type and len(qualified) < 2):
            continue
        symbol_id = f"{path}::{'.'.join(qualified)}"
        name = qualified[-1]
        if len(symbol_id) > MAX_SYMBOL_ID_LENGTH or len(name) > MAX_SYMBOL_ID_LENGTH:
            logger.info("symbol_skipped reason=id_too_long path=%s", path)
            continue
        if symbol_id in seen:
            continue
        seen.add(symbol_id)
        _, end = line_range(node)
        start = start_line(node)
        symbols.append(
            SymbolRef(
                symbol_id=symbol_id,
                name=name,
                kind=SymbolKind.CLASS if is_type else SymbolKind.METHOD,
                path=path,
                start_line=start,
                end_line=max(start, end),
                commit_sha=commit_sha,
            )
        )
    return ExtractedFile(symbols=symbols, definition_count=count)
