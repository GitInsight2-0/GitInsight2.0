"""라우트가 같이 쓰는 의존성과 소유자 확인."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status

from gitinsight_api.auth import CurrentUser, get_current_user
from gitinsight_api.brain.protocol import Brain, SessionNotFoundError
from gitinsight_api.report.service import ReportService


def get_brain(request: Request) -> Brain:
    return request.app.state.brain


def get_report_service(request: Request) -> ReportService:
    reports: ReportService | None = request.app.state.report_service
    if reports is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "리포트 서비스가 설정되지 않았습니다"
        )
    return reports


async def ensure_owner(
    brain: Brain, session_id: UUID, user: CurrentUser, *, forbidden: str
) -> None:
    """세션 소유자가 로그인 사용자와 같은지 확인합니다. 소유자는 만든 뒤 바뀌지 않아 잠금 없이 봅니다."""
    try:
        owner = await brain.owner_of(session_id)
    except SessionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "세션을 찾을 수 없습니다") from None
    if owner != user.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, forbidden)


BrainDep = Annotated[Brain, Depends(get_brain)]
UserDep = Annotated[CurrentUser, Depends(get_current_user)]
ReportServiceDep = Annotated[ReportService, Depends(get_report_service)]
