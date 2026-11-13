"""분석 기록 저장소 인터페이스와 메모리 구현.

- 기록 하나는 분석 요청 하나입니다. 상태(AnalysisState)와, 끝나면 결과(AnalysisResult JSON)를 가집니다.
- 같은 키(레포, 커밋, 요청자)로 동시에 요청해도 기록은 하나만 만듭니다.
  메모리 구현은 asyncio.Lock으로 이를 보장합니다.
  Postgres 구현(#17 이후)은 failed가 아닌 기록에 거는 키 부분 UNIQUE 인덱스와
  INSERT … ON CONFLICT DO NOTHING으로 같은 보장을 합니다.
- 진행 중인 기록이 오래 갱신되지 않으면 워커가 죽은 것으로 보고, 그 기록을 failed(StaleAnalysis)로 닫은 뒤 새 기록을 만듭니다.
  닫힌 기록에 늦게 온 진행률은 무시되고 결과 저장은 거부되므로, 느렸던 옛 워커가 결과를 덮어쓰지 못합니다.
  Postgres 구현은 오래됨 판단에 앱 시계가 아니라 DB의 now()를 씁니다. 서버와 워커의 시계가 다를 수 있기 때문입니다.
- 원본 코드는 저장하지 않습니다. 저장하는 것은 계약에 정의된 AnalysisResult뿐입니다.
- 결과는 JSON으로 저장하고, 읽을 때 AnalysisResult로 다시 검증합니다.
"""

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from gitinsight_contracts.analysis import AnalysisResult
from gitinsight_contracts.api import AnalysisState, AnalysisStatus

Clock = Callable[[], datetime]

# 이 시간 동안 상태 갱신이 없는 진행 중 기록은 워커가 죽은 것으로 봅니다.
# 분석은 보통 1분 안에 끝나고 단계마다 진행률을 보고합니다(docs/evals/stage2-2026-11-12.md).
STALE_AFTER = timedelta(minutes=10)
# failed 상태의 오류에는 예외 종류 이름만 남깁니다. 메시지에는 레포 내용이 섞일 수 있습니다.
_ERROR_KIND = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,99}")
UNKNOWN_ERROR = "Error"
# 오래되어 교체된 기록의 오류 이름입니다.
STALE_ERROR = "StaleAnalysis"

_IN_PROGRESS = frozenset({AnalysisStatus.QUEUED, AnalysisStatus.STAGE1, AnalysisStatus.STAGE2})


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class AnalysisKey:
    """캐시 키입니다. 기여 라벨이 요청자마다 달라서 요청자까지 넣습니다(#12 코멘트)."""

    repo_full_name: str
    commit_sha: str
    user_id: str


@dataclass(frozen=True)
class AnalysisRecord:
    analysis_id: str
    key: AnalysisKey
    state: AnalysisState
    updated_at: datetime

    @property
    def in_progress(self) -> bool:
        return self.state.status in _IN_PROGRESS


class AnalysisRecordNotFoundError(Exception):
    pass


class AnalysisStore(Protocol):
    async def get_or_create(self, key: AnalysisKey) -> tuple[AnalysisRecord, bool]:
        """키의 재사용할 기록을 돌려주고, 없으면 queued 기록을 만듭니다. (기록, 새로 만들었는지)입니다.

        완료된 기록과 진행 중인 기록은 재사용합니다.
        실패한 기록은 재사용하지 않고 새 기록을 만듭니다.
        오래 갱신되지 않은 진행 중 기록은 failed(STALE_ERROR)로 닫은 뒤 새 기록을 만듭니다.
        """
        ...

    async def get_record(self, analysis_id: str) -> AnalysisRecord | None: ...

    async def update_state(self, analysis_id: str, state: AnalysisState) -> None:
        """진행 상태를 저장합니다. 이미 끝난(done, failed) 기록은 바꾸지 않습니다."""
        ...

    async def save_result(self, analysis_id: str, result: AnalysisResult) -> None:
        """결과를 저장하고 done으로 바꿉니다. 진행 중인 기록에만 저장할 수 있습니다."""
        ...

    async def get(self, analysis_id: str) -> AnalysisResult | None:
        """완료된 결과를 돌려줍니다. 면접 두뇌의 AnalysisSource로 씁니다.

        소유자 확인을 하지 않습니다.
        라우트(예: POST /api/sessions)는 get_record로 key.user_id가 요청자인지 먼저 확인하고, 아니면 404를 냅니다.
        결과에는 비공개 레포의 심볼 경로, 요약, 질문이 들어 있습니다.
        """
        ...


