"""FastAPI 앱 팩토리."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from gitinsight_api.brain.protocol import Brain
from gitinsight_api.brain.scripted import ScriptedBrain
from gitinsight_api.metrics_store import JsonlMetricsStore, MetricsStore
from gitinsight_api.routes import metrics, sessions
from gitinsight_api.settings import Settings
from gitinsight_contracts.fixtures import load_analysis


def default_brain(settings: Settings) -> Brain | None:
    if settings.lab_enabled:
        analysis = load_analysis(settings.fixture_analysis)
        return ScriptedBrain({analysis.analysis_id: analysis})
    return None


def create_app(
    settings: Settings | None = None,
    *,
    brain: Brain | None = None,
    metrics_store: MetricsStore | None = None,
) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="GitInsight API")
    app.state.settings = settings
    app.state.brain = brain if brain is not None else default_brain(settings)
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
    app.include_router(metrics.router)
    return app


def app_factory() -> FastAPI:
    """uvicorn --factory 진입점입니다."""
    return create_app()
