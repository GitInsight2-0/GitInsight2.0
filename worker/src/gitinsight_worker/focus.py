"""주제 심볼 안의 좁은 강조 범위 후보를 tree-sitter로 뽑습니다 (#156).

면접 화면은 지금 주제 심볼 전체를 강조합니다. 긴 함수는 질문과 상관없이 100줄 넘게 칠해집니다.
2단계에서 요점마다 이 후보 중 하나를 고르게 하면, 확인 질문과 힌트에서 그 범위만 강조할 수 있습니다.
줄 번호는 여기서만 나옵니다. LLM은 후보 id만 고릅니다.

후보는 아래처럼 만듭니다. 모두 주제 심볼 범위 안에 있습니다.
- 함수: 본문의 최상위 문장을 블록으로 나눕니다.
  if, for, while, try, with, switch, 중첩 함수·클래스, 안쪽 화살표 함수(`const onClick = () => {...}`),
  여러 줄에 걸친 콜백 호출(`useEffect(() => {...})`)은 각각 하나의 블록입니다.
  그 사이의 짧은 문장들(변수 대입, 호출, return 등)은 이어진 만큼 하나의 블록으로 묶습니다.
- 클래스: 메서드(함수를 담은 필드 포함) 하나가 블록 하나입니다.
- Java(#182)도 같습니다. 메서드는 if, for, try, switch, 여러 줄 람다·익명 클래스가 블록이고, 클래스는 메서드와 생성자가 블록입니다.
- 너무 짧은 블록(MIN_BLOCK_LINES 미만)은 바로 앞 블록과 합칩니다. 맨 앞이면 다음 블록과 합칩니다.
- 블록이 MAX_CANDIDATES를 넘으면 이웃끼리 합친 길이가 가장 짧은 둘부터 합칩니다.
- 블록 하나가 주제 심볼 거의 전체(WHOLE_RATIO 이상)를 차지하면(큰 try, 함수를 감싼 중첩 함수 등) 그 안으로 들어가
  다시 나눕니다. 최대 MAX_DEPTH 단계까지입니다.
- 주제 심볼이 짧거나(MIN_SYMBOL_LINES 미만), 블록 하나가 거의 전체(WHOLE_RATIO 이상)면 좁힐 의미가 없어 후보를 내지 않습니다.
- 문서 문자열과 주석만 있는 줄은 블록에 넣지 않습니다.

원본 코드는 메모리에서만 읽고, 후보에는 위치와 첫 줄 요약만 남깁니다.
"""

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from tree_sitter import Node

from gitinsight_contracts.common import SymbolKind, SymbolRef
from gitinsight_worker.java_symbols import (
    METHOD_DECLARATIONS,
    TYPE_BODIES,
    TYPE_DECLARATIONS,
    parse_java,
)
from gitinsight_worker.java_symbols import start_line as java_start_line
from gitinsight_worker.languages import JAVA, PYTHON, language_of
from gitinsight_worker.script_symbols import parse_script
from gitinsight_worker.symbols import line_range, parse_python

logger = logging.getLogger(__name__)

# 이보다 짧은 주제 심볼은 전체를 강조해도 읽기 어렵지 않습니다.
MIN_SYMBOL_LINES = 15
# 이보다 짧은 블록은 이웃과 합칩니다. 한두 줄짜리 블록은 질문이 가리키기에 너무 좁습니다.
MIN_BLOCK_LINES = 3
# 프롬프트 길이를 제한하고 LLM이 고르기 쉽게 후보 수를 제한합니다.
MAX_CANDIDATES = 12
# 블록 하나가 주제 심볼의 이 비율 이상이면 좁히는 의미가 없습니다.
WHOLE_RATIO = 0.8
# 프롬프트에 보여 줄 블록 첫 줄의 최대 길이입니다.
MAX_HEAD_CHARS = 80

