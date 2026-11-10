"""면접 세션 라우트 (/api/sessions).

인증(D의 T4)이 붙기 전까지는 소유자 확인이 없습니다. 로컬 개발과 음성 PoC에서만 씁니다.
"""

import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status

from gitinsight_api.brain.protocol import (
    AnalysisNotFoundError,
    Brain,
    SessionClosedError,
    SessionNotFoundError,
)
from gitinsight_api.settings import Settings
from gitinsight_contracts.interview import BrainReply, SessionCreate, SessionCreated, TurnRequest

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

MAX_LAB_DELAY_MS = 5000


def _brain(request: Request) -> Brain:
    brain: Brain | None = request.app.state.brain
    if brain is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "면접 두뇌가 설정되지 않았습니다")
    return brain


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(body: SessionCreate, request: Request) -> SessionCreated:
    try:
        session_id, first = await _brain(request).start(body)
    except AnalysisNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "분석 결과를 찾을 수 없습니다") from None
    return SessionCreated(session_id=session_id, first=first)


@router.post("/{session_id}/turns")
async def post_turn(
    session_id: UUID,
    body: TurnRequest,
    request: Request,
    lab_delay_ms: Annotated[int | None, Query(ge=0, le=MAX_LAB_DELAY_MS)] = None,
) -> BrainReply:
    brain = _brain(request)
    if lab_delay_ms is not None:
        if not _settings(request).lab_enabled:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "lab_delay_ms는 LAB_ENABLED=true일 때만 쓸 수 있습니다"
            )
        await asyncio.sleep(lab_delay_ms / 1000)
    try:
        return await brain.handle_turn(session_id, body)
    except SessionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "세션을 찾을 수 없습니다") from None
    except SessionClosedError:
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 끝난 세션입니다") from None
