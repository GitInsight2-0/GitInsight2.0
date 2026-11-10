"""세션 종료와 리포트 보관.

세션 종료는 멱등입니다. 같은 세션을 여러 번 종료해도 리포트는 한 번만 만들고 같은 report_id를 돌려줍니다.
리포트 저장소는 메모리 구현만 있습니다. Postgres 저장과 화면은 D(T14)가 붙입니다.
"""

import asyncio
from typing import Protocol
from uuid import UUID

from gitinsight_api.brain.analysis_source import AnalysisSource
from gitinsight_api.brain.protocol import AnalysisNotFoundError
from gitinsight_api.brain.repository import SessionRepository
from gitinsight_api.report.builder import ReportBuilder
from gitinsight_contracts.report import Report


class ReportStore(Protocol):
    async def save(self, report: Report) -> None: ...

    async def get(self, report_id: UUID) -> Report | None: ...

    async def get_by_session(self, session_id: UUID) -> Report | None: ...


class InMemoryReportStore:
    def __init__(self) -> None:
        self._reports: dict[UUID, Report] = {}
        self._by_session: dict[UUID, UUID] = {}

    async def save(self, report: Report) -> None:
        if report.session_id in self._by_session:
            raise ValueError(f"이미 리포트가 있는 세션입니다: {report.session_id}")
        self._reports[report.report_id] = report
        self._by_session[report.session_id] = report.report_id

    async def get(self, report_id: UUID) -> Report | None:
        return self._reports.get(report_id)

    async def get_by_session(self, session_id: UUID) -> Report | None:
        report_id = self._by_session.get(session_id)
        return self._reports.get(report_id) if report_id else None


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
            await self._store.save(report)
            return report

    async def get(self, report_id: UUID) -> Report | None:
        return await self._store.get(report_id)
