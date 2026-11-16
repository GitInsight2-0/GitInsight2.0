import asyncio
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

import gitinsight_api.tables  # noqa: F401  모든 테이블을 metadata에 등록합니다.
from gitinsight_api.db import create_engine, metadata

from api_test_support import ClientFactory, alembic_config, client_for_app

# psycopg 3 비동기 모드는 Windows 기본 루프(Proactor)를 지원하지 않습니다 (gitinsight_api.event_loop).
# 테스트도 서버와 같은 Selector 루프에서 돌게 합니다.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(autouse=True)
def no_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # 기본 테스트는 DB 없이 돕니다. 개발자 .env의 DATABASE_URL보다 환경 변수가 우선합니다.
    # 실제 Postgres 테스트는 TEST_DATABASE_URL과 -m postgres로 따로 돌립니다.
    monkeypatch.setenv("DATABASE_URL", "")


@pytest.fixture
def client_for() -> ClientFactory:
    return client_for_app


@pytest.fixture
def metrics_path(tmp_path: Path) -> Path:
    return tmp_path / "metrics.jsonl"


# ---- 실제 Postgres 테스트 공용 픽스처 (@pytest.mark.postgres) ----
# 비어 있는 테스트 전용 DB를 쓰세요. 마이그레이션을 올리고, 테스트마다 모든 테이블을 비웁니다.


@pytest.fixture
def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL이 없습니다")
    return url


@pytest.fixture
async def pg_engine(test_database_url: str) -> AsyncIterator[AsyncEngine]:
    """마이그레이션을 적용하고 모든 테이블을 비운 DB의 엔진입니다.

    tables.py에 테이블을 추가하면 따로 고치지 않아도 이 픽스처가 같이 비웁니다.
    """
    command.upgrade(alembic_config(test_database_url), "head")
    engine = create_engine(test_database_url)
    names = ", ".join(t.name for t in reversed(metadata.sorted_tables))
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    yield engine
    await engine.dispose()
