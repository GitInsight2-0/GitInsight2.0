"""설명 못 한 코드 지도와 거기서 시작하는 재면접 (#107)."""

from uuid import UUID, uuid4

from gitinsight_api.brain.analysis_source import FixtureAnalysisSource
from gitinsight_api.brain.judge import JUDGE_PURPOSE
from gitinsight_api.brain.planner import RevisitTargets, revisit_first
from gitinsight_api.brain.repository import InMemorySessionRepository
from gitinsight_api.brain.service import InterviewBrain
from gitinsight_api.brain.utterance import (
    FIRST_INTRO_CHANGED,
    FIRST_INTRO_STUCK,
    NEXT_INTRO_CHANGED,
)
from gitinsight_api.llm.fake import FakeLLM, FakeStep
from gitinsight_api.report.builder import ReportBuilder
from gitinsight_api.report.code_map import ReportHistory, changed_symbol_ids
from gitinsight_api.report.service import InMemoryReportStore, ReportService
from gitinsight_contracts.analysis import AnalysisResult
from gitinsight_contracts.fixtures import load_analysis
from gitinsight_contracts.interview import (
    BrainReply,
    InterviewMode,
    SessionCreate,
    TurnRequest,
    TurnSource,
)
from gitinsight_contracts.llm import CoverageStatus, Followup, Intent, Judgment, PointCoverage
from gitinsight_contracts.report import ExplainStatus, Report

ANALYSIS = load_analysis()
FIRST = ANALYSIS.topic("build-github-repo")
SECOND = ANALYSIS.topic("vector-service")
GOOD = "커밋 SHA를 컬렉션 이름에 넣어서 커밋 단위로 캐시합니다."
OWNER = "user-1"

ANSWER = Judgment(
    intent=Intent.ANSWER,
    coverage=[
        PointCoverage(
            point_id="bgr-1",
            status=CoverageStatus.COVERED,
            evidence_quote="커밋 SHA를 컬렉션 이름에 넣어서",
        )
    ],
    followup=None,
)
DONT_KNOW = Judgment(intent=Intent.DONT_KNOW, coverage=[], followup=None)
OFF_TOPIC = Judgment(intent=Intent.OFF_TOPIC, coverage=[], followup=None)


class Server:
    """리포트 저장소를 같이 쓰는 면접 두뇌와 리포트 서비스입니다. 판단은 FakeLLM에 넣은 순서대로 나옵니다."""

    def __init__(self, *analyses: AnalysisResult) -> None:
        self.llm = FakeLLM()
        source = FixtureAnalysisSource(*(analyses or (ANALYSIS,)))
        store = InMemoryReportStore()
        repository = InMemorySessionRepository()
        self.brain = InterviewBrain(
            analyses=source,
            repository=repository,
            llm=self.llm,
            revisit=ReportHistory(store, source),
        )
        # 리포트 LLM 없이 턴별 판단 표로 만듭니다. 코드 지도는 LLM을 쓰지 않아 똑같이 나옵니다.
        self.reports = ReportService(
            analyses=source,
            sessions=repository,
            store=store,
            builder=ReportBuilder(llm=self.llm, force_prepared=True),
        )

    async def start(
        self, analysis: AnalysisResult = ANALYSIS, owner_id: str | None = OWNER
    ) -> tuple[UUID, BrainReply]:
        return await self.brain.start(
            SessionCreate(analysis_id=analysis.analysis_id, mode=InterviewMode.TEXT),
            owner_id=owner_id,
        )

    async def interview(
        self,
        *answers: tuple[str, Judgment],
        analysis: AnalysisResult = ANALYSIS,
        owner_id: str | None = OWNER,
    ) -> Report:
        session_id, _ = await self.start(analysis, owner_id)
        for transcript, judgment in answers:
            self.llm.push(JUDGE_PURPOSE, FakeStep(value=judgment))
            await self.brain.handle_turn(
                session_id,
                TurnRequest(turn_id=uuid4(), transcript=transcript, source=TurnSource.TEXT),
            )
        return await self.reports.end_session(session_id)


def statuses(report: Report) -> dict[str, ExplainStatus]:
    assert report.code_map is not None
    return {e.symbol.symbol_id: e.status for e in report.code_map.entries}


