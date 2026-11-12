"""리포트 라우트 (/api/reports).

리포트 소유자는 리포트가 나온 세션의 소유자입니다.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from gitinsight_api.routes.deps import BrainDep, ReportServiceDep, UserDep, ensure_owner
from gitinsight_contracts.report import Report

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/{report_id}")
async def get_report(
    report_id: UUID, brain: BrainDep, user: UserDep, reports: ReportServiceDep
) -> Report:
    report = await reports.get(report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "리포트를 찾을 수 없습니다")
    await ensure_owner(brain, report.session_id, user, forbidden="이 리포트에 접근할 수 없습니다")
    return report
