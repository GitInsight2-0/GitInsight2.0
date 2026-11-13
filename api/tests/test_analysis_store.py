"""분석 저장소 계약 테스트. Postgres 구현(#17 이후)이 생기면 STORES에 추가해 같은 테스트를 돌립니다."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from gitinsight_api.analyses.store import (
    STALE_AFTER,
    STALE_ERROR,
    UNKNOWN_ERROR,
    AnalysisKey,
    AnalysisRecordNotFoundError,
    AnalysisStore,
    Clock,
    InMemoryAnalysisStore,
)
from gitinsight_api.brain.repository import InMemorySessionRepository
from gitinsight_api.brain.service import InterviewBrain
from gitinsight_api.llm.fake import FakeLLM
from gitinsight_contracts.analysis import AnalysisResult
from gitinsight_contracts.api import AnalysisState, AnalysisStatus
from gitinsight_contracts.fixtures import load_analysis
from gitinsight_contracts.interview import InterviewMode, SessionCreate


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


StoreFactory = Callable[[Clock], AnalysisStore]
STORES: list[StoreFactory] = [lambda clock: InMemoryAnalysisStore(clock=clock)]

SAMPLE = load_analysis()
KEY = AnalysisKey(SAMPLE.repo_full_name, SAMPLE.commit_sha, user_id="u1")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(params=STORES, ids=["memory"])
def store(request: pytest.FixtureRequest, clock: FakeClock) -> AnalysisStore:
    return request.param(clock)


def result_for(analysis_id: str) -> AnalysisResult:
    return SAMPLE.model_copy(update={"analysis_id": analysis_id})


def stage1(progress: float = 0.2, *, error: str | None = None) -> AnalysisState:
    status = AnalysisStatus.FAILED if error else AnalysisStatus.STAGE1
    return AnalysisState(status=status, progress=progress, commit_sha=KEY.commit_sha, error=error)


async def test_new_key_creates_queued_record(store: AnalysisStore) -> None:
    record, created = await store.get_or_create(KEY)

    assert created
    assert record.key == KEY
    assert record.state.status is AnalysisStatus.QUEUED
    assert record.state.commit_sha == KEY.commit_sha
    assert await store.get_record(record.analysis_id) == record


async def test_in_progress_record_is_reused(store: AnalysisStore) -> None:
    first, _ = await store.get_or_create(KEY)
    await store.update_state(first.analysis_id, stage1())

    again, created = await store.get_or_create(KEY)

    assert not created
    assert again.analysis_id == first.analysis_id
    assert again.state.progress == 0.2


async def test_concurrent_requests_create_one_record(store: AnalysisStore) -> None:
    results = await asyncio.gather(*(store.get_or_create(KEY) for _ in range(10)))

    assert sum(created for _, created in results) == 1
    assert len({record.analysis_id for record, _ in results}) == 1


async def test_other_user_gets_own_record(store: AnalysisStore) -> None:
    # 기여 라벨이 요청자마다 달라서 다른 사람의 결과를 재사용하지 않습니다.
    mine, _ = await store.get_or_create(KEY)
    theirs, created = await store.get_or_create(
        AnalysisKey(KEY.repo_full_name, KEY.commit_sha, "u2")
    )

    assert created
    assert theirs.analysis_id != mine.analysis_id


async def test_done_record_is_reused_and_result_is_revalidated(store: AnalysisStore) -> None:
    record, _ = await store.get_or_create(KEY)
    assert await store.get(record.analysis_id) is None

    await store.save_result(record.analysis_id, result_for(record.analysis_id))
    again, created = await store.get_or_create(KEY)

    assert not created
    assert again.state.status is AnalysisStatus.DONE
    assert again.state.progress == 1.0
    assert await store.get(record.analysis_id) == result_for(record.analysis_id)


async def test_failed_record_is_not_reused(store: AnalysisStore) -> None:
    first, _ = await store.get_or_create(KEY)
    await store.update_state(first.analysis_id, stage1(error="CloneError"))

    second, created = await store.get_or_create(KEY)

    assert created
    assert second.analysis_id != first.analysis_id
    # 이전 기록은 id로 계속 조회할 수 있습니다.
    old = await store.get_record(first.analysis_id)
    assert old is not None and old.state.status is AnalysisStatus.FAILED


async def test_stale_in_progress_record_is_replaced(store: AnalysisStore, clock: FakeClock) -> None:
    first, _ = await store.get_or_create(KEY)
    clock.now += STALE_AFTER - timedelta(seconds=1)
    assert not (await store.get_or_create(KEY))[1]

    # 상태 갱신 없이 상한을 넘기면 워커가 죽은 것으로 봅니다.
    clock.now += timedelta(seconds=1)
    second, created = await store.get_or_create(KEY)

    assert created
    assert second.analysis_id != first.analysis_id
    # 교체된 옛 기록은 failed로 닫혀서, 화면은 실패를 알 수 있고 늦게 온 옛 워커는 막힙니다(PR #37 리뷰).
    old = await store.get_record(first.analysis_id)
    assert old is not None
    assert (old.state.status, old.state.error) == (AnalysisStatus.FAILED, STALE_ERROR)


async def test_slow_old_worker_cannot_write_after_replacement(
    store: AnalysisStore, clock: FakeClock
) -> None:
    first, _ = await store.get_or_create(KEY)
    clock.now += STALE_AFTER
    second, _ = await store.get_or_create(KEY)

    # 죽은 줄 알았던 옛 워커가 늦게 진행률과 결과를 보냅니다.
    await store.update_state(first.analysis_id, stage1(0.9))
    with pytest.raises(ValueError, match="진행 중"):
        await store.save_result(first.analysis_id, result_for(first.analysis_id))

    old = await store.get_record(first.analysis_id)
    assert old is not None and old.state.status is AnalysisStatus.FAILED
    assert await store.get(first.analysis_id) is None
    # 새 기록은 그대로 진행 중이고, 같은 키의 재요청은 새 기록을 재사용합니다.
    again, created = await store.get_or_create(KEY)
    assert (again.analysis_id, created) == (second.analysis_id, False)


async def test_progress_update_keeps_record_alive(store: AnalysisStore, clock: FakeClock) -> None:
    first, _ = await store.get_or_create(KEY)
    clock.now += STALE_AFTER - timedelta(seconds=1)
    await store.update_state(first.analysis_id, stage1(0.5))
    clock.now += STALE_AFTER - timedelta(seconds=1)

    again, created = await store.get_or_create(KEY)

    assert not created
    assert again.analysis_id == first.analysis_id


@pytest.mark.parametrize(
    ("error", "stored"),
    [
        ("CloneError", "CloneError"),
        ("git 출력: secret.py 내용이 섞인 메시지", UNKNOWN_ERROR),
        ("Error: token=abc", UNKNOWN_ERROR),
    ],
)
async def test_failed_error_keeps_only_exception_kind(
    store: AnalysisStore, error: str, stored: str
) -> None:
    record, _ = await store.get_or_create(KEY)

    await store.update_state(record.analysis_id, stage1(error=error))

    saved = await store.get_record(record.analysis_id)
    assert saved is not None and saved.state.error == stored


async def test_finished_record_ignores_late_progress(store: AnalysisStore) -> None:
    record, _ = await store.get_or_create(KEY)
    await store.save_result(record.analysis_id, result_for(record.analysis_id))

    await store.update_state(record.analysis_id, stage1(0.3))

    saved = await store.get_record(record.analysis_id)
    assert saved is not None and saved.state.status is AnalysisStatus.DONE


async def test_done_state_only_through_save_result(store: AnalysisStore) -> None:
    record, _ = await store.get_or_create(KEY)
    done = AnalysisState(status=AnalysisStatus.DONE, progress=1.0, commit_sha=KEY.commit_sha)

    with pytest.raises(ValueError, match="save_result"):
        await store.update_state(record.analysis_id, done)


@pytest.mark.parametrize(
    "change",
    [
        {"analysis_id": "other"},
        {"repo_full_name": "someone/else"},
    ],
)
async def test_mismatched_result_is_rejected(store: AnalysisStore, change: dict) -> None:
    record, _ = await store.get_or_create(KEY)
    result = result_for(record.analysis_id).model_copy(update=change)

    with pytest.raises(ValueError, match="기록"):
        await store.save_result(record.analysis_id, result)
    assert await store.get(record.analysis_id) is None


async def test_result_only_for_in_progress_record(store: AnalysisStore) -> None:
    record, _ = await store.get_or_create(KEY)
    await store.update_state(record.analysis_id, stage1(error="CloneError"))

    with pytest.raises(ValueError, match="진행 중"):
        await store.save_result(record.analysis_id, result_for(record.analysis_id))


async def test_unknown_record(store: AnalysisStore) -> None:
    assert await store.get_record("missing") is None
    assert await store.get("missing") is None
    with pytest.raises(AnalysisRecordNotFoundError):
        await store.update_state("missing", stage1())


async def test_store_works_as_interview_analysis_source(store: AnalysisStore) -> None:
    record, _ = await store.get_or_create(KEY)
    await store.save_result(record.analysis_id, result_for(record.analysis_id))
    brain = InterviewBrain(analyses=store, repository=InMemorySessionRepository(), llm=FakeLLM())

    _, first = await brain.start(
        SessionCreate(analysis_id=record.analysis_id, mode=InterviewMode.TEXT)
    )

    assert first.topic_id in {t.topic_id for t in SAMPLE.topics}
