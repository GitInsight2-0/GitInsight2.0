import ast
import textwrap

import pytest

from gitinsight_contracts.common import SymbolKind
from gitinsight_worker.symbols import MAX_SYMBOL_ID_LENGTH, extract_python

SHA = "a" * 40

SOURCE = textwrap.dedent(
    '''\
    import os


    def top(a, b):
        """독스트링."""
        def inner():
            return 1
        return inner() + a + b


    @decorator
    @another(1, 2)
    async def fetch():
        return await call()


    class Service:
        limit = 3

        def method(self):
            return self.limit

        @property
        def value(self):
            return 1

        class Nested:
            def deep(self):
                return 2


    x = lambda: 0
    '''
).encode()


def by_id(source: bytes = SOURCE) -> dict[str, tuple[SymbolKind, int, int]]:
    result = extract_python(source, "app/mod.py", SHA)
    return {s.symbol_id: (s.kind, s.start_line, s.end_line) for s in result.symbols}


def test_symbols_have_kind_and_exact_line_range() -> None:
    symbols = by_id()

    assert symbols["app/mod.py::top"] == (SymbolKind.FUNCTION, 4, 8)
    assert symbols["app/mod.py::fetch"] == (SymbolKind.FUNCTION, 13, 14)
    assert symbols["app/mod.py::Service"] == (SymbolKind.CLASS, 17, 29)
    assert symbols["app/mod.py::Service.method"] == (SymbolKind.METHOD, 20, 21)
    assert symbols["app/mod.py::Service.value"] == (SymbolKind.METHOD, 24, 25)
    assert symbols["app/mod.py::Service.Nested"] == (SymbolKind.CLASS, 27, 29)
    assert symbols["app/mod.py::Service.Nested.deep"] == (SymbolKind.METHOD, 28, 29)


def test_decorator_lines_are_not_part_of_the_range() -> None:
    # 줄 범위는 def 줄에서 시작합니다 (파이썬 ast의 lineno와 같은 기준).
    # 데코레이터는 11~12줄과 23줄에 있습니다.
    assert by_id()["app/mod.py::fetch"][1] == 13
    assert by_id()["app/mod.py::Service.value"][1] == 24


def test_nested_function_is_counted_but_not_emitted() -> None:
    result = extract_python(SOURCE, "app/mod.py", SHA)

    assert "app/mod.py::top.inner" not in {s.symbol_id for s in result.symbols}
    # top, inner, fetch, Service, method, value, Nested, deep
    assert result.definition_count == 8


def test_ranges_match_python_ast() -> None:
    tree = ast.parse(SOURCE)
    expected: dict[str, tuple[int, int]] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            expected[node.name] = (node.lineno, node.end_lineno or node.lineno)
    symbols = by_id()

    for name, (start, end) in expected.items():
        assert symbols[f"app/mod.py::{name}"][1:] == (start, end)


def test_symbol_order_follows_source() -> None:
    result = extract_python(SOURCE, "app/mod.py", SHA)

    assert [s.start_line for s in result.symbols] == sorted(s.start_line for s in result.symbols)


def test_range_without_trailing_newline() -> None:
    source = b"def f():\n    return 1"

    assert by_id(source)["app/mod.py::f"] == (SymbolKind.FUNCTION, 1, 2)


def test_syntax_error_still_returns_valid_symbols() -> None:
    source = b"def ok():\n    return 1\n\ndef broken(:\n    pass\n"

    symbols = by_id(source)

    assert symbols["app/mod.py::ok"] == (SymbolKind.FUNCTION, 1, 2)


def test_empty_and_definition_free_files() -> None:
    assert extract_python(b"", "a.py", SHA).symbols == []
    assert extract_python(b"x = 1\n", "a.py", SHA).definition_count == 0


def test_non_utf8_source_does_not_crash() -> None:
    source = "# 주석\ndef f():\n    return '한'\n".encode("cp949")

    assert "a.py::f" in by_id_for(source, "a.py")


def by_id_for(source: bytes, path: str) -> set[str]:
    return {s.symbol_id for s in extract_python(source, path, SHA).symbols}


def test_symbol_with_too_long_id_is_skipped() -> None:
    path = "d/" * 120 + "m.py"

    result = extract_python(b"def f():\n    pass\n", path, SHA)

    assert len(path) > MAX_SYMBOL_ID_LENGTH
    assert result.symbols == []
    # 건너뛰어도 정의 수에는 셉니다.
    assert result.definition_count == 1


def test_very_deep_expression_does_not_hit_recursion_limit() -> None:
    source = ("x = " + " + ".join(["1"] * 5000) + "\n\ndef f():\n    pass\n").encode()

    assert "app/mod.py::f" in by_id(source)


@pytest.mark.parametrize("bad_sha", ["abc", "G" * 40, ""])
def test_invalid_commit_sha_is_rejected_by_contract(bad_sha: str) -> None:
    with pytest.raises(ValueError, match="commit_sha"):
        extract_python(b"def f():\n    pass\n", "a.py", bad_sha)


# ---- 레포 안 참조 세기 (#58) ----

REFERENCES = textwrap.dedent(
    """\
    from app.other import helper, unused_import


    def walk(n):
        return walk(n - 1) if n else helper()


    class Store:
        def __init__(self):
            self.items = []

        def save(self, item):
            self.items.append(item)

        @route("/x")
        def handler(self):
            return self.save(1)
    """
).encode()


def test_references_skip_definition_names_and_imports() -> None:
    result = extract_python(REFERENCES, "app/mod.py", SHA)

    # import 문 안의 이름은 세지 않고, 실제로 부른 곳만 셉니다.
    assert result.references["helper"] == 1
    assert result.references["unused_import"] == 0
    # 정의 자신의 이름은 세지 않습니다. save는 handler 안에서 한 번 부릅니다.
    assert result.references["save"] == 1
    assert result.references["Store"] == 0
    # 재귀 호출은 자기 범위 안의 참조라 따로 기록해 레포 전체 수에서 뺍니다.
    assert result.references["walk"] == 1
    assert result.self_references["app/mod.py::walk"] == 1
    assert result.self_references["app/mod.py::Store.save"] == 0


def test_decorated_and_dunder_symbols_are_implicitly_called() -> None:
    result = extract_python(REFERENCES, "app/mod.py", SHA)

    assert result.implicitly_called == {
        "app/mod.py::Store.__init__",
        "app/mod.py::Store.handler",
    }
