"""리포트 라우트 (/api/reports)와 이전 면접 비교 (/api/me/progress).

리포트 소유자는 리포트가 나온 세션의 소유자입니다.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from gitinsight_api.routes.deps import BrainDep, ReportServiceDep, UserDep, ensure_owner
from gitinsight_contracts.report import ProgressEntry, Report

router = APIRouter(prefix="/api/reports", tags=["reports"])
# 계약의 경로가 /api/me/progress라서 접두사 없는 라우터를 따로 둡니다.
progress_router = APIRouter(tags=["reports"])


@router.get("/{report_id}")
async def get_report(
    report_id: UUID, brain: BrainDep, user: UserDep, reports: ReportServiceDep
) -> Report:
    report = await reports.get(report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "리포트를 찾을 수 없습니다")
    await ensure_owner(brain, report.session_id, user, forbidden="이 리포트에 접근할 수 없습니다")
    return report


@progress_router.get("/api/me/progress")
async def get_progress(user: UserDep, reports: ReportServiceDep) -> list[ProgressEntry]:
    """로그인한 사용자의 지난 면접별 역량 수준입니다. 오래된 면접부터 돌려줍니다.

    역량 평가가 없는 리포트(턴별 판단 표)는 들어가지 않습니다. 면접 기록이 없으면 빈 목록입니다.
    """
    return await reports.progress(user.user_id)
