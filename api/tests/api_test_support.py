"""API 테스트 공용 도구. pytest 설정의 pythonpath로 테스트 모듈에서 import합니다."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

import httpx
from alembic.config import Config
from fastapi import FastAPI

ClientFactory = Callable[[FastAPI], AbstractAsyncContextManager[httpx.AsyncClient]]

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def alembic_config(database_url: str | None = None) -> Config:
    """api/alembic.ini 설정입니다. database_url을 주면 DATABASE_URL 대신 그 DB를 씁니다."""
    config = Config(str(ALEMBIC_INI))
    if database_url:
        config.attributes["database_url"] = database_url
    return config


@asynccontextmanager
async def client_for_app(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
