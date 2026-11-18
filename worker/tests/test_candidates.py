from gitinsight_contracts.analysis import ContributionLabel
from gitinsight_contracts.common import SymbolKind, SymbolRef
from gitinsight_worker.candidates import (
    MAX_PER_FILE,
    MIN_LINES,
    has_enough_logic,
    is_inactive_path,
    is_test_path,
    logic_line_count,
    script_logic_line_count,
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


# ---- TypeScript·JavaScript (#72) ----


def test_script_test_and_inactive_paths() -> None:
    assert is_test_path("web/lib/api.test.ts")
    assert is_test_path("web/components/Button.spec.tsx")
    assert is_test_path("src/__tests__/util.js")
    assert is_test_path("src/__mocks__/client.mjs")
    assert not is_test_path("web/lib/latest.ts")
    assert not is_test_path("web/lib/spec.ts")
    assert is_inactive_path("src/old.ts")
    assert is_inactive_path("examples/demo.jsx")
    assert not is_inactive_path("src/models.ts")


HOLLOW_SCRIPT_CLASS = """export class AuditLogger {
  /**
   * 시작을 기록합니다.
   * @param name 작업 이름
   */
  start(name: string) {
    console.log("start", name);
  }

  // 끝을 기록합니다.
  finish(name: string) {
    this.logger.info("finish", name);
  }

  reset() {
    return;
  }
}
"""

WORKING_SCRIPT_FUNCTION = """export async function loadItems(client: Client, limit = 10): Promise<Item[]> {
  const response = await client.get("/items", { limit });
  if (!response.ok) {
    throw new Error("failed");
  }
  const items = response.data.filter((item) => item.active);
  return items.slice(0, limit);
}
"""


def test_hollow_script_class_has_little_logic() -> None:
    # 메서드 머리줄 3개만 남습니다. 주석, 로그 호출, 빈 return, 닫는 괄호는 세지 않습니다.
    assert script_logic_line_count(HOLLOW_SCRIPT_CLASS) == 3
    assert not has_enough_logic(HOLLOW_SCRIPT_CLASS, "src/audit.ts")


def test_working_script_function_has_enough_logic() -> None:
    # 함수 머리줄과 닫는 괄호를 뺀 다섯 줄입니다.
    assert script_logic_line_count(WORKING_SCRIPT_FUNCTION) == 5
    assert has_enough_logic(WORKING_SCRIPT_FUNCTION, "src/items.ts")


def test_components_and_other_code_alternate_so_routes_become_topics() -> None:
    # 줄 수만 보면 컴포넌트 넷이 앞자리를 다 차지합니다.
    symbols = [
        sym("app/components/Profile.tsx", "Profile", 1, 300),
        sym("app/components/Feed.tsx", "Feed", 1, 250),
        sym("app/components/Canvas.jsx", "Canvas", 1, 200),
        sym("app/components/Modal.tsx", "Modal", 1, 150),
        sym("app/api/items/route.ts", "POST", 1, 40),
        sym("lib/db.js", "connect", 1, 30),
    ]

    assert ids(symbols) == [
        "app/components/Profile.tsx::Profile",
        "app/api/items/route.ts::POST",
        "app/components/Feed.tsx::Feed",
        "lib/db.js::connect",
        "app/components/Canvas.jsx::Canvas",
        "app/components/Modal.tsx::Modal",
    ]


def test_alternation_starts_with_the_biggest_and_keeps_label_order() -> None:
    route = sym("app/api/route.ts", "handler", 1, 400)
    big = sym("app/Big.tsx", "Big", 1, 300)
    mine = sym("app/Mine.tsx", "Mine", 1, 20)
    other = sym("app/Other.tsx", "Other", 1, 100)
    labels = {mine.symbol_id: ContributionLabel.VERIFIED}

    candidates = select_candidates(
        [route, big, mine, other],
        lambda s: labels.get(s.symbol_id, ContributionLabel.UNVERIFIED),
    )

    # 기여 근거가 있는 코드가 먼저이고, 그 뒤는 가장 큰 후보(라우트)부터 번갈아 놓습니다.
    assert [c.symbol.name for c in candidates] == ["Mine", "handler", "Big", "Other"]


def test_repo_without_component_files_keeps_size_order() -> None:
    symbols = [
        sym("app/a.py", "small", 1, 10),
        sym("src/b.ts", "big", 1, 120),
        sym("app/c.py", "mid", 1, 60),
    ]

    assert ids(symbols) == ["src/b.ts::big", "app/c.py::mid", "app/a.py::small"]


def test_logic_rules_follow_the_file_language() -> None:
    # 경로가 없거나 Python이면 지금까지의 Python 기준을 그대로 씁니다.
    assert has_enough_logic(WORKING_FUNCTION) == has_enough_logic(WORKING_FUNCTION, "app/a.py")
    assert has_enough_logic(HOLLOW_CLASS) == has_enough_logic(HOLLOW_CLASS, "app/a.py")