_PY_COMPOUND = frozenset(
    {
        "if_statement",
        "for_statement",
        "while_statement",
        "try_statement",
        "with_statement",
        "match_statement",
        "function_definition",
        "class_definition",
        "decorated_definition",
    }
)
_SCRIPT_COMPOUND = frozenset(
    {
        "if_statement",
        "for_statement",
        "for_in_statement",
        "while_statement",
        "do_statement",
        "try_statement",
        "switch_statement",
        "function_declaration",
        "generator_function_declaration",
        "class_declaration",
    }
)
_SCRIPT_FUNCTION_VALUES = frozenset({"arrow_function", "function_expression", "generator_function"})
_COMMENTS = frozenset({"comment", "line_comment", "block_comment"})
# 문장을 담는 본문 노드입니다. 큰 블록 안으로 들어갈 때 이 안의 문장을 다시 나눕니다.
_CONTAINERS = frozenset({"block", "statement_block", "constructor_body"})
# Java에서 혼자 블록이 되는 문장입니다 (#182). 람다와 익명 클래스를 담은 문장은 _java_compound가 따로 봅니다.
_JAVA_COMPOUND = frozenset(
    {
        "if_statement",
        "for_statement",
        "enhanced_for_statement",
        "while_statement",
        "do_statement",
        "try_statement",
        "try_with_resources_statement",
        "switch_expression",
        "synchronized_statement",
        "class_declaration",
        "record_declaration",
    }
)
_JAVA_BLOCK_VALUES = frozenset({"lambda_expression", "class_body"})
# 큰 블록 안으로 들어가는 깊이 상한입니다.
MAX_DEPTH = 3


@dataclass(frozen=True)
class FocusCandidate:
    """주제 심볼 안의 강조 범위 후보입니다. focus_id는 주제 안에서만 유일합니다(b1, b2, ...)."""

    focus_id: str
    start_line: int
    end_line: int
    # 프롬프트에서 후보를 알아보게 하는 첫 줄입니다. 레포 코드라 untrusted_block 안에만 넣습니다.
    head: str


@dataclass
class _Block:
    start: int
    end: int
    # 혼자서 의미가 있는 블록(if, 중첩 함수, 메서드 등)인지입니다. 짧은 문장 묶음은 False입니다.
    compound: bool
    # 블록이 문장 하나일 때 그 노드입니다. 블록이 너무 크면 이 안으로 들어가 다시 나눕니다.
    node: Node | None = None

    @property
    def lines(self) -> int:
        return self.end - self.start + 1


# ---- 주제 심볼 노드 찾기 ----


def _walk(root: Node) -> Iterator[Node]:
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def _python_node(root: Node, symbol: SymbolRef) -> Node | None:
    wanted = "class_definition" if symbol.kind is SymbolKind.CLASS else "function_definition"
    for node in _walk(root):
        if node.type == wanted and line_range(node) == (symbol.start_line, symbol.end_line):
            return node
    return None


def _script_body(root: Node, symbol: SymbolRef) -> Node | None:
    """주제 심볼의 함수 본문(statement_block)이나 클래스 본문(class_body)입니다."""
    for node in _walk(root):
        _, end = line_range(node)
        if end != symbol.end_line or node.start_point.row + 1 > symbol.start_line:
            continue
        # 데코레이터 때문에 노드가 위에서 시작할 수 있어, 끝 줄이 같은 정의 노드 안에서 본문을 찾습니다.
        for child in _walk(node):
            if (
                child.type in ("statement_block", "class_body")
                and line_range(child)[1] == end
                and child.start_point.row + 1 >= symbol.start_line
            ):
                return child
    return None


# ---- 블록 나누기 ----


def _is_docstring(node: Node) -> bool:
    return (
        node.type == "expression_statement"
        and bool(node.named_children)
        and (node.named_children[0].type == "string")
    )


def _python_compound(node: Node) -> bool:
    return node.type in _PY_COMPOUND


