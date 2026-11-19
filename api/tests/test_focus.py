"""확인 질문이 가리키는 줄(BrainReply.focus)을 두뇌가 정하는 규칙 (#156)."""

from datetime import UTC, datetime
from uuid import uuid4

from gitinsight_api.brain.analysis_source import FixtureAnalysisSource
from gitinsight_api.brain.judge import JUDGE_PURPOSE
from gitinsight_api.brain.repository import InMemorySessionRepository
from gitinsight_api.brain.service import InterviewBrain, focus_within
from gitinsight_api.llm.fake import FakeLLM, FakeStep
from gitinsight_contracts.analysis import AnalysisResult
from gitinsight_contracts.common import LineSpan
from gitinsight_contracts.fixtures import load_analysis
from gitinsight_contracts.interview import (
    Action,
    InterviewMode,
    SessionCreate,
    TurnRequest,
    TurnSource,
)
from gitinsight_contracts.llm import CoverageStatus, Followup, Intent, Judgment, PointCoverage

TOPIC_ID = "build-github-repo"
START = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
ANSWER = "알려진 속성 이름을 먼저 확인하고 없으면 새로 만듭니다"


def analysis_with_focus(focus: LineSpan | None) -> AnalysisResult:
    """첫 주제의 첫 요점에 focus를 붙인 분석 결과입니다. 이 요점이 확인 질문(PROBE)으로 나옵니다."""
    data = load_analysis("gitinsight_agent_live").model_dump(mode="json")
    topic = next(t for t in data["topics"] if t["topic_id"] == TOPIC_ID)
    topic["expected_points"][0]["focus"] = focus.model_dump() if focus is not None else None
    return AnalysisResult.model_validate(data)


def inner_span(analysis: AnalysisResult) -> LineSpan:
    symbol = analysis.topic(TOPIC_ID).symbol
    return LineSpan(start_line=symbol.start_line + 2, end_line=symbol.start_line + 9)


def make_brain(analysis: AnalysisResult, fake: FakeLLM) -> InterviewBrain:
    return InterviewBrain(
        analyses=FixtureAnalysisSource(analysis),
        repository=InMemorySessionRepository(),
        llm=fake,
        judge_timeout_s=0.2,
        clock=lambda: START,
    )


def turn(transcript: str) -> TurnRequest:
    return TurnRequest(turn_id=uuid4(), transcript=transcript, source=TurnSource.TEXT)


def low_coverage(analysis: AnalysisResult) -> Judgment:
    """둘째 요점만 다룬 답변입니다. 빠진 첫 요점을 확인 질문으로 묻게 됩니다."""
    points = [p.point_id for p in analysis.topic(TOPIC_ID).expected_points]
    return Judgment(
        intent=Intent.ANSWER,
        coverage=[
            PointCoverage(
                point_id=points[1],
                status=CoverageStatus.COVERED,
                evidence_quote="알려진 속성 이름을 먼저 확인하고",
            )
        ],
        followup=None,
    )


async def start(brain: InterviewBrain, analysis: AnalysisResult):
    return await brain.start(
        SessionCreate(analysis_id=analysis.analysis_id, mode=InterviewMode.TEXT)
    )


async def test_first_question_has_no_focus() -> None:
    analysis = analysis_with_focus(inner_span(load_analysis("gitinsight_agent_live")))

    _, first = await start(make_brain(analysis, FakeLLM()), analysis)

    assert first.highlight == analysis.topic(TOPIC_ID).symbol
    assert first.focus is None


async def test_probe_carries_the_focus_of_its_point() -> None:
    span = inner_span(load_analysis("gitinsight_agent_live"))
    analysis = analysis_with_focus(span)
    fake = FakeLLM()
    fake.push(JUDGE_PURPOSE, FakeStep(value=low_coverage(analysis)))
    brain = make_brain(analysis, fake)
    session_id, _ = await start(brain, analysis)

    reply = await brain.handle_turn(session_id, turn(ANSWER))

    assert reply.action is Action.PROBE
    # highlight는 늘 주제 코드 전체이고, 좁은 줄은 focus에만 담습니다.
    assert reply.highlight == analysis.topic(TOPIC_ID).symbol
    assert reply.focus == span


async def test_probe_without_a_chosen_range_has_no_focus() -> None:
    analysis = analysis_with_focus(None)
    fake = FakeLLM()
    fake.push(JUDGE_PURPOSE, FakeStep(value=low_coverage(analysis)))
    brain = make_brain(analysis, fake)
    session_id, _ = await start(brain, analysis)

    reply = await brain.handle_turn(session_id, turn(ANSWER))

    assert reply.action is Action.PROBE
    assert reply.focus is None


async def test_hint_and_repeat_keep_the_focus_of_the_pending_probe() -> None:
    span = inner_span(load_analysis("gitinsight_agent_live"))
    analysis = analysis_with_focus(span)
    dont_know = Judgment(intent=Intent.DONT_KNOW, coverage=[], followup=None)
    fake = FakeLLM()
    fake.push(JUDGE_PURPOSE, FakeStep(value=low_coverage(analysis)), FakeStep(value=dont_know))
    brain = make_brain(analysis, fake)
    session_id, _ = await start(brain, analysis)
    await brain.handle_turn(session_id, turn(ANSWER))

    hint = await brain.handle_turn(session_id, turn("잘 모르겠습니다"))
    repeat = await brain.handle_turn(session_id, turn("   "))

    # 확인 질문에 아직 답하지 못했으므로 같은 줄을 계속 가리킵니다.
    assert hint.action is Action.HINT
    assert hint.focus == span
    assert repeat.action is Action.REPEAT
    assert repeat.focus == span


async def test_focus_is_cleared_when_the_question_changes() -> None:
    span = inner_span(load_analysis("gitinsight_agent_live"))
    analysis = analysis_with_focus(span)
    points = [p.point_id for p in analysis.topic(TOPIC_ID).expected_points]
    covered_all = Judgment(
        intent=Intent.ANSWER,
        coverage=[
            PointCoverage(
                point_id=point_id,
                status=CoverageStatus.COVERED,
                evidence_quote="알려진 속성 이름을 먼저 확인하고",
            )
            for point_id in points
        ],
        followup=Followup(question="그렇게 만든 이유는 무엇인가요?", symbol_id=None),
    )
    fake = FakeLLM()
    fake.push(JUDGE_PURPOSE, FakeStep(value=low_coverage(analysis)), FakeStep(value=covered_all))
    brain = make_brain(analysis, fake)
    session_id, _ = await start(brain, analysis)
    probe = await brain.handle_turn(session_id, turn(ANSWER))
    assert probe.focus == span

    after = await brain.handle_turn(session_id, turn(ANSWER))

    # 꼬리질문이든 다음 주제든 질문이 바뀌면 앞 질문의 줄을 들고 가지 않습니다.
    assert after.action in (Action.FOLLOW_UP, Action.NEXT_TOPIC)
    assert after.focus is None


def test_focus_outside_the_highlight_is_dropped_instead_of_failing() -> None:
    analysis = load_analysis("gitinsight_agent_live")
    symbol = analysis.topic(TOPIC_ID).symbol
    inside = LineSpan(start_line=symbol.start_line, end_line=symbol.end_line)
    outside = LineSpan(start_line=symbol.end_line, end_line=symbol.end_line + 1)

    assert focus_within(inside, symbol) == inside
    assert focus_within(outside, symbol) is None
    assert focus_within(inside, None) is None
    assert focus_within(None, symbol) is None