async def test_answered_function_is_explained_with_analysis_location() -> None:
    report = await Server().interview((GOOD, ANSWER))

    code_map = report.code_map
    assert code_map is not None
    assert code_map.analysis_id == ANALYSIS.analysis_id
    assert code_map.repo_full_name == ANALYSIS.repo_full_name
    [entry] = code_map.entries
    # 코드 위치는 분석 결과의 SymbolRef 그대로입니다.
    assert entry.symbol == FIRST.symbol
    assert entry.topic_id == FIRST.topic_id
    assert entry.status is ExplainStatus.EXPLAINED
    assert entry.url.endswith(
        f"/blob/{ANALYSIS.commit_sha}/{FIRST.symbol.path}"
        f"#L{FIRST.symbol.start_line}-L{FIRST.symbol.end_line}"
    )
    assert code_map.key_function_count == len({t.symbol.symbol_id for t in ANALYSIS.topics})
    assert (code_map.explained_count, code_map.after_hint_count, code_map.stuck_count) == (1, 0, 0)


async def test_answer_after_hint_is_explained_after_hint() -> None:
    report = await Server().interview(("잘 모르겠습니다.", DONT_KNOW), (GOOD, ANSWER))

    assert statuses(report) == {FIRST.symbol.symbol_id: ExplainStatus.EXPLAINED_AFTER_HINT}
    assert report.code_map is not None
    assert report.code_map.after_hint_count == 1


async def test_dont_know_after_hint_is_stuck() -> None:
    report = await Server().interview(("잘 모르겠습니다.", DONT_KNOW), ("모르겠어요.", DONT_KNOW))

    assert statuses(report) == {FIRST.symbol.symbol_id: ExplainStatus.STUCK}
    assert report.code_map is not None
    assert report.code_map.stuck_count == 1


async def test_answer_that_covers_no_expected_point_is_stuck() -> None:
    empty = Judgment(intent=Intent.ANSWER, coverage=[], followup=None)

    report = await Server().interview(("음, 그냥 저장하는 코드입니다.", empty))

    assert statuses(report) == {FIRST.symbol.symbol_id: ExplainStatus.STUCK}


async def test_function_without_judged_answer_is_left_out() -> None:
    # 다른 이야기만 한 함수는 설명했는지 알 수 없어서 지도에 넣지 않습니다.
    report = await Server().interview(("점심 뭐 드셨어요?", OFF_TOPIC))

    assert report.code_map is not None
    assert report.code_map.entries == []
    assert report.code_map.explained_count == 0


async def test_followup_on_related_function_gets_its_own_entry() -> None:
    related = FIRST.related_symbols[0]
    # 기대 포인트를 충분히 다뤄야 빠진 포인트를 다시 묻지 않고 꼬리질문으로 넘어갑니다.
    with_followup = Judgment(
        intent=Intent.ANSWER,
        coverage=[
            PointCoverage(
                point_id="bgr-1", status=CoverageStatus.COVERED, evidence_quote="커밋 SHA를"
            ),
            PointCoverage(
                point_id="bgr-2", status=CoverageStatus.COVERED, evidence_quote="캐시합니다"
            ),
        ],
        followup=Followup(
            question="연결을 재사용한 이유는 무엇인가요?", symbol_id=related.symbol_id
        ),
    )

    report = await Server().interview((GOOD, with_followup), ("잘 모르겠습니다.", DONT_KNOW))

    assert statuses(report) == {
        FIRST.symbol.symbol_id: ExplainStatus.EXPLAINED,
        related.symbol_id: ExplainStatus.STUCK,
    }
    # 누적 숫자는 핵심 함수(주제의 주 코드)만 셉니다.
    assert report.code_map is not None
    assert report.code_map.stuck_count == 0


async def test_counts_accumulate_over_interviews_with_latest_status() -> None:
    server = Server()
    await server.interview(("잘 모르겠습니다.", DONT_KNOW), ("모르겠어요.", DONT_KNOW))

    again = await server.interview((GOOD, ANSWER))

    # 지난번에 막혔던 함수를 이번에 설명했으므로 가장 최근 상태로 셉니다.
    assert again.code_map is not None
    assert (again.code_map.explained_count, again.code_map.stuck_count) == (1, 0)


async def test_other_users_history_is_not_counted_or_revisited() -> None:
    server = Server()
    await stuck_on_second_topic(server, owner_id="someone-else")

    _, first = await server.start()
    report = await server.interview((GOOD, ANSWER))

    assert first.topic_id == FIRST.topic_id
    assert report.code_map is not None
    assert report.code_map.stuck_count == 0


async def stuck_on_second_topic(server: Server, owner_id: str = OWNER) -> Report:
    """첫 주제는 다른 이야기만 하다 넘어가고, 둘째 주제에서 힌트를 받고도 막힌 면접입니다."""
    return await server.interview(
        ("점심 뭐 드셨어요?", OFF_TOPIC),
        ("날씨가 좋네요.", OFF_TOPIC),
        ("잘 모르겠습니다.", DONT_KNOW),
        ("모르겠어요.", DONT_KNOW),
        owner_id=owner_id,
    )


