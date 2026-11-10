"""실제 LLM 연동 테스트 (수동 실행, API 키 필요).

사용:
  $env:LLM_PROVIDER="gemini"; uv run pytest -m live_llm -v
  $env:LLM_PROVIDER="openai"; uv run pytest -m live_llm -v

무료 등급 한도를 아끼려고 판단·계획·리포트를 각 1회만 호출합니다.
품질이나 지연이 아니라 "실제 제공사가 우리 구조화 출력 스키마를 받아 주는가"만 확인합니다.
보내는 데이터는 공개 데모 레포 샘플과 골든 대화 자막뿐입니다.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from gitinsight_api.brain.config import InterviewConfig
from gitinsight_api.brain.factory import build_judge_llm, build_report_llm
from gitinsight_api.brain.judge import judge_answer
from gitinsight_api.brain.planner import plan_interview
from gitinsight_api.brain.state import SessionState, TopicProgress, TurnRecord
from gitinsight_api.llm import InMemoryCostRecorder, LiteLLMClient
from gitinsight_api.report.builder import ReportBuilder
from gitinsight_api.settings import Settings
from gitinsight_contracts.fixtures import load_analysis, load_conversations
from gitinsight_contracts.interview import Action, BrainReply, InterviewMode
from gitinsight_contracts.llm import Judgment
from gitinsight_contracts.report import ReportKind

pytestmark = pytest.mark.live_llm

ANALYSIS = load_analysis()
TOPIC = ANALYSIS.topic("build-github-repo")
GOOD = load_conversations()[0].turns[0]
# 무료 등급은 응답이 느릴 수 있어 제한 시간을 넉넉히 둡니다. 지연 측정은 llm_latency.py가 맡습니다.
TIMEOUT_S = 60.0


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


async def test_judge_returns_valid_judgment(settings: Settings) -> None:
    recorder = InMemoryCostRecorder()
    outcome = await judge_answer(
        build_judge_llm(settings, recorder),
        TOPIC,
        TOPIC.base_question,
        GOOD.transcript,
        [],
        timeout_s=TIMEOUT_S,
    )

    print(f"\n[{settings.llm_judge_model}] {outcome.elapsed_ms:.0f}ms, 시도 {outcome.attempts}회")
    print(
        outcome.judgment.model_dump_json(indent=2) if outcome.judgment else outcome.fallback_reason
    )
    assert outcome.judgment is not None, f"판단 실패: {outcome.fallback_reason}"
    assert isinstance(outcome.judgment, Judgment)


async def test_plan_reorders_with_jd(settings: Settings) -> None:
    llm = LiteLLMClient(settings.llm_analysis_model)

    plan = await plan_interview(
        ANALYSIS,
        "LangGraph로 에이전트 워크플로를 설계하고 운영한 경험자를 찾습니다.",
        llm=llm,
        config=InterviewConfig(),
        timeout_s=TIMEOUT_S,
    )

    print(
        f"\n[{settings.llm_analysis_model}] {plan.source} {plan.topic_order} {plan.persona_keywords}"
    )
    # source가 rules면 LLM 출력이 검증에 실패했거나 호출이 실패한 것입니다.
    assert plan.source == "llm"


async def test_report_returns_full_report(settings: Settings) -> None:
    now = datetime.now(UTC)
    state = SessionState(
        session_id=uuid4(),
        analysis_id=ANALYSIS.analysis_id,
        mode=InterviewMode.TEXT,
        duration_sec=600,
        started_at=now,
        topic_order=[TOPIC.topic_id],
        main_topic_count=1,
        progress=TopicProgress(topic_id=TOPIC.topic_id),
        last_question=TOPIC.base_question,
        last_action=Action.ASK,
        last_highlight=TOPIC.symbol,
    )
    turn_id = uuid4()
    turn = TurnRecord(
        session_id=state.session_id,
        turn_id=turn_id,
        index=0,
        topic_id=TOPIC.topic_id,
        question=TOPIC.base_question,
        transcript=GOOD.transcript,
        judgment=GOOD.judgment,
        fallback_reason=None,
        reply=BrainReply(
            turn_id=turn_id,
            action=Action.FOLLOW_UP,
            utterance="커밋 SHA를 못 가져오면 캐시 키는 어떻게 되나요?",
            highlight=TOPIC.symbol,
            topic_id=TOPIC.topic_id,
            depth=1,
            remaining_sec=500,
            done=False,
        ),
        received_at=now,
    )
    builder = ReportBuilder(llm=build_report_llm(settings), report_timeout_s=TIMEOUT_S)

    report = await builder.build(ANALYSIS, state, [turn])

    print(f"\n[{settings.llm_report_model}] {report.kind.value}")
    print(report.model_dump_json(indent=2, include={"competencies", "strengths", "improvements"}))
    assert report.kind is ReportKind.FULL, "리포트 LLM 호출이 실패해 판단 표로 대체되었습니다"
