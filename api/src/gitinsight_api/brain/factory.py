"""설정으로 면접 두뇌와 LLM 클라이언트를 만듭니다. CLI와 평가 스크립트가 같이 씁니다."""

from gitinsight_api.brain.analysis_source import FixtureAnalysisSource
from gitinsight_api.brain.repository import InMemorySessionRepository
from gitinsight_api.brain.service import InterviewBrain
from gitinsight_api.llm import CostRecorder, LiteLLMClient
from gitinsight_api.settings import Settings
from gitinsight_contracts.fixtures import load_analysis


def build_judge_llm(settings: Settings, cost_recorder: CostRecorder | None = None) -> LiteLLMClient:
    return LiteLLMClient(
        settings.llm_judge_model,
        cost_recorder=cost_recorder,
        reasoning_effort=settings.llm_judge_reasoning_effort,
    )


def build_fixture_brain(
    settings: Settings,
    *,
    llm: LiteLLMClient | None,
    fixture: str | None = None,
    force_prepared: bool = False,
) -> InterviewBrain:
    """샘플 분석 결과와 메모리 저장소로 면접 두뇌를 만듭니다. llm이 없으면 미리 만든 질문만 씁니다."""
    from gitinsight_api.llm.fake import FakeLLM

    analysis = load_analysis(fixture or settings.fixture_analysis)
    return InterviewBrain(
        analyses=FixtureAnalysisSource(analysis),
        repository=InMemorySessionRepository(),
        llm=llm if llm is not None else FakeLLM(),
        judge_timeout_s=settings.judge_timeout_s,
        plan_timeout_s=settings.plan_timeout_s,
        judge_model=settings.llm_judge_model,
        plan_model=settings.llm_analysis_model,
        force_prepared=force_prepared or llm is None,
    )