async def test_next_interview_starts_from_stuck_function() -> None:
    server = Server()
    report = await stuck_on_second_topic(server)
    assert statuses(report) == {SECOND.symbol.symbol_id: ExplainStatus.STUCK}

    session_id, first = await server.start()

    assert first.topic_id == SECOND.topic_id
    assert first.highlight == SECOND.symbol
    assert first.utterance == f"{FIRST_INTRO_STUCK} {SECOND.base_question}"
    state = await server.brain.repository.get(session_id)
    assert state is not None
    # 나머지 주제는 원래 순서 그대로입니다.
    assert state.topic_order[:2] == [SECOND.topic_id, FIRST.topic_id]


async def test_session_without_owner_keeps_default_order() -> None:
    server = Server()
    await stuck_on_second_topic(server)

    _, first = await server.start(owner_id=None)

    assert first.topic_id == FIRST.topic_id


def later_analysis(changed_topic_id: str) -> AnalysisResult:
    """같은 레포의 나중 커밋을 분석한 결과입니다. 주제 하나에만 새 커밋이 들어왔습니다."""
    new_sha = "b" * 40
    moved = AnalysisResult.model_validate_json(
        ANALYSIS.model_dump_json().replace(ANALYSIS.commit_sha, new_sha)
    )
    return moved.model_copy(
        update={
            "analysis_id": "later",
            "topics": [
                t.model_copy(update={"commits": [new_sha, *t.commits]})
                if t.topic_id == changed_topic_id
                else t
                for t in moved.topics
            ],
        }
    )


def test_changed_symbols_are_those_with_commits_the_old_analysis_did_not_have() -> None:
    target = ANALYSIS.topic("create-graph")
    later = later_analysis(target.topic_id)

    assert changed_symbol_ids(ANALYSIS, later) == {target.symbol.symbol_id}
    # 같은 커밋의 분석끼리는 바뀐 함수가 없습니다.
    assert changed_symbol_ids(ANALYSIS, ANALYSIS) == set()


async def test_next_interview_asks_stuck_then_changed_functions() -> None:
    changed = ANALYSIS.topic("create-graph")
    later = later_analysis(changed.topic_id)
    server = Server(ANALYSIS, later)
    await stuck_on_second_topic(server)

    session_id, _ = await server.start(later)

    state = await server.brain.repository.get(session_id)
    assert state is not None
    assert state.topic_order[:3] == [SECOND.topic_id, changed.topic_id, FIRST.topic_id]
    # 다음 주제로 넘어갈 때도 다시 묻는 까닭을 말합니다. 모르겠다고 두 번 답하면 다음 주제입니다.
    replies = []
    for _ in range(4):
        server.llm.push(JUDGE_PURPOSE, FakeStep(value=DONT_KNOW))
        replies.append(
            await server.brain.handle_turn(
                session_id,
                TurnRequest(turn_id=uuid4(), transcript="모르겠습니다.", source=TurnSource.TEXT),
            )
        )
    assert replies[1].utterance == f"{NEXT_INTRO_CHANGED} {changed.base_question}"
    # 다시 묻는 주제가 끝나면 원래 문장으로 돌아갑니다.
    assert replies[3].topic_id == FIRST.topic_id
    assert "지난 면접" not in replies[3].utterance


async def test_changed_function_intro_when_nothing_was_stuck() -> None:
    changed = ANALYSIS.topic("create-graph")
    later = later_analysis(changed.topic_id)
    server = Server(ANALYSIS, later)
    await server.interview((GOOD, ANSWER))

    _, first = await server.start(later)

    assert first.topic_id == changed.topic_id
    assert first.utterance == f"{FIRST_INTRO_CHANGED} {changed.base_question}"


def test_revisit_first_keeps_order_inside_each_group() -> None:
    order = ["a", "b", "c", "d", "e"]
    revisit = RevisitTargets(stuck=frozenset({"d", "b"}), changed=frozenset({"e"}))

    assert revisit_first(order, revisit) == ["b", "d", "e", "a", "c"]


def test_report_saved_before_code_map_still_loads() -> None:
    old = {
        "report_id": str(uuid4()),
        "session_id": str(uuid4()),
        "kind": "turn_table",
        "competencies": [],
        "topics": [],
        "strengths": [],
        "improvements": [],
        "next_topics": [],
        "turns": [],
    }

    assert Report.model_validate(old).code_map is None
