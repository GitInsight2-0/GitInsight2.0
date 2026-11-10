"""저장소 계약 테스트. Postgres 구현(Task 9)이 생기면 REPOSITORIES에 추가해 같은 테스트를 돌립니다."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from gitinsight_api.brain.protocol import SessionNotFoundError
from gitinsight_api.brain.repository import InMemorySessionRepository, SessionRepository
from gitinsight_api.brain.state import SessionState, TopicProgress, TurnRecord
from gitinsight_contracts.interview import Action, BrainReply, InterviewMode

REPOSITORIES: list[Callable[[], SessionRepository]] = [InMemorySessionRepository]
NOW = datetime(2026, 11, 10, tzinfo=UTC)


@pytest.fixture(params=REPOSITORIES, ids=lambda f: f.__name__)
def repository(request: pytest.FixtureRequest) -> SessionRepository:
    return request.param()


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