def _java_compound(node: Node) -> bool:
    if node.type in _JAVA_COMPOUND:
        return True
    if node.type in ("expression_statement", "local_variable_declaration", "return_statement"):
        # 여러 줄에 걸친 람다나 익명 클래스: list.forEach(x -> {...}), new Runnable() {...}
        start, end = line_range(node)
        return end > start and any(c.type in _JAVA_BLOCK_VALUES for c in _walk(node))
    return False


def _java_node(root: Node, symbol: SymbolRef) -> Node | None:
    """주제 심볼의 타입·메서드 선언입니다. 시작 줄은 어노테이션을 뺀 줄로 맞춥니다."""
    for node in _walk(root):
        if (node.type in TYPE_DECLARATIONS or node.type in METHOD_DECLARATIONS) and (
            java_start_line(node),
            line_range(node)[1],
        ) == (symbol.start_line, symbol.end_line):
            return node
    return None


def _java_blocks(node: Node) -> list[_Block]:
    body = node.child_by_field_name("body")
    if body is None:
        return []
    if body.type in TYPE_BODIES:
        methods: list[_Block] = []
        members = [
            m
            for child in body.children
            for m in (child.children if child.type == "enum_body_declarations" else [child])
        ]
        for member in members:
            if member.type in METHOD_DECLARATIONS and member.child_by_field_name("body"):
                methods.append(
                    _Block(
                        java_start_line(member), line_range(member)[1], compound=True, node=member
                    )
                )
        return methods
    return _statement_blocks(list(body.named_children), _java_compound)


def _script_compound(node: Node) -> bool:
    if node.type in _SCRIPT_COMPOUND:
        return True
    if node.type in ("lexical_declaration", "variable_declaration"):
        # 안쪽 함수: const onClick = () => {...}
        return any(
            (value := d.child_by_field_name("value")) is not None
            and value.type in _SCRIPT_FUNCTION_VALUES
            for d in node.named_children
            if d.type == "variable_declarator"
        )
    if node.type == "expression_statement":
        # 여러 줄에 걸친 콜백 호출: useEffect(() => {...}, [])
        start, end = line_range(node)
        return end > start and any(c.type in _SCRIPT_FUNCTION_VALUES for c in _walk(node))
    return False


def _statement_blocks(statements: list[Node], compound_of: Callable[[Node], bool]) -> list[_Block]:
    blocks: list[_Block] = []
    for node in statements:
        if node.type in _COMMENTS:
            continue
        start, end = line_range(node)
        if compound_of(node):
            blocks.append(_Block(start, end, compound=True, node=node))
        elif blocks and not blocks[-1].compound:
            blocks[-1].end = end
        else:
            blocks.append(_Block(start, end, compound=False))
    return blocks


def _python_blocks(node: Node) -> list[_Block]:
    body = node.child_by_field_name("body")
    if body is None:
        return []
    statements = list(body.named_children)
    if statements and _is_docstring(statements[0]):
        statements = statements[1:]
    if node.type == "class_definition":
        methods: list[_Block] = []
        for s in statements:
            inner = s.child_by_field_name("definition") if s.type == "decorated_definition" else s
            if inner is not None and inner.type == "function_definition":
                start, end = line_range(inner)
                methods.append(_Block(start, end, compound=True, node=inner))
        return methods
    return _statement_blocks(statements, _python_compound)


def _script_blocks(body: Node) -> list[_Block]:
    if body.type == "class_body":
        methods: list[_Block] = []
        for member in body.named_children:
            value = member.child_by_field_name("value")
            is_function_field = value is not None and value.type in _SCRIPT_FUNCTION_VALUES
            if member.type == "method_definition" or is_function_field:
                start, end = line_range(member)
                methods.append(_Block(start, end, compound=True, node=member))
        return methods
    return _statement_blocks(list(body.named_children), _script_compound)


