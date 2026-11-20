"""분석 기록 저장소의 Postgres 구현 (#12). 규칙은 store.py의 AnalysisStore와 메모리 구현과 같습니다.

- 같은 키의 동시 요청: failed가 아닌 기록에 거는 부분 UNIQUE 인덱스(uq_analyses_reusable_key)와
  INSERT … ON CONFLICT DO NOTHING으로 기록 하나만 만듭니다.
- 오래된 진행 중 기록: 같은 트랜잭션에서 먼저 failed(StaleAnalysis)로 닫아 인덱스에서 빼고 새 기록을 만듭니다.
- 시각: 운영에서는 DB 시계(now())만 씁니다. 서버와 워커가 다른 기기에서 돌면 앱 시계가 어긋날 수 있기 때문입니다.
  계약 테스트처럼 시간을 앞당겨야 할 때만 clock을 넘깁니다.
- 결과는 jsonb에 저장하고, 읽을 때 AnalysisResult로 다시 검증합니다. 원본 코드는 저장하지 않습니다.
"""

from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import ColumnElement, DateTime, RowMapping, and_, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from gitinsight_api.analyses.store import (
    IN_PROGRESS,
    STALE_AFTER,
    STALE_ERROR,
    UNVERSIONED,
    AnalysisKey,
    AnalysisRecord,
    AnalysisRecordNotFoundError,
    Clock,
    check_result,
    clean_error,
    sanitized,
)
from gitinsight_api.tables import analyses
from gitinsight_contracts.analysis import AnalysisResult
from gitinsight_contracts.api import AnalysisState, AnalysisStatus

# 데모 스냅샷을 넣으면서 닫은 같은 키의 기록에 남기는 오류 이름입니다.
SNAPSHOT_REPLACED_ERROR = "ReplacedBySnapshot"
_IN_PROGRESS_VALUES = sorted(s.value for s in IN_PROGRESS)
_FAILED = AnalysisStatus.FAILED.value
_RECORD_COLUMNS = (
    analyses.c.analysis_id,
    analyses.c.repo_full_name,
    analyses.c.commit_sha,
    analyses.c.user_id,
    analyses.c.analyzer_version,
    analyses.c.status,
    analyses.c.progress,
    analyses.c.error,
    analyses.c.updated_at,
)


def _record(row: RowMapping) -> AnalysisRecord:
    return AnalysisRecord(
        analysis_id=row["analysis_id"],
        key=AnalysisKey(
            repo_full_name=row["repo_full_name"],
            commit_sha=row["commit_sha"],
            user_id=row["user_id"],
            analyzer_version=row["analyzer_version"],
        ),
        state=AnalysisState(
            status=AnalysisStatus(row["status"]),
            progress=row["progress"],
            commit_sha=row["commit_sha"],
            error=row["error"],
        ),
        updated_at=row["updated_at"],
    )


def _key_matches(key: AnalysisKey) -> ColumnElement[bool]:
    return and_(
        analyses.c.repo_full_name == key.repo_full_name,
        analyses.c.commit_sha == key.commit_sha,
        analyses.c.user_id == key.user_id,
        analyses.c.analyzer_version == key.analyzer_version,
    )


