"""리포트 저장소 계약 테스트. 메모리 구현과 Postgres 구현이 같은 테스트를 통과해야 합니다.

Postgres 구현은 postgres 표시로 기본 실행에서 빠집니다.
실행: TEST_DATABASE_URL=postgresql://... uv run pytest -m postgres api/tests/test_report_store.py
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from gitinsight_api.brain.postgres import PostgresSessionRepository
from gitinsight_api.brain.state import SessionState, TopicProgress
from gitinsight_api.report.postgres import PostgresReportStore
from gitinsight_api.report.service import (
    InMemoryReportStore,
    ReportAlreadyExistsError,
    ReportStore,
)
from gitinsight_contracts.interview import Action, InterviewMode
from gitinsight_contracts.report import FallbackReason, Report, ReportKind, TurnRow

NOW = datetime(2026, 11, 18, tzinfo=UTC)

# 리포트를 저장할 세션을 하나 만들고 session_id를 돌려줍니다.
NewSession = Callable[[], Awaitable[UUID]]


@pytest.fixture(params=["memory", pytest.param("postgres", marks=pytest.mark.postgres)])
def store_and_session(request: pytest.FixtureRequest) -> tuple[ReportStore, NewSession]:
    if request.param == "memory":

        async def any_session() -> UUID:
            return uuid4()

        return InMemoryReportStore(), any_session

    engine = request.getfixturevalue("pg_engine")
    sessions = PostgresSessionRepository(engine)

    async def stored_session() -> UUID:
        # reports.session_id는 interview_sessions를 가리키므로 세션이 먼저 있어야 합니다.
        state = SessionState(
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
        await sessions.create(state)
        return state.session_id

    return PostgresReportStore(engine), stored_session


def make_report(session_id: UUID, strength: str = "흐름 설명이 정확함") -> Report:
    return Report(
        report_id=uuid4(),
        session_id=session_id,
        kind=ReportKind.TURN_TABLE,
        competencies=[],
        topics=[],
        strengths=[strength],
        improvements=[],
        next_topics=[],
        turns=[
            TurnRow(
                turn_id=uuid4(),
                topic_id="t1",
                question="질문입니다?",
                transcript="답변입니다",
                intent=None,
                covered_points=["p1"],
                action=Action.NEXT_TOPIC,
                fallback_reason=FallbackReason.TIMEOUT,
            )
        ],
    )


async def test_saved_report_is_read_back_by_id_and_by_session(
    store_and_session: tuple[ReportStore, NewSession],
) -> None:
    store, new_session = store_and_session
    report = make_report(await new_session())

    await store.save(report)

    assert await store.get(report.report_id) == report
    assert await store.get_by_session(report.session_id) == report


async def test_missing_report_is_none(store_and_session: tuple[ReportStore, NewSession]) -> None:
    store, new_session = store_and_session

    assert await store.get(uuid4()) is None
    assert await store.get_by_session(await new_session()) is None


async def test_second_report_for_same_session_is_rejected(
    store_and_session: tuple[ReportStore, NewSession],
) -> None:
    # 세션 하나에 리포트는 하나입니다. 먼저 저장된 리포트가 그대로 남습니다.
    store, new_session = store_and_session
    session_id = await new_session()
    first = make_report(session_id)
    await store.save(first)

    with pytest.raises(ReportAlreadyExistsError):
        await store.save(make_report(session_id))

    assert await store.get_by_session(session_id) == first


async def test_reports_of_different_sessions_do_not_mix(
    store_and_session: tuple[ReportStore, NewSession],
) -> None:
    store, new_session = store_and_session
    one = make_report(await new_session())
    other = make_report(await new_session())
    await store.save(one)
    await store.save(other)

    assert await store.get_by_session(one.session_id) == one
    assert await store.get_by_session(other.session_id) == other


async def test_list_by_owner_returns_only_that_owners_reports_oldest_first(
    store_and_session: tuple[ReportStore, NewSession],
) -> None:
    # 이전 면접 비교(#77)는 본인 리포트만, 오래된 면접부터 봅니다.
    store, new_session = store_and_session
    first = make_report(await new_session(), "첫 번째")
    others = make_report(await new_session(), "다른 사람")
    no_owner = make_report(await new_session(), "소유자 없음")
    second = make_report(await new_session(), "두 번째")
    await store.save(first, "alice")
    await store.save(others, "bob")
    await store.save(no_owner)
    await store.save(second, "alice")

    mine = await store.list_by_owner("alice", 10)

    assert [item.report for item in mine] == [first, second]
    assert mine[0].created_at <= mine[1].created_at
    assert mine[0].created_at.tzinfo is not None
    assert await store.list_by_owner("carol", 10) == []


async def test_list_by_owner_keeps_the_most_recent_when_limited(
    store_and_session: tuple[ReportStore, NewSession],
) -> None:
    store, new_session = store_and_session
    saved = [make_report(await new_session(), f"{i}번째") for i in range(3)]
    for report in saved:
        await store.save(report, "alice")

    recent = await store.list_by_owner("alice", 2)

    assert [item.report for item in recent] == saved[1:]
