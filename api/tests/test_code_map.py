"""설명 못 한 코드 지도와, 답변이 부족했던 주제만 다시 묻는 다시 보기 면접 (#107)."""

from pathlib import Path
from uuid import UUID, uuid4

import pytest

from gitinsight_api.auth import CurrentUser, get_current_user
from gitinsight_api.brain.analysis_source import FixtureAnalysisSource
from gitinsight_api.brain.judge import JUDGE_PURPOSE
from gitinsight_api.brain.protocol import NothingToReviewError
from gitinsight_api.brain.repository import InMemorySessionRepository
from gitinsight_api.brain.service import InterviewBrain
from gitinsight_api.brain.utterance import FIRST_INTRO_REVIEW, NEXT_INTRO_REVIEW
from gitinsight_api.llm.fake import FakeLLM, FakeStep
from gitinsight_api.main import create_app
from gitinsight_api.report.builder import ReportBuilder
from gitinsight_api.report.code_map import ReportHistory
from gitinsight_api.report.service import InMemoryReportStore, ReportService
from gitinsight_api.routes.sessions import NOTHING_TO_REVIEW
from gitinsight_api.settings import Settings
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

from api_test_support import ClientFactory

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
            review=ReportHistory(store),
        )
        # 리포트 LLM 없이 턴별 판단 표로 만듭니다. 코드 지도는 LLM을 쓰지 않아 똑같이 나옵니다.
        self.reports = ReportService(
            analyses=source,
            sessions=repository,
            store=store,
            builder=ReportBuilder(llm=self.llm, force_prepared=True),
        )

    async def start(
        self,
        analysis: AnalysisResult = ANALYSIS,
        owner_id: str | None = OWNER,
        *,
        review_only: bool = False,
    ) -> tuple[UUID, BrainReply]:
        return await self.brain.start(
            SessionCreate(
                analysis_id=analysis.analysis_id, mode=InterviewMode.TEXT, review_only=review_only
            ),
            owner_id=owner_id,
        )

    async def interview(
        self,
        *answers: tuple[str, Judgment],
        analysis: AnalysisResult = ANALYSIS,
        owner_id: str | None = OWNER,
        review_only: bool = False,
    ) -> Report:
        session_id, _ = await self.start(analysis, owner_id, review_only=review_only)
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


async def stuck_on_second_topic(server: Server, owner_id: str = OWNER) -> Report:
    """첫 주제는 다른 이야기만 하다 넘어가고, 둘째 주제에서 힌트를 받고도 막힌 면접입니다."""
    return await server.interview(
        ("점심 뭐 드셨어요?", OFF_TOPIC),
        ("날씨가 좋네요.", OFF_TOPIC),
        ("잘 모르겠습니다.", DONT_KNOW),
        ("모르겠어요.", DONT_KNOW),
        owner_id=owner_id,
    )


async def topic_order(server: Server, session_id: UUID) -> list[str]:
    state = await server.brain.repository.get(session_id)
    assert state is not None
    return state.topic_order


async def say_dont_know(server: Server, session_id: UUID) -> BrainReply:
    server.llm.push(JUDGE_PURPOSE, FakeStep(value=DONT_KNOW))
    return await server.brain.handle_turn(
        session_id,
        TurnRequest(turn_id=uuid4(), transcript="모르겠습니다.", source=TurnSource.TEXT),
    )


async def test_other_users_history_is_not_counted_or_reviewed() -> None:
    server = Server()
    await stuck_on_second_topic(server, owner_id="someone-else")

    report = await server.interview((GOOD, ANSWER))

    assert report.code_map is not None
    assert report.code_map.stuck_count == 0
    # 다른 사람이 막힌 주제는 내 다시 보기 면접에 나오지 않습니다.
    with pytest.raises(NothingToReviewError):
        await server.start(review_only=True)


async def test_review_interview_asks_only_the_stuck_topic() -> None:
    server = Server()
    report = await stuck_on_second_topic(server)
    assert statuses(report) == {SECOND.symbol.symbol_id: ExplainStatus.STUCK}

    session_id, first = await server.start(review_only=True)

    assert first.topic_id == SECOND.topic_id
    assert first.highlight == SECOND.symbol
    assert first.utterance == f"{FIRST_INTRO_REVIEW} {SECOND.base_question}"
    # 답변이 부족했던 주제만 묻습니다. 나머지 주제는 넣지 않습니다.
    assert await topic_order(server, session_id) == [SECOND.topic_id]
    # 그 주제가 끝나면 다른 주제로 넘어가지 않고 면접을 마칩니다. 모르겠다고 두 번 답하면 주제가 끝납니다.
    await say_dont_know(server, session_id)
    last = await say_dont_know(server, session_id)
    assert last.done
    assert last.topic_id != FIRST.topic_id


