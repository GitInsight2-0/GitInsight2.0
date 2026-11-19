"""DB 오류 응답과 시작 때 마이그레이션 확인 (#101). DB 없이 예외를 흉내 냅니다."""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from gitinsight_api.analyses.store import AnalysisRecord, InMemoryAnalysisStore
from gitinsight_api.auth import CurrentUser, get_current_user
from gitinsight_api.main import create_app
from gitinsight_api.schema_check import SchemaStatus, check_schema, head_revision
from gitinsight_api.settings import Settings

from api_test_support import ClientFactory

# 예외 메시지에 섞일 수 있는 접속 주소와 쿼리입니다. 응답과 로그에 나오면 안 됩니다.
SECRET_HOST = "db-secret-host.internal"
UNREACHABLE = "postgresql://gi:s3cret-pw@127.0.0.1:1/gitinsight"


def missing_table() -> ProgrammingError:
    return ProgrammingError(
        f"SELECT * FROM analyses -- {SECRET_HOST}",
        {},
        Exception('relation "analyses" does not exist'),
    )


def connection_refused() -> OperationalError:
    return OperationalError(f"connect {SECRET_HOST}", {}, Exception("connection refused"))


class FailingStore(InMemoryAnalysisStore):
    """기록을 읽을 때 정해 둔 DB 오류를 냅니다."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    async def get_record(self, analysis_id: str) -> AnalysisRecord | None:
        raise self.error


def stored_app(metrics_path: Path, store: InMemoryAnalysisStore) -> FastAPI:
    app = create_app(
        Settings(lab_enabled=False, metrics_path=metrics_path, brain_force_prepared=True),
        analysis_store=store,
    )
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("u1", "u1")
    return app


async def test_schema_error_returns_500_with_reason_and_no_leak(
    client_for: ClientFactory, metrics_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    app = stored_app(metrics_path, FailingStore(missing_table()))
    with caplog.at_level(logging.WARNING):
        async with client_for(app) as client:
            response = await client.post(
                "/api/sessions", json={"analysis_id": "a1", "mode": "text"}
            )

    # 다시 보내도 같은 오류라 503이 아닙니다. 웹은 503만 다시 시도합니다.
    assert response.status_code == 500
    assert response.json() == {
        "detail": "데이터베이스 오류로 처리하지 못했습니다. 관리자에게 알려 주세요"
    }
    assert "database_error error=ProgrammingError" in caplog.text
    assert SECRET_HOST not in caplog.text
    assert SECRET_HOST not in response.text
    assert "analyses" not in response.text


async def test_connection_error_still_returns_503(
    client_for: ClientFactory, metrics_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    app = stored_app(metrics_path, FailingStore(connection_refused()))
    with caplog.at_level(logging.WARNING):
        async with client_for(app) as client:
            response = await client.post(
                "/api/sessions", json={"analysis_id": "a1", "mode": "text"}
            )

    assert response.status_code == 503
    assert response.json() == {"detail": "분석 결과를 읽지 못했습니다"}
    assert "analysis_source_failed error=OperationalError" in caplog.text
    assert SECRET_HOST not in caplog.text


# ---- 시작 때 마이그레이션 확인 ----


class FakeConnection:
    def __init__(self, revision: str | None) -> None:
        self.revision = revision

    async def run_sync(self, _fn: Callable[..., Any]) -> str | None:
        return self.revision


class FakeEngine:
    """connect()로 정해 둔 마이그레이션 번호를 돌려주거나 연결 오류를 냅니다."""

    def __init__(self, revision: str | None = None, error: Exception | None = None) -> None:
        self.revision = revision
        self.error = error

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[FakeConnection]:
        if self.error is not None:
            raise self.error
        yield FakeConnection(self.revision)


def fake_engine(revision: str | None = None, error: Exception | None = None) -> AsyncEngine:
    return cast(AsyncEngine, FakeEngine(revision, error))


def test_head_revision_is_last_migration_file() -> None:
    versions = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    last = max(p.name.split("_", 1)[0] for p in versions.glob("[0-9]*_*.py"))
    assert head_revision() == last


async def test_missing_migrations_are_logged_as_error(caplog: pytest.LogCaptureFixture) -> None:
    head = head_revision()
    with caplog.at_level(logging.INFO):
        status = await check_schema(fake_engine(revision=None))

    assert status is SchemaStatus.NOT_MIGRATED
    [record] = [r for r in caplog.records if "schema_not_migrated" in r.getMessage()]
    assert record.levelno == logging.ERROR
    assert f"head={head}" in record.getMessage()
    assert "alembic -c api/alembic.ini upgrade head" in record.getMessage()


async def test_outdated_migrations_are_logged_as_error(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        status = await check_schema(fake_engine(revision="0001"))

    assert status is SchemaStatus.OUTDATED
    assert f"schema_outdated current=0001 head={head_revision()}" in caplog.text


async def test_current_schema_is_info(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        status = await check_schema(fake_engine(revision=head_revision()))

    assert status is SchemaStatus.CURRENT
    assert all(r.levelno < logging.WARNING for r in caplog.records)


async def test_unreachable_database_does_not_block_startup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO):
        status = await check_schema(fake_engine(error=connection_refused()))

    assert status is SchemaStatus.UNKNOWN
    assert "schema_check_failed error=OperationalError" in caplog.text
    assert SECRET_HOST not in caplog.text


async def test_missing_migrations_dir_is_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        status = await check_schema(fake_engine(revision=None), tmp_path / "none")

    assert status is SchemaStatus.UNKNOWN
    assert "schema_check_skipped" in caplog.text


async def test_server_starts_and_logs_when_database_is_unreachable(
    metrics_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(Settings(metrics_path=metrics_path, database_url=SecretStr(UNREACHABLE)))
    with caplog.at_level(logging.INFO):
        async with app.router.lifespan_context(app):
            pass

    assert "schema_check_failed error=OperationalError" in caplog.text
    assert "s3cret-pw" not in caplog.text


@pytest.mark.postgres
async def test_migrated_database_is_current(
    pg_engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        status = await check_schema(pg_engine)

    assert status is SchemaStatus.CURRENT
