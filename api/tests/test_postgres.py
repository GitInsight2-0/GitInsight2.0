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
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from gitinsight_api.analyses.postgres import SNAPSHOT_REPLACED_ERROR, PostgresAnalysisStore
from gitinsight_api.analyses.source import StoredAnalysisSource
from gitinsight_api.analyses.store import AnalysisKey
from gitinsight_api.auth import CurrentUser, get_current_user
from gitinsight_api.brain.postgres import PostgresSessionRepository
from gitinsight_api.brain.service import InterviewBrain
from gitinsight_api.db import CONNECT_ARGS, normalize_url
from gitinsight_api.llm.fake import FakeLLM
from gitinsight_api.main import create_app
from gitinsight_api.metrics_store import PostgresMetricsStore
from gitinsight_api.settings import Settings
from gitinsight_api.tables import metric_events
from gitinsight_contracts.api import AnalysisStatus
from gitinsight_contracts.fixtures import load_analysis
from gitinsight_contracts.interview import (
    InterviewMode,
    SessionCreate,
    TurnRequest,
    TurnSource,
)
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


def test_owner_migration_fills_existing_reports_from_their_sessions(
    test_database_url: str,
) -> None:
    # 0005는 이미 저장된 리포트의 owner_id를 세션 상태의 소유자로 채웁니다 (#77).
    import psycopg

    config = alembic_config(test_database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "0004")
    owned, unowned = uuid4(), uuid4()
    with psycopg.connect(test_database_url, autocommit=True) as conn:
        for session_id, state in ((owned, '{"owner_id": "alice"}'), (unowned, "{}")):
            conn.execute(
                "INSERT INTO interview_sessions (session_id, state) VALUES (%s, %s::jsonb)",
                (session_id, state),
            )
            conn.execute(
                "INSERT INTO reports (report_id, session_id, report) VALUES (%s, %s, '{}'::jsonb)",
                (uuid4(), session_id),
            )

        command.upgrade(config, "head")

        rows = conn.execute("SELECT session_id, owner_id FROM reports").fetchall()
        conn.execute("TRUNCATE reports, interview_sessions CASCADE")
    assert dict(rows) == {owned: "alice", unowned: None}


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


async def test_session_starts_from_seeded_snapshot_for_its_owner_only(
    client_for: ClientFactory, test_database_url: str, pg_engine: AsyncEngine, tmp_path: Path
) -> None:
    # 데모 경로입니다 (#52). 스냅샷을 넣은 계정만 그 분석으로 면접을 시작합니다.
    sample = load_analysis()
    await PostgresAnalysisStore(pg_engine).save_snapshot(sample, "demo")
    settings = Settings(
        lab_enabled=False,
        metrics_path=tmp_path / "m.jsonl",
        brain_force_prepared=True,
        database_url=SecretStr(test_database_url),
    )
    app = create_app(settings)
    body = {"analysis_id": sample.analysis_id, "mode": "text"}
    async with client_for(app) as client:
        app.dependency_overrides[get_current_user] = lambda: CurrentUser("demo", "demo")
        created = await client.post("/api/sessions", json=body)
        session_id = created.json()["session_id"]
        app.dependency_overrides[get_current_user] = lambda: CurrentUser("other", "other")
        other = await client.post("/api/sessions", json=body)
    await app.state.db_engine.dispose()

    assert created.status_code == 201, created.text
    assert other.status_code == 404

    # 서버를 다시 시작해도 같은 세션이 이어집니다 (#53). 세션과 턴 기록이 DB에 있기 때문입니다.
    restarted = create_app(settings)
    restarted.dependency_overrides[get_current_user] = lambda: CurrentUser("demo", "demo")
    turn = {"turn_id": str(uuid4()), "transcript": "답변입니다", "source": "text"}
    async with client_for(restarted) as client:
        first = await client.post(f"/api/sessions/{session_id}/turns", json=turn)
        again = await client.post(f"/api/sessions/{session_id}/turns", json=turn)
        ended = await client.post(f"/api/sessions/{session_id}/end")
        report = await client.get(f"/api/reports/{ended.json()['report_id']}")
        closed = await client.post(
            f"/api/sessions/{session_id}/turns", json={**turn, "turn_id": str(uuid4())}
        )
        restarted.dependency_overrides[get_current_user] = lambda: CurrentUser("other", "other")
        forbidden = await client.post(f"/api/sessions/{session_id}/turns", json=turn)
    await restarted.state.db_engine.dispose()

    # 한 번 더 다시 시작해도 같은 리포트를 다시 엽니다 (#75). 리포트도 DB에 있기 때문입니다.
    third = create_app(settings)
    third.dependency_overrides[get_current_user] = lambda: CurrentUser("demo", "demo")
    async with client_for(third) as client:
        reopened = await client.get(f"/api/reports/{ended.json()['report_id']}")
        ended_again = await client.post(f"/api/sessions/{session_id}/end")
        third.dependency_overrides[get_current_user] = lambda: CurrentUser("other", "other")
        not_mine = await client.get(f"/api/reports/{ended.json()['report_id']}")
    await third.state.db_engine.dispose()

    assert first.status_code == 200, first.text
    # 같은 turn_id는 저장된 응답을 그대로 돌려줍니다.
    assert again.json() == first.json()
    assert ended.status_code == 200, ended.text
    assert [t["turn_id"] for t in report.json()["turns"]] == [turn["turn_id"]]
    assert closed.status_code == 409
    assert forbidden.status_code == 403
    assert reopened.status_code == 200, reopened.text
    assert reopened.json() == report.json()
    assert ended_again.json() == ended.json()
    assert not_mine.status_code == 403


async def test_turn_after_restart_needs_only_one_connection(
    test_database_url: str, pg_engine: AsyncEngine
) -> None:
    # 연결이 하나뿐인 풀에서, 서버를 다시 시작한 직후(분석 캐시가 빈 상태)에도 턴이 처리됩니다 (PR #57 리뷰).
    # 세션 잠금을 잡은 채 분석을 읽으면 두 번째 연결을 기다리다 풀 대기 시간이 넘습니다.
    sample = load_analysis()
    await PostgresAnalysisStore(pg_engine).save_snapshot(sample, "demo")
    single = create_async_engine(
        normalize_url(test_database_url),
        pool_size=1,
        max_overflow=0,
        pool_timeout=2,
        connect_args=CONNECT_ARGS,
    )

    def server() -> InterviewBrain:
        # 서버 하나에 해당합니다. 분석 캐시는 서버마다 새로 시작합니다.
        return InterviewBrain(
            analyses=StoredAnalysisSource(PostgresAnalysisStore(single)),
            repository=PostgresSessionRepository(single),
            llm=FakeLLM(),
            force_prepared=True,
        )

    try:
        session_id, _ = await server().start(
            SessionCreate(analysis_id=sample.analysis_id, mode=InterviewMode.TEXT)
        )
        reply = await server().handle_turn(
            session_id,
            TurnRequest(turn_id=uuid4(), transcript="답변입니다", source=TurnSource.TEXT),
        )
    finally:
        await single.dispose()

    assert reply.turn_id is not None and not reply.done
