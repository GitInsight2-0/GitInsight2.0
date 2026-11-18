"""리포트 저장소의 Postgres 구현 (#75). 규칙은 service.py의 ReportStore와 메모리 구현과 같습니다.

- 세션 하나에 리포트는 하나입니다. session_id의 유일 제약이 두 번째 저장을 막습니다.
  서버가 여러 개일 때 같은 세션을 동시에 끝내면 먼저 저장된 리포트만 남습니다.
- 리포트는 jsonb에 저장하고, 읽을 때 Report로 다시 검증합니다.
- 세션을 지우면 리포트도 같이 지워집니다 (ON DELETE CASCADE).
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from gitinsight_api.report.service import ReportAlreadyExistsError
from gitinsight_api.tables import reports
from gitinsight_contracts.report import Report


class PostgresReportStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def save(self, report: Report) -> None:
        async with self._engine.begin() as conn:
            saved = await conn.execute(
                insert(reports)
                .values(
                    report_id=report.report_id,
                    session_id=report.session_id,
                    report=report.model_dump(mode="json"),
                )
                # 같은 세션의 두 번째 리포트만 조용히 건너뜁니다. report_id가 겹치면 그대로 오류가 납니다.
                .on_conflict_do_nothing(index_elements=[reports.c.session_id])
                # 건너뛴 INSERT는 행을 돌려주지 않습니다. rowcount는 드라이버에 따라 믿을 수 없습니다.
                .returning(reports.c.report_id)
            )
            created = saved.first() is not None
        if not created:
            raise ReportAlreadyExistsError(report.session_id)

    async def get(self, report_id: UUID) -> Report | None:
        async with self._engine.connect() as conn:
            stored = await conn.scalar(
                select(reports.c.report).where(reports.c.report_id == report_id)
            )
        return Report.model_validate(stored) if stored is not None else None

    async def get_by_session(self, session_id: UUID) -> Report | None:
        async with self._engine.connect() as conn:
            stored = await conn.scalar(
                select(reports.c.report).where(reports.c.session_id == session_id)
            )
        return Report.model_validate(stored) if stored is not None else None
