"""설정으로 면접 두뇌와 LLM 클라이언트를 만듭니다. CLI와 평가 스크립트가 같이 씁니다."""

from gitinsight_api.brain.analysis_source import FixtureAnalysisSource
from gitinsight_api.brain.repository import InMemorySessionRepository
from gitinsight_api.brain.service import InterviewBrain
from gitinsight_api.llm import CostRecorder, LiteLLMClient, LLMClient
from gitinsight_api.report.builder import ReportBuilder
from gitinsight_api.report.service import InMemoryReportStore, ReportService
from gitinsight_api.settings import Settings
from gitinsight_contracts.analysis import AnalysisResult
from gitinsight_contracts.fixtures import load_analysis


def build_judge_llm(settings: Settings, cost_recorder: CostRecorder | None = None) -> LiteLLMClient:
    return LiteLLMClient(
        settings.llm_judge_model,
        cost_recorder=cost_recorder,
        reasoning_effort=settings.llm_judge_reasoning_effort,
    )


def build_report_llm(
    settings: Settings, cost_recorder: CostRecorder | None = None
) -> LiteLLMClient:
    return LiteLLMClient(settings.llm_report_model, cost_recorder=cost_recorder)


def build_report_service(
    settings: Settings,
    brain: InterviewBrain,
    analysis: AnalysisResult,
    *,
    llm: LLMClient | None,
    force_prepared: bool = False,
) -> ReportService:
    """llm이 없으면 LLM 없이 턴별 판단 표 리포트만 만듭니다."""
    from gitinsight_api.llm.fake import FakeLLM

    return ReportService(
        analyses=FixtureAnalysisSource(analysis),
        sessions=brain.repository,
        store=InMemoryReportStore(),
        builder=ReportBuilder(
            llm=llm if llm is not None else FakeLLM(),
            report_timeout_s=settings.report_timeout_s,
            rejudge_timeout_s=settings.rejudge_timeout_s,
            report_model=settings.llm_report_model,
            judge_model=settings.llm_judge_model,
            force_prepared=force_prepared or settings.brain_force_prepared or llm is None,
            quote_matcher=settings.quote_matcher(),
        ),
    )


def build_server_services(settings: Settings) -> tuple[InterviewBrain, ReportService]:
    """HTTP 서버용 면접 두뇌와 리포트 서비스를 만듭니다.

    LLM 클라이언트는 만들기만 하고 부르지 않으므로 서버 시작 때 LLM이나 네트워크를 쓰지 않습니다.
    세션과 리포트는 메모리 저장소에 둡니다.
    """
    brain = build_fixture_brain(settings, llm=build_judge_llm(settings))
    reports = build_report_service(
        settings, brain, load_analysis(settings.fixture_analysis), llm=build_report_llm(settings)
    )
    return brain, reports


def build_fixture_brain(
    settings: Settings,
    *,
    llm: LLMClient | None,
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
        force_prepared=force_prepared or settings.brain_force_prepared or llm is None,
        quote_matcher=settings.quote_matcher(),
    )
