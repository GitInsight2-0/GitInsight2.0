"""세션 저장소 인터페이스와 메모리 구현.

한 세션의 턴은 세션 잠금 안에서 순서대로 처리합니다.
메모리 구현은 세션별 asyncio.Lock, Postgres 구현(Task 9)은 SELECT … FOR UPDATE를 씁니다.
잠금 안에서 commit하지 않고 나가면 아무것도 저장하지 않습니다.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol
from uuid import UUID

from gitinsight_api.brain.protocol import SessionNotFoundError
from gitinsight_api.brain.state import SessionState, TurnRecord


class SessionHandle(Protocol):
    @property
    def state(self) -> SessionState: ...

    async def get_turn(self, turn_id: UUID) -> TurnRecord | None: ...

    async def commit(self, state: SessionState, turn: TurnRecord | None = None) -> None: ...


class SessionRepository(Protocol):
    async def create(self, state: SessionState) -> None: ...

    async def get(self, session_id: UUID) -> SessionState | None: ...

    async def list_turns(self, session_id: UUID) -> list[TurnRecord]: ...

    def locked(self, session_id: UUID) -> AbstractAsyncContextManager[SessionHandle]:
        """세션을 잠그고 핸들을 돌려줍니다. 세션이 없으면 SessionNotFoundError를 냅니다."""
        ...


class _MemoryHandle:
    def __init__(self, repo: "InMemorySessionRepository", state: SessionState) -> None:
        self._repo = repo
        self._state = state

    @property
    def state(self) -> SessionState:
        return self._state

    async def get_turn(self, turn_id: UUID) -> TurnRecord | None:
        turn = self._repo._turns[self._state.session_id].get(turn_id)
        return turn.model_copy(deep=True) if turn else None

    async def commit(self, state: SessionState, turn: TurnRecord | None = None) -> None:
        if turn is not None:
            turns = self._repo._turns[state.session_id]
            if turn.turn_id in turns:
                # Postgres의 UNIQUE(session_id, turn_id)와 같은 안전장치입니다.
                raise ValueError(f"이미 저장된 turn_id입니다: {turn.turn_id}")
            turns[turn.turn_id] = turn.model_copy(deep=True)
        self._repo._states[state.session_id] = state.model_copy(deep=True)
        self._state = state


class InMemorySessionRepository:
    def __init__(self) -> None:
        self._states: dict[UUID, SessionState] = {}
        self._turns: dict[UUID, dict[UUID, TurnRecord]] = {}
        self._locks: dict[UUID, asyncio.Lock] = {}

    async def create(self, state: SessionState) -> None:
        if state.session_id in self._states:
            raise ValueError(f"이미 있는 세션입니다: {state.session_id}")
        self._states[state.session_id] = state.model_copy(deep=True)
        self._turns[state.session_id] = {}
        self._locks[state.session_id] = asyncio.Lock()

    async def get(self, session_id: UUID) -> SessionState | None:
        state = self._states.get(session_id)
        return state.model_copy(deep=True) if state else None

    async def list_turns(self, session_id: UUID) -> list[TurnRecord]:
        turns = self._turns.get(session_id, {})
        return sorted((t.model_copy(deep=True) for t in turns.values()), key=lambda t: t.index)

    @asynccontextmanager
    async def _locked(self, session_id: UUID) -> AsyncIterator[SessionHandle]:
        lock = self._locks.get(session_id)
        if lock is None:
            raise SessionNotFoundError(session_id)
        async with lock:
            yield _MemoryHandle(self, self._states[session_id].model_copy(deep=True))

    def locked(self, session_id: UUID) -> AbstractAsyncContextManager[SessionHandle]:
        return self._locked(session_id)
