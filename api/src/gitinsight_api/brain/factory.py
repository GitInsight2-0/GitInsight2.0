"""설정으로 면접 두뇌와 LLM 클라이언트를 만듭니다. CLI와 평가 스크립트가 같이 씁니다."""

from gitinsight_api.brain.analysis_source import AnalysisSource, FixtureAnalysisSource
from gitinsight_api.brain.planner import RevisitSource
from gitinsight_api.brain.repository import InMemorySessionRepository, SessionRepository
from gitinsight_api.brain.service import InterviewBrain
from gitinsight_api.llm import CostRecorder, LiteLLMClient, LLMClient
from gitinsight_api.report.builder import ReportBuilder
from gitinsight_api.report.code_map import ReportHistory
from gitinsight_api.report.service import InMemoryReportStore, ReportService, ReportStore
from gitinsight_api.settings import Settings
from gitinsight_contracts.analysis import AnalysisResult
from gitinsight_contracts.fixtures import load_analysis


def build_judge_llm(settings: Settings, cost_recorder: CostRecorder | None = None) -> LiteLLMClient:
    return LiteLLMClient(
        settings.llm_judge_model,
        cost_recorder=cost_recorder,
        reasoning_effort=settings.llm_judge_reasoning_effort,
    )


def build_analysis_llm(
    settings: Settings, cost_recorder: CostRecorder | None = None
) -> LiteLLMClient:
    """분석 워커의 2단계(주제 생성)용 클라이언트입니다."""
    return LiteLLMClient(
        settings.llm_analysis_model,
        cost_recorder=cost_recorder,
        reasoning_effort=settings.llm_analysis_reasoning_effort,
    )


def build_report_llm(
    settings: Settings, cost_recorder: CostRecorder | None = None
) -> LiteLLMClient:
    return LiteLLMClient(
        settings.llm_report_model,
        cost_recorder=cost_recorder,
        reasoning_effort=settings.llm_report_reasoning_effort,
    )


def build_report_service(
    settings: Settings,
    brain: InterviewBrain,
    analysis: AnalysisResult,
    *,
    llm: LLMClient | None,
    force_prepared: bool = False,
    analyses: AnalysisSource | None = None,
    store: ReportStore | None = None,
) -> ReportService:
    """llm이 없으면 LLM 없이 턴별 판단 표 리포트만 만듭니다.

    analyses를 주면 analysis 대신 그 저장소(예: Postgres 분석 기록)에서 결과를 읽습니다.
    store를 주면 리포트를 그 저장소에 두고, 없으면 메모리에 둡니다.
    """
    from gitinsight_api.llm.fake import FakeLLM

    return ReportService(
        analyses=analyses if analyses is not None else FixtureAnalysisSource(analysis),
        sessions=brain.repository,
        store=store if store is not None else InMemoryReportStore(),
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


def build_server_services(
    settings: Settings,
    analyses: AnalysisSource | None = None,
    sessions: SessionRepository | None = None,
    report_store: ReportStore | None = None,
) -> tuple[InterviewBrain, ReportService]:
    """HTTP 서버용 면접 두뇌와 리포트 서비스를 만듭니다.

    LLM 클라이언트는 만들기만 하고 부르지 않으므로 서버 시작 때 LLM이나 네트워크를 쓰지 않습니다.
    analyses를 주면 두뇌와 리포트가 그 저장소에서 분석 결과를 읽고, 없으면 샘플 파일을 읽습니다.
    sessions를 주면 세션과 턴 기록을 그 저장소에 두고, 없으면 메모리에 둡니다.
    report_store를 주면 리포트를 그 저장소에 두고, 없으면 메모리에 둡니다.
    """
    if analyses is None:
        analyses = FixtureAnalysisSource(load_analysis(settings.fixture_analysis))
    if report_store is None:
        report_store = InMemoryReportStore()
    brain = build_fixture_brain(
        settings,
        llm=build_judge_llm(settings),
        analyses=analyses,
        repository=sessions,
        # 면접 두뇌가 같은 리포트 저장소에서 지난 코드 지도를 읽어 다시 물을 주제를 고릅니다 (#107).
        revisit=ReportHistory(report_store, analyses),
    )
    reports = build_report_service(
        settings,
        brain,
        load_analysis(settings.fixture_analysis),
        llm=build_report_llm(settings),
        analyses=analyses,
        store=report_store,
    )
    return brain, reports


def build_fixture_brain(
    settings: Settings,
    *,
    llm: LLMClient | None,
    fixture: str | None = None,
    force_prepared: bool = False,
    analyses: AnalysisSource | None = None,
    repository: SessionRepository | None = None,
    revisit: RevisitSource | None = None,
) -> InterviewBrain:
    """샘플 분석 결과와 메모리 저장소로 면접 두뇌를 만듭니다. llm이 없으면 미리 만든 질문만 씁니다.

    analyses를 주면 샘플 파일 대신 그 저장소(예: Postgres 분석 기록)에서 면접 재료를 읽습니다.
    repository를 주면 메모리 대신 그 저장소(예: Postgres)에 세션을 둡니다.
    revisit을 주면 같은 레포로 다시 보는 면접에서 지난 면접에 막혔던 함수부터 묻습니다.
    """
    from gitinsight_api.llm.fake import FakeLLM

    if analyses is None:
        analyses = FixtureAnalysisSource(load_analysis(fixture or settings.fixture_analysis))
    return InterviewBrain(
        analyses=analyses,
        repository=repository if repository is not None else InMemorySessionRepository(),
        llm=llm if llm is not None else FakeLLM(),
        judge_timeout_s=settings.judge_timeout_s,
        plan_timeout_s=settings.plan_timeout_s,
        judge_model=settings.llm_judge_model,
        plan_model=settings.llm_analysis_model,
        force_prepared=force_prepared or settings.brain_force_prepared or llm is None,
        quote_matcher=settings.quote_matcher(),
        revisit=revisit,
    )
