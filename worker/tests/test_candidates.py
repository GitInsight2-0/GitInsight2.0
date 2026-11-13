from gitinsight_contracts.analysis import ContributionLabel
from gitinsight_contracts.common import SymbolKind, SymbolRef
from gitinsight_worker.candidates import (
    MAX_PER_FILE,
    MIN_LINES,
    is_inactive_path,
    is_test_path,
    select_candidates,
)

SHA = "c" * 40


def sym(path: str, name: str, start: int, end: int, kind: SymbolKind = SymbolKind.FUNCTION):
    return SymbolRef(
        symbol_id=f"{path}::{name}",
        name=name.split(".")[-1],
        kind=kind,
        path=path,
        start_line=start,
        end_line=end,
        commit_sha=SHA,
    )


def ids(symbols: list[SymbolRef]) -> list[str]:
    return [c.symbol.symbol_id for c in select_candidates(symbols)]


def test_short_test_and_inactive_code_is_skipped() -> None:
    symbols = [
        sym("app/a.py", "short", 1, MIN_LINES - 1),
        sym("tests/test_a.py", "test_big", 1, 50),
        sym("app/_legacy_feedback.py", "old_big", 1, 80),
        sym("app/a.py", "ok", 10, 10 + MIN_LINES - 1),
    ]

    assert ids(symbols) == ["app/a.py::ok"]


def test_paths() -> None:
    assert is_test_path("pkg/tests/util.py")
    assert is_test_path("conftest.py")
    assert is_test_path("app/foo_test.py")
    assert not is_test_path("app/contest.py")
    assert is_inactive_path("db/migrations/0001_init.py")
    assert is_inactive_path("examples/demo.py")
    assert is_inactive_path("old/x.py")
    assert not is_inactive_path("app/models.py")


def test_bigger_symbols_first_and_per_file_cap() -> None:
    symbols = [
        sym("app/a.py", "small", 1, 10),
        sym("app/a.py", "big", 20, 120),
        sym("app/a.py", "mid", 130, 180),
        sym("app/b.py", "other", 1, 30),
    ]

    # a.py는 큰 순서로 최대 MAX_PER_FILE개만 고릅니다.
    assert MAX_PER_FILE == 2
    assert ids(symbols) == ["app/a.py::big", "app/a.py::mid", "app/b.py::other"]


def test_class_and_its_methods_are_not_both_topics() -> None:
    cls = sym("app/s.py", "Service", 1, 60, SymbolKind.CLASS)
    method = sym("app/s.py", "Service.run", 10, 40, SymbolKind.METHOD)
    helper = sym("app/s.py", "helper", 70, 75)

    candidates = select_candidates([cls, method, helper])

    assert [c.symbol.symbol_id for c in candidates] == ["app/s.py::Service"]
    # 클래스 주제의 메서드와 같은 파일의 다른 함수는 관련 심볼로 고를 수 있습니다.
    assert [s.symbol_id for s in candidates[0].related_choices] == [
        "app/s.py::Service.run",
        "app/s.py::helper",
    ]


def test_contribution_label_comes_before_size() -> None:
    mine = sym("app/m.py", "mine", 1, 20)
    theirs = sym("app/t.py", "theirs", 1, 200)
    labels = {mine.symbol_id: ContributionLabel.VERIFIED}

    candidates = select_candidates(
        [theirs, mine], lambda s: labels.get(s.symbol_id, ContributionLabel.UNVERIFIED)
    )

    assert [c.symbol.name for c in candidates] == ["mine", "theirs"]
