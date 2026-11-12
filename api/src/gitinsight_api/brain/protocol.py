"""라우트가 의존하는 면접 두뇌 인터페이스와 오류."""

from typing import Protocol
from uuid import UUID

from gitinsight_contracts.interview import BrainReply, SessionCreate, TurnRequest


class SessionNotFoundError(Exception):
    pass


class SessionClosedError(Exception):
    pass


class AnalysisNotFoundError(Exception):
    pass


class Brain(Protocol):
    async def start(
        self, request: SessionCreate, owner_id: str | None = None
    ) -> tuple[UUID, BrainReply]:
        """세션을 만들고 첫 질문을 돌려줍니다."""
        ...

    async def owner_of(self, session_id: UUID) -> str | None:
        """세션 소유자를 알려줍니다. 세션이 없으면 SessionNotFoundError를 냅니다."""
        ...

    async def handle_turn(self, session_id: UUID, request: TurnRequest) -> BrainReply:
        """답변 한 턴을 처리합니다. 같은 turn_id는 한 번만 처리하고 같은 응답을 돌려줍니다."""
        ...
