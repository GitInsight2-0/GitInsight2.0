"""실제 Postgres 테스트. 기본 실행에서 빠집니다.

실행: TEST_DATABASE_URL=postgresql://user@localhost:5432/gitinsight_test uv run pytest -m postgres
공용 픽스처(test_database_url, pg_engine)는 api/tests/conftest.py에 있습니다.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from gitinsight_api.main import create_app
from gitinsight_api.metrics_store import PostgresMetricsStore
from gitinsight_api.settings import Settings
from gitinsight_api.tables import metric_events
from gitinsight_contracts.metrics import MetricEvent, MetricName

from api_test_support import ClientFactory, alembic_config

pytestmark = pytest.mark.postgres


def test_migrations_round_trip(test_database_url: str) -> None:
    config = alembic_config(test_database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    # 모델(tables.py)과 마이그레이션이 어긋나면 실패합니다.
    command.check(config)


async def test_postgres_store_saves_event(pg_engine: AsyncEngine) -> None:
    session_id, turn_id = uuid4(), uuid4()
    await PostgresMetricsStore(pg_engine).append(
        MetricEvent(
            name=MetricName.FIRST_AUDIO,
            session_id=session_id,
            turn_id=turn_id,
            at_ms=1_794_000_000_000,
            value_ms=320.5,
            provider="gemini",
            scenario="s1",
        )
    )
    async with pg_engine.connect() as conn:
        rows = (await conn.execute(select(metric_events))).mappings().all()
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "first_audio"
    assert (row["session_id"], row["turn_id"]) == (session_id, turn_id)
    assert (row["at_ms"], row["value_ms"]) == (1_794_000_000_000, 320.5)
    assert (row["provider"], row["scenario"], row["error_code"]) == ("gemini", "s1", None)
    assert row["received_at"] is not None


async def test_metrics_route_writes_to_postgres(
    client_for: ClientFactory, test_database_url: str, pg_engine: AsyncEngine, tmp_path: Path
) -> None:
    settings = Settings(
        metrics_path=tmp_path / "m.jsonl", database_url=SecretStr(test_database_url)
    )
    app = create_app(settings)
    async with client_for(app) as client:
        response = await client.post("/api/metrics", json={"name": "barge_in", "at_ms": 5})
    await app.state.db_engine.dispose()
    assert response.status_code == 204
    async with pg_engine.connect() as conn:
        names = (await conn.execute(select(metric_events.c.name))).scalars().all()
    assert names == ["barge_in"]
    assert not (tmp_path / "m.jsonl").exists()
