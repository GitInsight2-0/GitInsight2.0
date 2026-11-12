"""면접 세션 라우트 (/api/sessions).

세션은 만든 로그인 사용자만 이어 가고 끝낼 수 있습니다.
LAB_ENABLED=true(음성 PoC)에서는 로그인 없이 고정 실험 사용자로 처리합니다.
로그에는 id, 행동, 시간만 남기고 자막, JD, 코드, 사용자 이름은 남기지 않습니다.
"""

import asyncio
import logging
import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status

from gitinsight_api.brain.protocol import (
    AnalysisNotFoundError,
    SessionClosedError,
    SessionNotFoundError,
)
from gitinsight_api.routes.deps import BrainDep, ReportServiceDep, UserDep, ensure_owner
from gitinsight_api.settings import Settings
from gitinsight_contracts.interview import (
    BrainReply,
    SessionCreate,
    SessionCreated,
    SessionEnded,
    TurnRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

MAX_LAB_DELAY_MS = 5000
SESSION_FORBIDDEN = "이 세션에 접근할 수 없습니다"


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(body: SessionCreate, brain: BrainDep, user: UserDep) -> SessionCreated:
    try:
        session_id, first = await brain.start(body, owner_id=user.user_id)
    except AnalysisNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "분석 결과를 찾을 수 없습니다") from None
    logger.info(
        "session start session=%s analysis=%s mode=%s",
        session_id,
        body.analysis_id,
        body.mode.value,
    )
    return SessionCreated(session_id=session_id, first=first)


@router.post("/{session_id}/turns")
async def post_turn(
    session_id: UUID,
    body: TurnRequest,
    request: Request,
    brain: BrainDep,
    user: UserDep,
    lab_delay_ms: Annotated[int | None, Query(ge=0, le=MAX_LAB_DELAY_MS)] = None,
) -> BrainReply:
    if lab_delay_ms is not None and not _settings(request).lab_enabled:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "lab_delay_ms는 LAB_ENABLED=true일 때만 쓸 수 있습니다"
        )
    await ensure_owner(brain, session_id, user, forbidden=SESSION_FORBIDDEN)
    if lab_delay_ms is not None:
        await asyncio.sleep(lab_delay_ms / 1000)
    try:
        return await brain.handle_turn(session_id, body)
    except SessionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "세션을 찾을 수 없습니다") from None
    except SessionClosedError:
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 끝난 세션입니다") from None


@router.post("/{session_id}/end")
async def end_session(
    session_id: UUID, brain: BrainDep, user: UserDep, reports: ReportServiceDep
) -> SessionEnded:
    """세션을 끝내고 리포트를 만듭니다. 여러 번 불러도 같은 report_id를 돌려줍니다."""
    await ensure_owner(brain, session_id, user, forbidden=SESSION_FORBIDDEN)
    began = time.perf_counter()
    try:
        report = await reports.end_session(session_id)
    except SessionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "세션을 찾을 수 없습니다") from None
    except AnalysisNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "분석 결과를 찾을 수 없습니다") from None
    logger.info(
        "session end session=%s report=%s kind=%s ms=%.0f",
        session_id,
        report.report_id,
        report.kind.value,
        (time.perf_counter() - began) * 1000,
    )
    return SessionEnded(report_id=report.report_id)
