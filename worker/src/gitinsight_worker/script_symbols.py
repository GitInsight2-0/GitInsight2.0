"""tree-sitter로 TypeScript·JavaScript 소스에서 함수, 메서드, 클래스의 줄 범위를 뽑습니다 (#72).

원칙은 Python(symbols.py)과 같습니다.
- 줄은 1부터 셉니다. 데코레이터 줄은 범위에 넣지 않습니다.
- 모듈 최상위와 클래스 안의 정의를 SymbolRef로 내보냅니다.
- 최상위 함수 바로 안의 이름 있는 함수도 내보냅니다 (`파일::바깥함수.안쪽함수`, function).
  React 컴포넌트는 동작이 안쪽 함수(이벤트 처리, 데이터 불러오기)에 있어서, 이것이 없으면 컴포넌트 주제에
  꼬리질문으로 가리킬 관련 심볼이 하나도 없습니다. `useEffect(() => { ... })` 같은 이름 없는 콜백 안의 함수도 포함합니다.
- 그보다 깊이 중첩된 정의, 메서드 안의 정의, 함수 안의 클래스는 내보내지 않지만 symbol_count에는 셉니다.

정의로 보는 것은 아래와 같습니다. SymbolKind는 Python과 같은 세 가지만 씁니다.
- 함수 선언(`function f() {}`, 제너레이터 포함) → function
- 변수에 담은 화살표 함수와 함수 식(`const f = () => {}`) → function. 범위는 변수 이름부터 함수 끝까지입니다.
- CommonJS 내보내기(`exports.f = function () {}`, `module.exports.f = () => {}`) → function
- 클래스 선언, 추상 클래스, 변수에 담은 클래스 식 → class
- 클래스 안의 메서드와 함수를 담은 필드(`handle = () => {}`) → method

`export`, `export default`는 같은 선언으로 보고 따로 구분하지 않습니다.
아래는 내보내지 않습니다.
- 이름 없는 정의(`export default function () {}`), 계산된 이름(`[Symbol.iterator]() {}`)
- 본문 없는 선언(오버로드 시그니처, `declare function`, 추상 메서드), 인터페이스, 타입, enum
- 객체 리터럴 안의 메서드, 다른 함수 호출로 감싼 함수(`memo(() => {})`)
- 같은 이름이 한 파일에 다시 나오면(getter와 setter 등) 처음 것만 내보냅니다.

`.tsx`, `.jsx`의 컴포넌트도 함수나 클래스로 똑같이 뽑습니다.
이름 참조 세기(#58)는 Python 문법 기준이라 여기서는 하지 않습니다. `references`는 비어 있습니다.
"""

import logging
from collections.abc import Iterator
from pathlib import PurePosixPath

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from gitinsight_contracts.common import SymbolKind, SymbolRef
from gitinsight_worker.symbols import MAX_SYMBOL_ID_LENGTH, Container, ExtractedFile, line_range

logger = logging.getLogger(__name__)

_JAVASCRIPT = Language(tree_sitter_javascript.language())
_TYPESCRIPT = Language(tree_sitter_typescript.language_typescript())
_TSX = Language(tree_sitter_typescript.language_tsx())
# JavaScript 문법은 JSX를 함께 읽습니다. TypeScript는 JSX가 있는 파일(.tsx)의 문법이 따로 있습니다.
_LANGUAGE_BY_SUFFIX: dict[str, Language] = {
    ".js": _JAVASCRIPT,
    ".jsx": _JAVASCRIPT,
    ".mjs": _JAVASCRIPT,
    ".cjs": _JAVASCRIPT,
    ".ts": _TYPESCRIPT,
    ".mts": _TYPESCRIPT,
    ".cts": _TYPESCRIPT,
    ".tsx": _TSX,
}

_FUNCTION_DECLARATIONS = frozenset({"function_declaration", "generator_function_declaration"})
_CLASS_DECLARATIONS = frozenset({"class_declaration", "abstract_class_declaration"})
_FUNCTION_VALUES = frozenset({"arrow_function", "function_expression", "generator_function"})
_CLASS_VALUE = "class"
# 클래스 필드입니다. TypeScript 문법과 JavaScript 문법의 노드 이름이 다릅니다.
_FIELDS = frozenset({"public_field_definition", "field_definition"})
_METHOD = "method_definition"
_METHOD_NAMES = frozenset({"property_identifier", "private_property_identifier"})
# 이 안의 정의는 함수 안에 중첩된 것으로 봅니다.
_FUNCTION_SCOPES = _FUNCTION_VALUES | {_METHOD, "class_static_block"}

type Definition = tuple[str, bool, Node | None]


def _text(node: Node | None) -> str | None:
    if node is None or node.text is None:
        return None
    return node.text.decode("utf-8", errors="replace")


def _commonjs_export_name(left: Node | None) -> str | None:
    """`exports.이름`이나 `module.exports.이름`이면 그 이름입니다."""
    if left is None or left.type != "member_expression":
        return None
    target = _text(left.child_by_field_name("object"))
    prop = left.child_by_field_name("property")
    if target not in ("exports", "module.exports") or prop is None:
        return None
    return _text(prop) if prop.type == "property_identifier" else None