async def test_review_interview_includes_topic_explained_after_hint() -> None:
    server = Server()
    report = await server.interview(("잘 모르겠습니다.", DONT_KNOW), (GOOD, ANSWER))
    assert statuses(report) == {FIRST.symbol.symbol_id: ExplainStatus.EXPLAINED_AFTER_HINT}

    session_id, first = await server.start(review_only=True)

    # 힌트를 받고 설명한 함수도 힌트 없이 다시 설명해 보게 합니다.
    assert first.topic_id == FIRST.topic_id
    assert await topic_order(server, session_id) == [FIRST.topic_id]


async def test_review_interview_keeps_rule_order_and_says_why_on_each_topic() -> None:
    server = Server()
    default_session, _ = await server.start()
    default_order = await topic_order(server, default_session)
    # 첫 주제와 둘째 주제에서 모두 힌트를 받고도 막힌 면접입니다.
    report = await server.interview(*[("모르겠습니다.", DONT_KNOW)] * 4)
    assert statuses(report) == {
        FIRST.symbol.symbol_id: ExplainStatus.STUCK,
        SECOND.symbol.symbol_id: ExplainStatus.STUCK,
    }

    session_id, first = await server.start(review_only=True)

    weak = {FIRST.topic_id, SECOND.topic_id}
    order = await topic_order(server, session_id)
    assert order == [t for t in default_order if t in weak]
    assert first.topic_id == order[0]
    await say_dont_know(server, session_id)
    moved = await say_dont_know(server, session_id)
    # 다음 주제로 넘어갈 때도 다시 묻는 까닭을 말합니다.
    second = ANALYSIS.topic(order[1])
    assert moved.topic_id == second.topic_id
    assert moved.utterance == f"{NEXT_INTRO_REVIEW} {second.base_question}"


async def test_topic_explained_in_review_drops_out_of_the_next_review() -> None:
    server = Server()
    await server.interview(("잘 모르겠습니다.", DONT_KNOW), ("모르겠어요.", DONT_KNOW))

    again = await server.interview((GOOD, ANSWER), review_only=True)

    # 다시 보기 면접에서 힌트 없이 설명했으므로 가장 최근 상태가 설명함입니다.
    assert statuses(again) == {FIRST.symbol.symbol_id: ExplainStatus.EXPLAINED}
    with pytest.raises(NothingToReviewError):
        await server.start(review_only=True)


async def test_ordinary_interview_ignores_past_interviews() -> None:
    fresh = Server()
    fresh_session, fresh_first = await fresh.start()
    server = Server()
    await stuck_on_second_topic(server)

    session_id, first = await server.start()

    # 다시 보기를 요청하지 않으면 지난 면접과 관계없이 늘 같은 순서와 같은 첫 인사로 시작합니다.
    assert first.topic_id == FIRST.topic_id
    assert first.utterance == fresh_first.utterance
    assert await topic_order(server, session_id) == await topic_order(fresh, fresh_session)
    state = await server.brain.repository.get(session_id)
    assert state is not None
    assert not state.review_only


async def test_review_interview_without_history_or_owner_is_refused() -> None:
    server = Server()
    with pytest.raises(NothingToReviewError):
        await server.start(review_only=True)

    await stuck_on_second_topic(server)
    # 로그인 사용자가 없으면 지난 면접을 찾을 수 없습니다.
    with pytest.raises(NothingToReviewError):
        await server.start(owner_id=None, review_only=True)


async def test_session_api_starts_review_interview_or_answers_409(
    client_for: ClientFactory, metrics_path: Path
) -> None:
    server = Server()
    app = create_app(
        Settings(lab_enabled=False, metrics_path=metrics_path),
        brain=server.brain,
        report_service=server.reports,
    )
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(OWNER, OWNER)
    body = {"analysis_id": ANALYSIS.analysis_id, "mode": "text", "review_only": True}

    async with client_for(app) as client:
        nothing = await client.post("/api/sessions", json=body)
        await stuck_on_second_topic(server)
        created = await client.post("/api/sessions", json=body)

    assert nothing.status_code == 409
    assert nothing.json() == {"detail": NOTHING_TO_REVIEW}
    assert created.status_code == 201
    assert created.json()["first"]["topic_id"] == SECOND.topic_id


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
