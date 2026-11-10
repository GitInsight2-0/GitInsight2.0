from gitinsight_api.brain.analysis_source import FixtureAnalysisSource
from gitinsight_api.brain.config import InterviewConfig
from gitinsight_api.brain.planner import PLAN_PURPOSE, plan_interview, rule_order
from gitinsight_api.brain.repository import InMemorySessionRepository
from gitinsight_api.brain.service import InterviewBrain
from gitinsight_api.llm import LLMTimeoutError
from gitinsight_api.llm.fake import FakeLLM, FakeStep
from gitinsight_contracts.fixtures import load_analysis
from gitinsight_contracts.interview import Action, InterviewMode, SessionCreate
from gitinsight_contracts.llm import InterviewPlanOutput

ANALYSIS = load_analysis()
RULE_ORDER = [
    "build-github-repo",
    "vector-service",
    "classify-user-intent",
    "create-graph",
    "next-question-node",
    "extract-question-pool",
]


def test_rule_order_puts_verified_first_then_easier() -> None:
    assert rule_order(ANALYSIS) == RULE_ORDER


async def test_without_jd_llm_is_not_called() -> None:
    fake = FakeLLM()
    plan = await plan_interview(ANALYSIS, None, llm=fake, config=InterviewConfig(), timeout_s=1)

    assert fake.calls == []
    assert plan.source == "rules"
    assert plan.topic_order == RULE_ORDER
    assert plan.main_topic_count == 5


async def test_jd_reorders_within_allowed_topics() -> None:
    fake = FakeLLM()
    fake.push(
        PLAN_PURPOSE,
        FakeStep(
            value=InterviewPlanOutput(
                topic_order=["create-graph", "vector-service"],
                persona_keywords=["LangGraph", "  ", "x" * 40],
            )
        ),
    )

    plan = await plan_interview(
        ANALYSIS, "LangGraph 경험자 우대", llm=fake, config=InterviewConfig(), timeout_s=1
    )

    assert plan.source == "llm"
    assert plan.topic_order[:2] == ["create-graph", "vector-service"]
    assert sorted(plan.topic_order) == sorted(RULE_ORDER)
    assert plan.persona_keywords == ["LangGraph"]
    # JD는 신뢰할 수 없는 데이터 블록 안에만 들어갑니다.
    user_message = fake.calls[0].messages[1]["content"]
    assert (
        '<untrusted_data kind="job_description">\nLangGraph 경험자 우대\n</untrusted_data>'
        in user_message
    )


async def test_unknown_topic_id_falls_back_to_rules() -> None:
    fake = FakeLLM()
    fake.push(
        PLAN_PURPOSE,
        FakeStep(
            value=InterviewPlanOutput(
                topic_order=["create-graph", "made-up-topic"], persona_keywords=["Kafka"]
            )
        ),
    )

    plan = await plan_interview(ANALYSIS, "JD", llm=fake, config=InterviewConfig(), timeout_s=1)

    assert plan.source == "rules"
    assert plan.topic_order == RULE_ORDER
    assert plan.persona_keywords == []


async def test_llm_failure_falls_back_to_rules() -> None:
    fake = FakeLLM()
    fake.push(PLAN_PURPOSE, FakeStep(error=LLMTimeoutError("timeout")))

    plan = await plan_interview(ANALYSIS, "JD", llm=fake, config=InterviewConfig(), timeout_s=1)

    assert plan.source == "rules"
    assert plan.topic_order == RULE_ORDER


async def test_spare_topics_are_limited() -> None:
    config = InterviewConfig(main_topics=2, spare_topics=1)
    plan = await plan_interview(ANALYSIS, None, llm=FakeLLM(), config=config, timeout_s=1)

    assert plan.topic_order == RULE_ORDER[:3]
    assert plan.main_topic_count == 2


async def test_start_returns_first_question_with_fixture_highlight() -> None:
    repository = InMemorySessionRepository()
    brain = InterviewBrain(
        analyses=FixtureAnalysisSource(ANALYSIS), repository=repository, llm=FakeLLM()
    )

    session_id, reply = await brain.start(
        SessionCreate(analysis_id=ANALYSIS.analysis_id, mode=InterviewMode.TEXT)
    )

    first_topic = ANALYSIS.topic("build-github-repo")
    assert reply.action is Action.ASK
    assert reply.highlight == first_topic.symbol
    assert reply.utterance.endswith(first_topic.base_question)
    assert "작업하신 기록이 있는" in reply.utterance
    assert reply.remaining_sec == 600
    stored = await repository.get(session_id)
    assert stored is not None
    assert stored.topic_order == RULE_ORDER[:6]
    assert stored.last_question == first_topic.base_question