class PostgresAnalysisStore:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        clock: Clock | None = None,
        stale_after: timedelta = STALE_AFTER,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._stale_after = stale_after

    def _now(self) -> ColumnElement[Any]:
        if self._clock is None:
            return func.now()
        return literal(self._clock(), DateTime(timezone=True))

    async def get_or_create(self, key: AnalysisKey) -> tuple[AnalysisRecord, bool]:
        async with self._engine.begin() as conn:
            now = self._now()
            # 오래된 진행 중 기록을 먼저 닫아 부분 UNIQUE 인덱스에서 뺍니다.
            await conn.execute(
                update(analyses)
                .where(
                    _key_matches(key),
                    analyses.c.status.in_(_IN_PROGRESS_VALUES),
                    # 메모리 구현과 같게, 정확히 stale_after가 지난 기록부터 오래된 것으로 봅니다.
                    analyses.c.updated_at <= now - self._stale_after,
                )
                .values(status=_FAILED, error=STALE_ERROR, updated_at=now)
            )
            inserted = (
                (
                    await conn.execute(
                        insert(analyses)
                        .values(
                            analysis_id=str(uuid4()),
                            repo_full_name=key.repo_full_name,
                            commit_sha=key.commit_sha,
                            user_id=key.user_id,
                            analyzer_version=key.analyzer_version,
                            status=AnalysisStatus.QUEUED.value,
                            progress=0.0,
                            updated_at=now,
                        )
                        .on_conflict_do_nothing(
                            index_elements=[
                                analyses.c.repo_full_name,
                                analyses.c.commit_sha,
                                analyses.c.user_id,
                                analyses.c.analyzer_version,
                            ],
                            index_where=analyses.c.status != _FAILED,
                        )
                        .returning(*_RECORD_COLUMNS)
                    )
                )
                .mappings()
                .first()
            )
            if inserted is not None:
                return _record(inserted), True
            # 같은 키의 재사용할 기록이 이미 있습니다. 동시에 만든 쪽이 커밋할 때까지 INSERT가 기다린 뒤 여기로 옵니다.
            existing = (
                (
                    await conn.execute(
                        select(*_RECORD_COLUMNS).where(
                            _key_matches(key), analyses.c.status != _FAILED
                        )
                    )
                )
                .mappings()
                .one()
            )
            return _record(existing), False

    async def get_record(self, analysis_id: str) -> AnalysisRecord | None:
        async with self._engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(*_RECORD_COLUMNS).where(analyses.c.analysis_id == analysis_id)
                    )
                )
                .mappings()
                .first()
            )
        return _record(row) if row is not None else None

    async def update_state(self, analysis_id: str, state: AnalysisState) -> None:
        if state.status is AnalysisStatus.DONE:
            raise ValueError("done은 save_result로만 저장합니다")
        clean = sanitized(state)
        async with self._engine.begin() as conn:
            changed = await conn.execute(
                update(analyses)
                .where(
                    analyses.c.analysis_id == analysis_id,
                    analyses.c.status.in_(_IN_PROGRESS_VALUES),
                )
                .values(
                    status=clean.status.value,
                    progress=clean.progress,
                    error=clean.error,
                    updated_at=self._now(),
                )
            )
            if changed.rowcount:
                return
            # 끝난 기록은 바꾸지 않습니다. 기록이 아예 없을 때만 오류입니다.
            exists = await conn.scalar(
                select(analyses.c.analysis_id).where(analyses.c.analysis_id == analysis_id)
            )
            if exists is None:
                raise AnalysisRecordNotFoundError(analysis_id)

    async def save_result(self, analysis_id: str, result: AnalysisResult) -> None:
        async with self._engine.begin() as conn:
            row = (
                (
                    await conn.execute(
                        select(*_RECORD_COLUMNS)
                        .where(analyses.c.analysis_id == analysis_id)
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise AnalysisRecordNotFoundError(analysis_id)
            record = _record(row)
            if not record.in_progress:
                raise ValueError("진행 중인 기록에만 결과를 저장합니다")
            check_result(record, result)
            await conn.execute(
                update(analyses)
                .where(analyses.c.analysis_id == analysis_id)
                .values(
                    status=AnalysisStatus.DONE.value,
                    progress=1.0,
                    error=None,
                    result=result.model_dump(mode="json"),
                    updated_at=self._now(),
                )
            )

    async def fail_in_progress(self, error: str) -> int:
        async with self._engine.begin() as conn:
            closed = await conn.execute(
                update(analyses)
                .where(analyses.c.status.in_(_IN_PROGRESS_VALUES))
                .values(status=_FAILED, error=clean_error(error), updated_at=self._now())
            )
            return closed.rowcount

    async def save_snapshot(
        self, result: AnalysisResult, user_id: str, analyzer_version: str = UNVERSIONED
    ) -> AnalysisRecord:
        """미리 만든 분석 결과를 그 analysis_id 그대로 완료 기록으로 넣습니다. 데모 스냅샷용입니다.

        analyzer_version은 지금 서버의 분석기 버전으로 넣어야 같은 레포를 다시 요청할 때 스냅샷을 재사용합니다 (#171).

        여러 번 실행해도 같은 기록 하나만 남습니다.
        같은 키의 다른 진행 중·완료 기록은 failed(SNAPSHOT_REPLACED_ERROR)로 닫아,
        이 키를 다시 요청하면 스냅샷을 재사용하게 합니다.
        """
        key = AnalysisKey(result.repo_full_name, result.commit_sha, user_id, analyzer_version)
        async with self._engine.begin() as conn:
            now = self._now()
            await conn.execute(
                update(analyses)
                .where(
                    _key_matches(key),
                    analyses.c.status != _FAILED,
                    analyses.c.analysis_id != result.analysis_id,
                )
                .values(status=_FAILED, error=SNAPSHOT_REPLACED_ERROR, updated_at=now)
            )
            values = {
                "repo_full_name": key.repo_full_name,
                "commit_sha": key.commit_sha,
                "user_id": key.user_id,
                "analyzer_version": key.analyzer_version,
                "status": AnalysisStatus.DONE.value,
                "progress": 1.0,
                "error": None,
                "result": result.model_dump(mode="json"),
                "updated_at": now,
            }
            row = (
                (
                    await conn.execute(
                        insert(analyses)
                        .values(analysis_id=result.analysis_id, **values)
                        .on_conflict_do_update(index_elements=[analyses.c.analysis_id], set_=values)
                        .returning(*_RECORD_COLUMNS)
                    )
                )
                .mappings()
                .one()
            )
        return _record(row)

    async def get(self, analysis_id: str) -> AnalysisResult | None:
        async with self._engine.connect() as conn:
            stored = await conn.scalar(
                select(analyses.c.result).where(
                    analyses.c.analysis_id == analysis_id,
                    analyses.c.status == AnalysisStatus.DONE.value,
                )
            )
        return AnalysisResult.model_validate(stored) if stored is not None else None