def sanitized(state: AnalysisState) -> AnalysisState:
    """failed 오류가 예외 종류 이름 형식이 아니면 일반 이름으로 바꿉니다."""
    if state.error is None or _ERROR_KIND.fullmatch(state.error):
        return state
    return state.model_copy(update={"error": UNKNOWN_ERROR})


def check_result(record: AnalysisRecord, result: AnalysisResult) -> None:
    if result.analysis_id != record.analysis_id:
        raise ValueError("결과의 analysis_id가 기록과 다릅니다")
    if (result.repo_full_name, result.commit_sha) != (
        record.key.repo_full_name,
        record.key.commit_sha,
    ):
        raise ValueError("결과의 레포나 커밋이 기록과 다릅니다")


class InMemoryAnalysisStore:
    def __init__(self, *, clock: Clock = utc_now, stale_after: timedelta = STALE_AFTER) -> None:
        self._clock = clock
        self._stale_after = stale_after
        self._records: dict[str, AnalysisRecord] = {}
        self._results: dict[str, str] = {}
        # 키별로 지금 재사용할 수 있는 기록입니다. Postgres에서는 키 UNIQUE 인덱스가 같은 역할을 합니다.
        self._latest: dict[AnalysisKey, str] = {}
        self._lock = asyncio.Lock()

    def _reusable(self, record: AnalysisRecord) -> bool:
        if record.state.status is AnalysisStatus.DONE:
            return True
        if record.in_progress:
            return self._clock() - record.updated_at < self._stale_after
        return False

    async def get_or_create(self, key: AnalysisKey) -> tuple[AnalysisRecord, bool]:
        async with self._lock:
            existing_id = self._latest.get(key)
            existing = self._records[existing_id] if existing_id is not None else None
            if existing is not None and self._reusable(existing):
                return existing, False
            if existing is not None and existing.in_progress:
                # 오래된 기록을 같은 잠금 안에서 닫아, 늦게 온 옛 워커의 보고와 결과를 기존 규칙으로 막습니다.
                self._records[existing.analysis_id] = replace(
                    existing,
                    state=existing.state.model_copy(
                        update={"status": AnalysisStatus.FAILED, "error": STALE_ERROR}
                    ),
                    updated_at=self._clock(),
                )
            record = AnalysisRecord(
                analysis_id=str(uuid4()),
                key=key,
                state=AnalysisState(
                    status=AnalysisStatus.QUEUED, progress=0.0, commit_sha=key.commit_sha
                ),
                updated_at=self._clock(),
            )
            self._records[record.analysis_id] = record
            self._latest[key] = record.analysis_id
            return record, True

    async def get_record(self, analysis_id: str) -> AnalysisRecord | None:
        return self._records.get(analysis_id)

    def _require(self, analysis_id: str) -> AnalysisRecord:
        record = self._records.get(analysis_id)
        if record is None:
            raise AnalysisRecordNotFoundError(analysis_id)
        return record

    async def update_state(self, analysis_id: str, state: AnalysisState) -> None:
        async with self._lock:
            record = self._require(analysis_id)
            if not record.in_progress:
                return
            if state.status is AnalysisStatus.DONE:
                raise ValueError("done은 save_result로만 저장합니다")
            self._records[analysis_id] = replace(
                record, state=sanitized(state), updated_at=self._clock()
            )

    async def save_result(self, analysis_id: str, result: AnalysisResult) -> None:
        async with self._lock:
            record = self._require(analysis_id)
            if not record.in_progress:
                raise ValueError("진행 중인 기록에만 결과를 저장합니다")
            check_result(record, result)
            self._results[analysis_id] = result.model_dump_json()
            done = AnalysisState(
                status=AnalysisStatus.DONE, progress=1.0, commit_sha=record.key.commit_sha
            )
            self._records[analysis_id] = replace(record, state=done, updated_at=self._clock())

    async def get(self, analysis_id: str) -> AnalysisResult | None:
        stored = self._results.get(analysis_id)
        return AnalysisResult.model_validate_json(stored) if stored is not None else None
