"""FastAPI 앱 팩토리."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from gitinsight_api.brain.factory import build_server_services
from gitinsight_api.brain.protocol import Brain
from gitinsight_api.brain.scripted import ScriptedBrain
from gitinsight_api.metrics_store import JsonlMetricsStore, MetricsStore
from gitinsight_api.report.service import ReportService
from gitinsight_api.routes import metrics, reports, sessions
from gitinsight_api.settings import Settings
from gitinsight_contracts.fixtures import load_analysis


def default_services(settings: Settings) -> tuple[Brain, ReportService | None]:
    """실험 모드는 대본 면접 두뇌만, 그 밖에는 진짜 면접 두뇌와 리포트 서비스를 씁니다."""
    if settings.lab_enabled:
        analysis = load_analysis(settings.fixture_analysis)
        return ScriptedBrain({analysis.analysis_id: analysis}), None
    return build_server_services(settings)


def create_app(
    settings: Settings | None = None,
    *,
    brain: Brain | None = None,
    report_service: ReportService | None = None,
    metrics_store: MetricsStore | None = None,
) -> FastAPI:
    """brain을 넣으면 report_service도 넣은 그대로 씁니다. 테스트가 가짜 LLM 서비스를 넣을 때 씁니다."""
    settings = settings or Settings()
    if brain is None:
        brain, report_service = default_services(settings)
    app = FastAPI(title="GitInsight API")
    app.state.settings = settings
    app.state.brain = brain
    app.state.report_service = report_service
    app.state.metrics_store = metrics_store or JsonlMetricsStore(settings.metrics_path)

    origins = settings.cors_origin_list()
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
            allow_credentials=True,
        )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        # DB 연결 확인은 T1(D)에서 추가합니다.
        return {"status": "ok"}

    app.include_router(sessions.router)
    app.include_router(reports.router)
    app.include_router(metrics.router)
    return app


def app_factory() -> FastAPI:
    """uvicorn --factory 진입점입니다."""
    return create_app()
