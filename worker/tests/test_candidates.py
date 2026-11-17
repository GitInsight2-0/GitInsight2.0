from gitinsight_contracts.analysis import ContributionLabel
from gitinsight_contracts.common import SymbolKind, SymbolRef
from gitinsight_worker.candidates import (
    MAX_PER_FILE,
    MIN_LINES,
    has_enough_logic,
    is_inactive_path,
    is_test_path,
    logic_line_count,
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


# ---- 껍데기 코드 거르기 (PR #54 리뷰) ----

HOLLOW_CLASS = '''class SeedManager:
    """초기 데이터를 관리합니다.

    Args:
        service: 주입받은 서비스.
    """

    def __init__(self, service=None):
        # 나중에 쓰려고 보관합니다.
        self._service = service

    def run_if_empty(self) -> None:
        """run_all의 별칭입니다."""
        self.run_all()

    def run_all(self) -> None:
        """모든 작업을 실행합니다."""
        logger.info("기본 작업을 건너뜁니다.")
'''

WORKING_FUNCTION = '''def total(items):
    """합계를 구합니다."""
    result = 0
    for item in items:
        if item.skip:
            continue
        result += item.value
    logger.debug("total=%s", result)
    return result
'''


def test_hollow_class_has_little_logic() -> None:
    assert logic_line_count(HOLLOW_CLASS) == 2
    assert not has_enough_logic(HOLLOW_CLASS)


def test_working_function_has_enough_logic() -> None:
    # 로그와 문서 문자열은 빼고, 반복과 분기와 값을 돌려주는 줄은 셉니다.
    assert logic_line_count(WORKING_FUNCTION) == 6
    assert has_enough_logic(WORKING_FUNCTION)


def test_single_line_docstring_and_other_logging_forms_are_not_logic() -> None:
    code = '''def noop():
    """한 줄 문서 문자열."""
    print("start")
    logging.warning("x")
    self._logger.info("y")
    console.log("z")
    pass
    return None
'''
    assert logic_line_count(code) == 0


def test_string_value_lines_are_counted_not_skipped() -> None:
    # 문서 문자열이 아닌 여러 줄 문자열 값은 셉니다. 적게 세서 좋은 코드를 버리지 않게 합니다.
    code = '''def prompt():
    text = """첫 줄
둘째 줄
"""
    return text
'''
    assert logic_line_count(code) == 4