def _definition(node: Node, container: Container) -> Definition | None:
    """정의 노드면 (이름, 클래스인지, 이름을 받은 값 노드)를 돌려줍니다."""
    kind = node.type
    if kind in _FUNCTION_DECLARATIONS or kind in _CLASS_DECLARATIONS:
        name = _text(node.child_by_field_name("name"))
        return None if name is None else (name, kind in _CLASS_DECLARATIONS, None)
    if kind == _METHOD:
        name_node = node.child_by_field_name("name")
        in_class = node.parent is not None and node.parent.type == "class_body"
        if not in_class or name_node is None or name_node.type not in _METHOD_NAMES:
            return None
        name = _text(name_node)
        return None if name is None else (name, False, None)
    if kind in _FIELDS:
        name_node = node.child_by_field_name("name") or node.child_by_field_name("property")
        value = node.child_by_field_name("value")
        if name_node is None or name_node.type not in _METHOD_NAMES:
            return None
        if value is None or value.type not in _FUNCTION_VALUES:
            return None
        name = _text(name_node)
        return None if name is None else (name, False, value)
    if kind == "variable_declarator":
        name_node = node.child_by_field_name("name")
        value = node.child_by_field_name("value")
        if name_node is None or name_node.type != "identifier" or value is None:
            return None
        if value.type not in _FUNCTION_VALUES and value.type != _CLASS_VALUE:
            return None
        name = _text(name_node)
        return None if name is None else (name, value.type == _CLASS_VALUE, value)
    if kind == "assignment_expression" and container == "module":
        value = node.child_by_field_name("right")
        name = _commonjs_export_name(node.child_by_field_name("left"))
        if name is None or value is None:
            return None
        if value.type not in _FUNCTION_VALUES and value.type != _CLASS_VALUE:
            return None
        return name, value.type == _CLASS_VALUE, value
    return None


def _walk(root: Node) -> Iterator[tuple[Node, tuple[str, ...], Container, bool]]:
    """정의 노드를 소스 순서로 (노드, 이름 경로, 정의가 놓인 곳, 클래스인지)로 돌려줍니다."""
    stack: list[tuple[Node, tuple[str, ...], Container]] = [(root, (), "module")]
    # 변수나 내보내기 이름을 받은 클래스 식입니다. 이름 없는 클래스 식과 구분합니다.
    named_classes: set[int] = set()
    while stack:
        node, scope, container = stack.pop()
        inner_scope, inner_container = scope, container
        found = _definition(node, container)
        if found is not None:
            name, is_class, value = found
            inner_scope = (*scope, name)
            # 함수 안의 클래스는 내보내지 않으므로 그 메서드도 중첩 정의로 봅니다.
            inner_container = "class" if is_class and container != "function" else "function"
            if is_class and value is not None:
                named_classes.add(value.id)
            yield node, inner_scope, container, is_class
        elif node.type in _FUNCTION_SCOPES or (
            node.type == _CLASS_VALUE and node.id not in named_classes
        ):
            # 이름 없는 함수와 클래스 안의 정의는 가리킬 이름이 없어 내보내지 않습니다.
            inner_container = "function"
        for child in reversed(node.children):
            stack.append((child, inner_scope, inner_container))


def _start_line(node: Node) -> int:
    """데코레이터와 그 사이 주석을 뺀 첫 줄입니다."""
    for child in node.children:
        if child.type not in ("decorator", "comment"):
            return child.start_point.row + 1
    return node.start_point.row + 1


def extract_script(source: bytes, path: str, commit_sha: str) -> ExtractedFile:
    """확장자에 맞는 문법으로 읽습니다. 모르는 확장자는 JavaScript로 읽습니다."""
    language = _LANGUAGE_BY_SUFFIX.get(PurePosixPath(path).suffix.lower(), _JAVASCRIPT)
    tree = Parser(language).parse(source)
    symbols: list[SymbolRef] = []
    seen: set[str] = set()
    # 내보낸 최상위 함수 이름입니다. 이 함수 바로 안의 함수만 중첩 정의 중에서 내보냅니다.
    top_functions: set[str] = set()
    count = 0
    for node, qualified, container, is_class in _walk(tree.root_node):
        count += 1
        if container == "function":
            inside_top_function = len(qualified) == 2 and qualified[0] in top_functions
            if is_class or not inside_top_function:
                continue
        symbol_id = f"{path}::{'.'.join(qualified)}"
        name = qualified[-1]
        if len(symbol_id) > MAX_SYMBOL_ID_LENGTH or len(name) > MAX_SYMBOL_ID_LENGTH:
            logger.info("symbol_skipped reason=id_too_long path=%s", path)
            continue
        if symbol_id in seen:
            continue
        seen.add(symbol_id)
        if is_class:
            kind = SymbolKind.CLASS
        elif container == "class":
            kind = SymbolKind.METHOD
        else:
            kind = SymbolKind.FUNCTION
            if container == "module":
                top_functions.add(name)
        _, end = line_range(node)
        start = _start_line(node)
        symbols.append(
            SymbolRef(
                symbol_id=symbol_id,
                name=name,
                kind=kind,
                path=path,
                start_line=start,
                end_line=max(start, end),
                commit_sha=commit_sha,
            )
        )
    return ExtractedFile(symbols=symbols, definition_count=count)
