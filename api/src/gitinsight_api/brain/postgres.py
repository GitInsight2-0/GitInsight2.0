"""세션 저장소의 Postgres 구현 (#53). 규칙은 repository.py의 SessionRepository와 메모리 구현과 같습니다.

- 세션 잠금: 트랜잭션 하나에서 세션 행을 SELECT … FOR UPDATE로 잡습니다.
  같은 세션의 다른 턴과 종료 요청은 이 트랜잭션이 끝날 때까지 기다립니다.
- 잠금 안에서 commit한 내용은 잠금을 정상으로 나갈 때 DB에 반영됩니다.
  commit하지 않았거나 commit 뒤에 예외가 나면 아무것도 저장하지 않습니다.
- 같은 turn_id는 기본 키 (session_id, turn_id)가 두 번 저장하지 못하게 막습니다.
- 턴 하나를 처리하는 동안 연결 하나를 잡고 있습니다 (판단 LLM 최대 2.5초).
  동시에 처리하는 턴이 연결 풀보다 많으면 풀에서 기다립니다.
- 상태와 턴 기록은 jsonb에 저장하고, 읽을 때 SessionState, TurnRecord로 다시 검증합니다.
"""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from gitinsight_api.brain.protocol import SessionNotFoundError
from gitinsight_api.brain.repository import SessionHandle
from gitinsight_api.brain.state import SessionState, TurnRecord
from gitinsight_api.tables import interview_sessions, interview_turns


class _PostgresHandle:
    def __init__(self, conn: AsyncConnection, state: SessionState) -> None:
        self._conn = conn
        self._state = state

    @property
    def state(self) -> SessionState:
        return self._state

    async def get_turn(self, turn_id: UUID) -> TurnRecord | None:
        stored = await self._conn.scalar(
            select(interview_turns.c.record).where(
                interview_turns.c.session_id == self._state.session_id,
                interview_turns.c.turn_id == turn_id,
            )
        )
        return TurnRecord.model_validate(stored) if stored is not None else None

    async def commit(self, state: SessionState, turn: TurnRecord | None = None) -> None:
        if turn is not None:
            saved = await self._conn.execute(
                insert(interview_turns)
                .values(
                    session_id=state.session_id,
                    turn_id=turn.turn_id,
                    turn_index=turn.index,
                    record=turn.model_dump(mode="json"),
                )
                # 같은 turn_id만 조용히 건너뜁니다. 순서(turn_index)가 겹치면 그대로 오류가 납니다.
                .on_conflict_do_nothing(
                    index_elements=[interview_turns.c.session_id, interview_turns.c.turn_id]
                )
                # 건너뛴 INSERT는 행을 돌려주지 않습니다. rowcount는 드라이버에 따라 믿을 수 없습니다.
                .returning(interview_turns.c.turn_id)
            )
            if saved.first() is None:
                raise ValueError(f"이미 저장된 turn_id입니다: {turn.turn_id}")
        await self._conn.execute(
            update(interview_sessions)
            .where(interview_sessions.c.session_id == state.session_id)
            .values(state=state.model_dump(mode="json"), updated_at=func.now())
        )
        self._state = state


class PostgresSessionRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(self, state: SessionState) -> None:
        async with self._engine.begin() as conn:
            saved = await conn.execute(
                insert(interview_sessions)
                .values(session_id=state.session_id, state=state.model_dump(mode="json"))
                .on_conflict_do_nothing(index_elements=[interview_sessions.c.session_id])
                .returning(interview_sessions.c.session_id)
            )
            created = saved.first() is not None
        if not created:
            raise ValueError(f"이미 있는 세션입니다: {state.session_id}")

    async def get(self, session_id: UUID) -> SessionState | None:
        async with self._engine.connect() as conn:
            stored = await conn.scalar(
                select(interview_sessions.c.state).where(
                    interview_sessions.c.session_id == session_id
                )
            )
        return SessionState.model_validate(stored) if stored is not None else None

    async def list_turns(self, session_id: UUID) -> list[TurnRecord]:
        async with self._engine.connect() as conn:
            stored = await conn.scalars(
                select(interview_turns.c.record)
                .where(interview_turns.c.session_id == session_id)
                .order_by(interview_turns.c.turn_index)
            )
            return [TurnRecord.model_validate(record) for record in stored]

    @asynccontextmanager
    async def _locked(self, session_id: UUID) -> AsyncIterator[SessionHandle]:
        async with self._engine.begin() as conn:
            stored = await conn.scalar(
                select(interview_sessions.c.state)
                .where(interview_sessions.c.session_id == session_id)
                .with_for_update()
            )
            if stored is None:
                raise SessionNotFoundError(session_id)
            yield _PostgresHandle(conn, SessionState.model_validate(stored))

    def locked(self, session_id: UUID) -> AbstractAsyncContextManager[SessionHandle]:
        return self._locked(session_id)
