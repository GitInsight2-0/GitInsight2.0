"""저장소 계약 테스트. 메모리 구현과 Postgres 구현이 같은 테스트를 통과해야 합니다.

Postgres 구현은 postgres 표시로 기본 실행에서 빠집니다.
실행: TEST_DATABASE_URL=postgresql://... uv run pytest -m postgres api/tests/test_repository.py
"""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from gitinsight_api.brain.postgres import PostgresSessionRepository
from gitinsight_api.brain.protocol import SessionNotFoundError
from gitinsight_api.brain.repository import InMemorySessionRepository, SessionRepository
from gitinsight_api.brain.state import SessionState, TopicProgress, TurnRecord
from gitinsight_contracts.interview import Action, BrainReply, InterviewMode

NOW = datetime(2026, 11, 10, tzinfo=UTC)


@pytest.fixture(params=["memory", pytest.param("postgres", marks=pytest.mark.postgres)])
def repository(request: pytest.FixtureRequest) -> SessionRepository:
    if request.param == "postgres":
        return PostgresSessionRepository(request.getfixturevalue("pg_engine"))
    return InMemorySessionRepository()


def make_state() -> SessionState:
    return SessionState(
        session_id=uuid4(),
        analysis_id="a",
        mode=InterviewMode.TEXT,
        duration_sec=600,
        started_at=NOW,
        topic_order=["t1"],
        main_topic_count=1,
        progress=TopicProgress(topic_id="t1"),
        last_question="질문입니다?",
        last_action=Action.ASK,
        last_highlight=None,
    )


def make_turn(state: SessionState, index: int = 0) -> TurnRecord:
    turn_id = uuid4()
    return TurnRecord(
        session_id=state.session_id,
        turn_id=turn_id,
        index=index,
        topic_id="t1",
        question="질문입니다?",
        transcript="답변",
        judgment=None,
        fallback_reason=None,
        reply=BrainReply(
            turn_id=turn_id,
            action=Action.NEXT_TOPIC,
            utterance="다음입니다.",
            highlight=None,
            topic_id="t1",
            depth=0,
            remaining_sec=500,
            done=False,
        ),
        received_at=NOW,
    )


async def test_commit_saves_state_and_turn(repository: SessionRepository) -> None:
    state = make_state()
    await repository.create(state)
    turn = make_turn(state)

    async with repository.locked(state.session_id) as handle:
        updated = handle.state.model_copy(update={"turn_count": 1})
        await handle.commit(updated, turn)

    stored = await repository.get(state.session_id)
    assert stored is not None and stored.turn_count == 1
    assert [t.turn_id for t in await repository.list_turns(state.session_id)] == [turn.turn_id]


async def test_leaving_without_commit_saves_nothing(repository: SessionRepository) -> None:
    state = make_state()
    await repository.create(state)

    with pytest.raises(RuntimeError, match="중간 실패"):
        async with repository.locked(state.session_id) as handle:
            handle.state.turn_count = 99
            raise RuntimeError("중간 실패")

    stored = await repository.get(state.session_id)
    assert stored is not None and stored.turn_count == 0


async def test_same_turn_id_cannot_be_saved_twice(repository: SessionRepository) -> None:
    state = make_state()
    await repository.create(state)
    turn = make_turn(state)
    async with repository.locked(state.session_id) as handle:
        await handle.commit(handle.state, turn)

    with pytest.raises(ValueError, match="이미 저장된 turn_id"):
        async with repository.locked(state.session_id) as handle:
            await handle.commit(handle.state, turn)


async def test_get_turn_returns_saved_turn(repository: SessionRepository) -> None:
    state = make_state()
    await repository.create(state)
    turn = make_turn(state)
    async with repository.locked(state.session_id) as handle:
        await handle.commit(handle.state, turn)

    async with repository.locked(state.session_id) as handle:
        assert await handle.get_turn(turn.turn_id) == turn
        assert await handle.get_turn(uuid4()) is None


async def test_lock_serializes_concurrent_updates(repository: SessionRepository) -> None:
    state = make_state()
    await repository.create(state)

    async def increment() -> None:
        async with repository.locked(state.session_id) as handle:
            current = handle.state.turn_count
            await asyncio.sleep(0.01)
            await handle.commit(handle.state.model_copy(update={"turn_count": current + 1}))

    await asyncio.gather(*(increment() for _ in range(5)))

    stored = await repository.get(state.session_id)
    assert stored is not None and stored.turn_count == 5


async def test_unknown_session_raises_not_found(repository: SessionRepository) -> None:
    with pytest.raises(SessionNotFoundError):
        async with repository.locked(uuid4()):
            pass


async def test_stored_state_and_turns_read_back_equal(repository: SessionRepository) -> None:
    state = make_state().model_copy(update={"owner_id": "alice", "persona_keywords": ["캐시"]})
    await repository.create(state)
    turns = [make_turn(state, index) for index in range(3)]
    # 저장한 순서와 상관없이 index 순서로 돌려줍니다.
    for turn in (turns[0], turns[2], turns[1]):
        async with repository.locked(state.session_id) as handle:
            await handle.commit(handle.state, turn)

    assert await repository.get(state.session_id) == state
    assert await repository.list_turns(state.session_id) == turns
    assert await repository.get(uuid4()) is None
    assert await repository.list_turns(uuid4()) == []


async def test_same_session_cannot_be_created_twice(repository: SessionRepository) -> None:
    state = make_state()
    await repository.create(state)

    with pytest.raises(ValueError, match="이미 있는 세션"):
        await repository.create(state)