def _inner_statements(node: Node) -> list[Node]:
    """블록 노드 안의 문장들입니다. 가장 바깥 본문(Python block, TS statement_block)들의 문장을 순서대로 모읍니다.

    if는 elif·else 본문까지, try는 except·finally 본문까지 이어서 모읍니다.
    """
    found: list[Node] = []
    stack = list(reversed(node.children))
    while stack:
        current = stack.pop()
        if current.type in _CONTAINERS:
            statements = list(current.named_children)
            if statements and _is_docstring(statements[0]):
                statements = statements[1:]
            found.extend(statements)
            continue
        stack.extend(reversed(current.children))
    return found


def _split_dominant(
    blocks: list[_Block], total: int, compound_of: Callable[[Node], bool], depth: int = 0
) -> list[_Block]:
    """주제 심볼 거의 전체를 차지하는 블록(큰 try, 중첩 함수 등)은 안으로 들어가 다시 나눕니다."""
    out: list[_Block] = []
    for block in blocks:
        dominant = block.node is not None and block.lines >= total * WHOLE_RATIO
        if dominant and depth < MAX_DEPTH and block.node is not None:
            inner = _statement_blocks(_inner_statements(block.node), compound_of)
            if inner:
                out.extend(_split_dominant(inner, total, compound_of, depth + 1))
                continue
        out.append(block)
    return out


def _merge_short(blocks: list[_Block]) -> list[_Block]:
    merged: list[_Block] = []
    for block in blocks:
        if merged and (block.lines < MIN_BLOCK_LINES or merged[-1].lines < MIN_BLOCK_LINES):
            last = merged[-1]
            merged[-1] = _Block(last.start, block.end, compound=last.compound and block.compound)
        else:
            merged.append(block)
    return merged


def _merge_to_limit(blocks: list[_Block]) -> list[_Block]:
    blocks = list(blocks)
    while len(blocks) > MAX_CANDIDATES:
        i = min(range(len(blocks) - 1), key=lambda k: blocks[k + 1].end - blocks[k].start)
        blocks[i : i + 2] = [_Block(blocks[i].start, blocks[i + 1].end, compound=False)]
    return blocks


def _head(lines: list[str], block: _Block) -> str:
    for line in lines[block.start - 1 : block.end]:
        text = line.strip()
        if text and not text.startswith(("#", "//", "/*", "*")):
            return text[:MAX_HEAD_CHARS]
    return ""


def focus_candidates(source: bytes, symbol: SymbolRef) -> list[FocusCandidate]:
    """주제 심볼 안의 강조 범위 후보입니다. 좁힐 의미가 없거나 심볼을 찾지 못하면 빈 목록입니다."""
    total = symbol.end_line - symbol.start_line + 1
    if total < MIN_SYMBOL_LINES:
        return []
    language = language_of(symbol.path)
    if language == PYTHON:
        node = _python_node(parse_python(source).root_node, symbol)
        blocks = _python_blocks(node) if node is not None else []
        compound_of = _python_compound
    elif language == JAVA:
        node = _java_node(parse_java(source).root_node, symbol)
        blocks = _java_blocks(node) if node is not None else []
        compound_of = _java_compound
    else:
        body = _script_body(parse_script(source, symbol.path).root_node, symbol)
        blocks = _script_blocks(body) if body is not None else []
        compound_of = _script_compound
    blocks = _split_dominant(blocks, total, compound_of)
    blocks = [
        b
        for b in _merge_to_limit(_merge_short(blocks))
        if symbol.start_line <= b.start <= b.end <= symbol.end_line
        and b.lines < total * WHOLE_RATIO
    ]
    if len(blocks) < 2:
        # 후보가 하나뿐이면 주제 심볼 전체를 강조하는 것과 큰 차이가 없습니다.
        return []
    lines = source.decode("utf-8", errors="replace").splitlines()
    return [
        FocusCandidate(f"b{i}", b.start, b.end, _head(lines, b)) for i, b in enumerate(blocks, 1)
    ]
