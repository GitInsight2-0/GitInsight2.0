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

from gitinsight_api.analyses.postgres import SNAPSHOT_REPLACED_ERROR, PostgresAnalysisStore
from gitinsight_api.analyses.store import AnalysisKey
from gitinsight_api.main import create_app
from gitinsight_api.metrics_store import PostgresMetricsStore
from gitinsight_api.settings import Settings
from gitinsight_api.tables import metric_events
from gitinsight_contracts.api import AnalysisStatus
from gitinsight_contracts.fixtures import load_analysis
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


async def test_analysis_store_uses_database_clock(pg_engine: AsyncEngine) -> None:
    # 운영 경로입니다. 시계를 넘기지 않으면 updated_at과 오래됨 판단에 DB now()를 씁니다.
    # 규칙 자체는 test_analysis_store.py의 계약 테스트가 가짜 시계로 확인합니다.
    sample = load_analysis()
    store = PostgresAnalysisStore(pg_engine)
    key = AnalysisKey(sample.repo_full_name, sample.commit_sha, "u1")

    record, created = await store.get_or_create(key)
    again, created_again = await store.get_or_create(key)
    await store.save_result(
        record.analysis_id, sample.model_copy(update={"analysis_id": record.analysis_id})
    )

    assert created and not created_again
    assert again.analysis_id == record.analysis_id
    saved = await store.get_record(record.analysis_id)
    assert saved is not None and saved.state.status is AnalysisStatus.DONE
    assert saved.updated_at >= record.updated_at
    result = await store.get(record.analysis_id)
    assert result is not None and result.analysis_id == record.analysis_id


async def test_demo_snapshot_is_idempotent_and_reused(pg_engine: AsyncEngine) -> None:
    sample = load_analysis()
    store = PostgresAnalysisStore(pg_engine)
    key = AnalysisKey(sample.repo_full_name, sample.commit_sha, "demo")
    # 같은 키로 진행 중이던 기록이 있어도 스냅샷이 이깁니다.
    running, _ = await store.get_or_create(key)

    first = await store.save_snapshot(sample, "demo")
    second = await store.save_snapshot(sample, "demo")

    assert first.analysis_id == second.analysis_id == sample.analysis_id
    assert second.key == key and second.state.status is AnalysisStatus.DONE
    assert await store.get(sample.analysis_id) == sample
    old = await store.get_record(running.analysis_id)
    assert old is not None
    assert (old.state.status, old.state.error) == (AnalysisStatus.FAILED, SNAPSHOT_REPLACED_ERROR)
    # 같은 키를 다시 요청하면 새로 분석하지 않고 스냅샷을 재사용합니다.
    reused, created = await store.get_or_create(key)
    assert (reused.analysis_id, created) == (sample.analysis_id, False)
