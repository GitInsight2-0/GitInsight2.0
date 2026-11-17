"""FastAPI 앱 팩토리."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncEngine

from gitinsight_api.analyses.postgres import PostgresAnalysisStore
from gitinsight_api.analyses.source import AnalysisUnavailableError, StoredAnalysisSource
from gitinsight_api.analyses.store import AnalysisStore
from gitinsight_api.brain.factory import build_server_services
from gitinsight_api.brain.protocol import Brain
from gitinsight_api.brain.scripted import ScriptedBrain
from gitinsight_api.db import create_engine
from gitinsight_api.metrics_store import JsonlMetricsStore, MetricsStore, PostgresMetricsStore
from gitinsight_api.report.service import ReportService
from gitinsight_api.routes import auth, metrics, reports, sessions
from gitinsight_api.settings import Settings
from gitinsight_contracts.fixtures import load_analysis


def default_services(
    settings: Settings, analyses: StoredAnalysisSource | None = None
) -> tuple[Brain, ReportService | None]:
    """실험 모드는 대본 면접 두뇌만, 그 밖에는 진짜 면접 두뇌와 리포트 서비스를 씁니다.

    analyses가 있으면 진짜 면접 두뇌와 리포트가 샘플 파일 대신 그 저장소의 분석 결과를 읽습니다.
    """
    if settings.lab_enabled:
        analysis = load_analysis(settings.fixture_analysis)
        return ScriptedBrain({analysis.analysis_id: analysis}), None
    return build_server_services(settings, analyses)


def create_app(
    settings: Settings | None = None,
    *,
    brain: Brain | None = None,
    report_service: ReportService | None = None,
    metrics_store: MetricsStore | None = None,
    analysis_store: AnalysisStore | None = None,
    github_http: httpx.AsyncClient | None = None,
) -> FastAPI:
    """brain을 넣으면 report_service도 넣은 그대로 씁니다. 테스트가 가짜 LLM 서비스를 넣을 때 씁니다.

    DATABASE_URL이 있으면 Postgres 엔진을 만들고(연결은 첫 쿼리 때), 서버를 끌 때 닫습니다.
    이때 실험 모드가 아니면 면접 재료도 analyses 테이블에서 읽고, 세션을 만들 때 분석 소유자를 확인합니다 (#52).
    analysis_store는 테스트가 메모리 저장소를 넣을 때 씁니다.
    github_http는 GitHub 로그인 호출용 HTTP 클라이언트입니다. 테스트는 가짜 응답 클라이언트를 넣고,
    넣지 않으면 새로 만들어 서버를 끌 때 닫습니다.
    """
    settings = settings or Settings()
    engine: AsyncEngine | None = None
    if settings.database_url is not None:
        engine = create_engine(settings.database_url.get_secret_value())
    if analysis_store is None and engine is not None:
        analysis_store = PostgresAnalysisStore(engine)
    # 실험 모드의 대본 면접 두뇌는 로그인 없이 샘플 파일만 씁니다.
    analyses = (
        StoredAnalysisSource(analysis_store)
        if analysis_store is not None and not settings.lab_enabled
        else None
    )
    if brain is None:
        brain, report_service = default_services(settings, analyses)
    if metrics_store is None:
        metrics_store = (
            PostgresMetricsStore(engine)
            if engine is not None
            else JsonlMetricsStore(settings.metrics_path)
        )

    owns_github_http = github_http is None
    if github_http is None:
        # 리다이렉트를 따라가지 않아, 토큰이 실린 요청이 다른 곳으로 넘어가지 않게 합니다.
        github_http = httpx.AsyncClient(follow_redirects=False)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if engine is not None:
            await engine.dispose()
        if owns_github_http:
            await github_http.aclose()

    app = FastAPI(title="GitInsight API", lifespan=lifespan)
    app.state.settings = settings
    app.state.db_engine = engine
    app.state.analyses = analyses
    app.state.brain = brain
    app.state.report_service = report_service
    app.state.metrics_store = metrics_store
    app.state.github_http = github_http

    origins = settings.cors_origin_list()
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
            allow_credentials=True,
        )

    @app.exception_handler(AnalysisUnavailableError)
    async def analysis_unavailable(_request: Request, _error: Exception) -> JSONResponse:
        # 세션 시작, 턴, 종료 어디서든 분석 결과를 읽지 못하면 같은 응답을 돌려줍니다.
        return JSONResponse(
            {"detail": "분석 결과를 읽지 못했습니다"},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        # DB 연결 확인은 T1(D)에서 추가합니다.
        return {"status": "ok"}

    auth.install_access_log_redaction()
    app.include_router(auth.router)
    app.include_router(sessions.router)
    app.include_router(reports.router)
    app.include_router(metrics.router)
    return app


def app_factory() -> FastAPI:
    """uvicorn --factory 진입점입니다."""
    return create_app()
