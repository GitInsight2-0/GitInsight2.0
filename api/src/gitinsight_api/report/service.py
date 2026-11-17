"""세션 종료와 리포트 보관.

세션 종료는 멱등입니다. 같은 세션을 여러 번 종료해도 리포트는 한 번만 만들고 같은 report_id를 돌려줍니다.
리포트 저장소는 메모리 구현(여기)과 Postgres 구현(report/postgres.py, #75)이 있습니다.
세션 하나에 리포트는 하나입니다. 이미 리포트가 있는 세션에 다시 저장하면 ReportAlreadyExistsError입니다.
리포트는 세션 소유자와 함께 저장합니다. 이전 면접 비교(progress)는 그 소유자의 리포트만 봅니다 (#77).
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from gitinsight_api.brain.analysis_source import AnalysisSource
from gitinsight_api.brain.protocol import AnalysisNotFoundError
from gitinsight_api.brain.repository import SessionRepository
from gitinsight_api.report.builder import ReportBuilder
from gitinsight_contracts.report import CompetencyLevel, ProgressEntry, Report

# 이전 면접 비교에 돌려주는 최대 면접 수입니다. 가장 최근 것부터 이만큼만 봅니다.
PROGRESS_LIMIT = 20


class ReportAlreadyExistsError(ValueError):
    """이미 리포트가 있는 세션입니다."""

    def __init__(self, session_id: UUID) -> None:
        super().__init__(f"이미 리포트가 있는 세션입니다: {session_id}")
        self.session_id = session_id


@dataclass(frozen=True)
class SavedReport:
    report: Report
    # 리포트를 저장한 시각입니다.
    created_at: datetime


class ReportStore(Protocol):
    async def save(self, report: Report, owner_id: str | None = None) -> None: ...

    async def get(self, report_id: UUID) -> Report | None: ...

    async def get_by_session(self, session_id: UUID) -> Report | None: ...

    async def list_by_owner(self, owner_id: str, limit: int) -> list[SavedReport]:
        """그 소유자의 리포트 중 가장 최근 limit개를 오래된 것부터 돌려줍니다."""
        ...


class InMemoryReportStore:
    def __init__(self) -> None:
        self._reports: dict[UUID, Report] = {}
        self._by_session: dict[UUID, UUID] = {}
        # 저장한 순서대로 (소유자, 저장 시각, report_id)입니다.
        self._saved: list[tuple[str | None, datetime, UUID]] = []

    async def save(self, report: Report, owner_id: str | None = None) -> None:
        if report.session_id in self._by_session:
            raise ReportAlreadyExistsError(report.session_id)
        self._reports[report.report_id] = report
        self._by_session[report.session_id] = report.report_id
        self._saved.append((owner_id, datetime.now(UTC), report.report_id))

    async def get(self, report_id: UUID) -> Report | None:
        return self._reports.get(report_id)

    async def get_by_session(self, session_id: UUID) -> Report | None:
        report_id = self._by_session.get(session_id)
        return self._reports.get(report_id) if report_id else None

    async def list_by_owner(self, owner_id: str, limit: int) -> list[SavedReport]:
        mine = [
            SavedReport(self._reports[report_id], created_at)
            for owner, created_at, report_id in self._saved
            if owner == owner_id
        ]
        return mine[-limit:] if limit > 0 else []


class ReportService:
    def __init__(
        self,
        *,
        analyses: AnalysisSource,
        sessions: SessionRepository,
        store: ReportStore,
        builder: ReportBuilder,
    ) -> None:
        self._analyses = analyses
        self._sessions = sessions
        self._store = store
        self._builder = builder
        self._locks: dict[UUID, asyncio.Lock] = {}

    async def end_session(self, session_id: UUID) -> Report:
        """세션을 닫고 리포트를 만듭니다. 이미 리포트가 있으면 그대로 돌려줍니다."""
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            existing = await self._store.get_by_session(session_id)
            if existing is not None:
                return existing
            # 세션 잠금 안에서 닫아서, 처리 중인 턴이 끝난 뒤에 리포트를 만듭니다.
            async with self._sessions.locked(session_id) as handle:
                state = handle.state.model_copy(deep=True)
                if not state.closed:
                    state.closed = True
                    await handle.commit(state)
            analysis = await self._analyses.get(state.analysis_id)
            if analysis is None:
                raise AnalysisNotFoundError(state.analysis_id)
            turns = await self._sessions.list_turns(session_id)
            report = await self._builder.build(analysis, state, turns)
            try:
                await self._store.save(report, state.owner_id)
            except ReportAlreadyExistsError:
                # 다른 서버가 같은 세션의 리포트를 먼저 저장했습니다. 그 리포트를 돌려줘서 report_id가 하나로 유지됩니다.
                saved = await self._store.get_by_session(session_id)
                if saved is None:
                    raise
                return saved
            return report

    async def get(self, report_id: UUID) -> Report | None:
        return await self._store.get(report_id)

    async def progress(self, owner_id: str) -> list[ProgressEntry]:
        """그 사용자의 지난 면접별 역량 수준입니다. 오래된 면접부터 돌려줍니다.

        리포트에 적힌 역량 수준을 그대로 옮깁니다. 새로 계산하지 않습니다.
        역량 평가가 없는 리포트(턴별 판단 표)는 비교할 값이 없어서 뺍니다.
        """
        saved = await self._store.list_by_owner(owner_id, PROGRESS_LIMIT)
        return [
            ProgressEntry(
                session_id=item.report.session_id,
                date=item.created_at,
                competencies=[
                    CompetencyLevel(name=c.name, level=c.level) for c in item.report.competencies
                ],
            )
            for item in saved
            if item.report.competencies
        ]
