"""DB 연결 설정과 마이그레이션 테스트. 실제 DB 없이 돕니다."""

import io
import logging
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr

from gitinsight_api.db import DRIVER, DatabaseUrlError, normalize_url
from gitinsight_api.main import create_app
from gitinsight_api.metrics_store import (
    JsonlMetricsStore,
    MetricsStoreUnavailableError,
    PostgresMetricsStore,
)
from gitinsight_api.settings import Settings
from gitinsight_contracts.metrics import MetricEvent, MetricName

from api_test_support import ClientFactory

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
# 아무도 듣지 않는 포트라 연결이 바로 거부됩니다.
UNREACHABLE = "postgresql://gi:s3cret-pw@127.0.0.1:1/gitinsight"


@pytest.mark.parametrize(
    "raw",
    [
        "postgres://u:p@db.example:5432/app",
        "postgresql://u:p@db.example:5432/app",
        "postgresql+psycopg://u:p@db.example:5432/app",
    ],
)
def test_postgres_urls_use_psycopg_driver(raw: str) -> None:
    url = normalize_url(raw)
    assert url.drivername == DRIVER
    assert (url.host, url.port, url.database) == ("db.example", 5432, "app")


@pytest.mark.parametrize(
    "raw", ["mysql://u:p@db.example/app", "sqlite:///app.db", "postgresql+asyncpg://u:p@h/app"]
)
def test_other_urls_are_rejected_without_leaking_password(raw: str) -> None:
    with pytest.raises(DatabaseUrlError) as error:
        normalize_url(raw)
    assert "u:p" not in str(error.value)


def test_database_url_is_secret_and_empty_means_unset() -> None:
    settings = Settings(database_url=SecretStr(UNREACHABLE))
    assert "s3cret-pw" not in repr(settings)
    assert Settings(database_url=SecretStr("")).database_url is None
    assert Settings(database_url=SecretStr("  ")).database_url is None


def test_app_uses_jsonl_without_database_url(metrics_path: Path) -> None:
    app = create_app(Settings(metrics_path=metrics_path))
    assert isinstance(app.state.metrics_store, JsonlMetricsStore)
    assert app.state.db_engine is None


def test_app_uses_postgres_with_database_url(metrics_path: Path) -> None:
    # 엔진은 만들 때 연결하지 않으므로 DB가 없어도 앱이 뜹니다.
    app = create_app(Settings(metrics_path=metrics_path, database_url=SecretStr(UNREACHABLE)))
    assert isinstance(app.state.metrics_store, PostgresMetricsStore)
    assert app.state.db_engine is not None


def event() -> MetricEvent:
    return MetricEvent(name=MetricName.FIRST_AUDIO, session_id=uuid4(), at_ms=1, value_ms=320.5)


async def test_unreachable_database_raises_without_url(
    metrics_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(Settings(metrics_path=metrics_path, database_url=SecretStr(UNREACHABLE)))
    with caplog.at_level(logging.WARNING), pytest.raises(MetricsStoreUnavailableError) as error:
        await app.state.metrics_store.append(event())
    await app.state.db_engine.dispose()
    assert "s3cret-pw" not in str(error.value)
    assert "s3cret-pw" not in caplog.text
    # DB 장애를 JSONL로 대신 받지 않습니다.
    assert not metrics_path.exists()


async def test_metrics_route_returns_503_when_store_fails(
    client_for: ClientFactory, metrics_path: Path
) -> None:
    app = create_app(Settings(metrics_path=metrics_path, database_url=SecretStr(UNREACHABLE)))
    async with client_for(app) as client:
        response = await client.post(
            "/api/metrics", json={"name": "first_audio", "at_ms": 1, "value_ms": 320.5}
        )
    await app.state.db_engine.dispose()
    assert response.status_code == 503


def test_migrations_render_offline_without_database() -> None:
    # --sql 모드는 DB에 연결하지 않고 SQL만 만듭니다. CI 기본 실행에서 마이그레이션 문법을 확인합니다.
    buffer = io.StringIO()
    config = Config(str(ALEMBIC_INI), output_buffer=buffer)
    command.upgrade(config, "head", sql=True)
    sql = buffer.getvalue()
    assert "CREATE TABLE metric_events" in sql
    assert "UPDATE alembic_version SET" in sql or "INSERT INTO alembic_version" in sql
