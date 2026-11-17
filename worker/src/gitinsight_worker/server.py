"""분석까지 여는 API 서버 진입점입니다 (#12).

uv run uvicorn --factory gitinsight_worker.server:app_factory --loop gitinsight_api.event_loop:server_loop_factory

워커 패키지가 API 패키지에 의존하므로, 분석 실행기(InProcessLauncher)를 넣은 서버는 여기서 만듭니다.
gitinsight_api.main:app_factory로 띄우면 분석 요청 API만 503이고 나머지는 같습니다.
분석은 DATABASE_URL이 있고 실험 모드가 아닐 때만 엽니다.
"""

from fastapi import FastAPI

from gitinsight_api.analyses.store import AnalysisStore
from gitinsight_api.brain.factory import build_analysis_llm
from gitinsight_api.main import create_app
from gitinsight_api.settings import Settings
from gitinsight_worker.jobs import InProcessLauncher


def create_server(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    def launcher_for(store: AnalysisStore) -> InProcessLauncher:
        # 모델은 클라이언트가 설정(LLM_ANALYSIS_MODEL)으로 정합니다.
        return InProcessLauncher(store, build_analysis_llm(settings))

    return create_app(settings, launcher_factory=launcher_for)


def app_factory() -> FastAPI:
    """uvicorn --factory 진입점입니다."""
    return create_server()
