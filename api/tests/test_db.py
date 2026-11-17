"""DB 연결 설정과 마이그레이션 테스트. 실제 DB 없이 돕니다."""

import asyncio
import io
import logging
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr

from gitinsight_api import event_loop
from gitinsight_api.db import (
    CONNECT_TIMEOUT_S,
    DRIVER,
    DatabaseUrlError,
    create_engine,
    normalize_url,
)
from gitinsight_api.main import create_app
from gitinsight_api.metrics_store import (
    JsonlMetricsStore,
    MetricsStoreUnavailableError,
    PostgresMetricsStore,
)
from gitinsight_api.settings import Settings
from gitinsight_api.tables import metric_events
from gitinsight_contracts.metrics import MetricEvent, MetricName

from api_test_support import ALEMBIC_INI, ClientFactory

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


def test_metric_event_fields_match_table_columns() -> None:
    # 계약에 필드가 늘면 insert가 깨져 /api/metrics가 계속 503이 됩니다. alembic check로는 잡히지 않습니다.
    columns = {c.name for c in metric_events.columns} - {"id", "received_at"}
    assert set(MetricEvent.model_fields) == columns


def event() -> MetricEvent:
    return MetricEvent(name=MetricName.FIRST_AUDIO, session_id=uuid4(), at_ms=1, value_ms=320.5)


async def test_unreachable_database_raises_without_url(
    metrics_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(Settings(metrics_path=metrics_path, database_url=SecretStr(UNREACHABLE)))
    with caplog.at_level(logging.WARNING), pytest.raises(MetricsStoreUnavailableError) as error:
        await app.state.metrics_store.append(event())
    await app.state.db_engine.dispose()
    # 연결 거부(OperationalError)여야 합니다. Windows Proactor 루프 문제(InterfaceError)로 통과하지 않게 확인합니다.
    assert str(error.value) == "OperationalError"
    assert "s3cret-pw" not in caplog.text
    # DB 장애를 JSONL로 대신 받지 않습니다.
    assert not metrics_path.exists()


async def test_engine_passes_connect_timeout_to_psycopg(monkeypatch: pytest.MonkeyPatch) -> None:
    # 제한 시간이 없으면 응답 없는 DB 호스트에 OS 기본값(Windows 약 130초)만큼 매달립니다.
    seen: dict[str, object] = {}

    async def fake_connect(*_args: object, **kwargs: object) -> object:
        seen.update(kwargs)
        raise OSError("stop before connecting")

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", fake_connect)
    engine = create_engine(UNREACHABLE)
    with pytest.raises(OSError):
        async with engine.connect():
            pass
    await engine.dispose()
    assert seen.get("connect_timeout") == CONNECT_TIMEOUT_S


async def test_metrics_route_returns_503_when_store_fails(
    client_for: ClientFactory, metrics_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(Settings(metrics_path=metrics_path, database_url=SecretStr(UNREACHABLE)))
    with caplog.at_level(logging.WARNING):
        async with client_for(app) as client:
            response = await client.post(
                "/api/metrics", json={"name": "first_audio", "at_ms": 1, "value_ms": 320.5}
            )
    await app.state.db_engine.dispose()
    assert response.status_code == 503
    assert "metrics_store_failed error=OperationalError" in caplog.text


def test_alembic_ini_is_ascii() -> None:
    # configparser는 OS 기본 인코딩으로 읽습니다. 한국어 Windows(cp949)에서 깨지지 않게 ASCII만 둡니다.
    assert ALEMBIC_INI.read_bytes().isascii()


def test_migrations_render_offline_without_database() -> None:
    # --sql 모드는 DB에 연결하지 않고 SQL만 만듭니다. CI 기본 실행에서 마이그레이션 문법을 확인합니다.
    buffer = io.StringIO()
    config = Config(str(ALEMBIC_INI), output_buffer=buffer)
    command.upgrade(config, "head", sql=True)
    sql = buffer.getvalue()
    assert "CREATE TABLE metric_events" in sql
    assert "UPDATE alembic_version SET" in sql or "INSERT INTO alembic_version" in sql


def test_server_loop_factory_uses_selector_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(event_loop.sys, "platform", "win32")
    loop = event_loop.server_loop_factory()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()


def test_server_loop_factory_runs_coroutines() -> None:
    loop = event_loop.server_loop_factory()
    try:
        assert loop.run_until_complete(asyncio.sleep(0, result="ok")) == "ok"
    finally:
        loop.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 전용")
async def test_tests_run_on_selector_loop_on_windows() -> None:
    assert isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop)
