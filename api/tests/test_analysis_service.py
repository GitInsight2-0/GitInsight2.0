import asyncio

import pytest

from gitinsight_api.analyses.service import AnalysisService
from gitinsight_api.analyses.store import AnalysisRecord, InMemoryAnalysisStore
from gitinsight_contracts.api import AnalysisStatus
from gitinsight_contracts.fixtures import load_analysis

SAMPLE = load_analysis()
REPO, SHA = SAMPLE.repo_full_name, SAMPLE.commit_sha


class RecordingLauncher:
    def __init__(self, error: Exception | None = None) -> None:
        self.launched: list[AnalysisRecord] = []
        self.error = error

    async def launch(self, record: AnalysisRecord) -> None:
        self.launched.append(record)
        # 실제 구현처럼 다른 요청이 끼어들 틈을 줍니다.
        await asyncio.sleep(0)
        if self.error is not None:
            raise self.error


@pytest.fixture
def store() -> InMemoryAnalysisStore:
    return InMemoryAnalysisStore()


async def test_first_request_launches_once(store: InMemoryAnalysisStore) -> None:
    launcher = RecordingLauncher()
    service = AnalysisService(store, launcher)

    created = await service.request(REPO, SHA, "u1")
    again = await service.request(REPO, SHA, "u1")

    assert (created.status, created.cached) == (AnalysisStatus.QUEUED, False)
    # 진행 중이면 새로 시작하지 않고 같은 분석을 돌려줍니다.
    assert (again.analysis_id, again.cached) == (created.analysis_id, False)
    assert len(launcher.launched) == 1


async def test_concurrent_requests_launch_once(store: InMemoryAnalysisStore) -> None:
    launcher = RecordingLauncher()
    service = AnalysisService(store, launcher)

    results = await asyncio.gather(*(service.request(REPO, SHA, "u1") for _ in range(5)))

    assert len({r.analysis_id for r in results}) == 1
    assert len(launcher.launched) == 1


async def test_done_result_is_returned_as_cached(store: InMemoryAnalysisStore) -> None:
    launcher = RecordingLauncher()
    service = AnalysisService(store, launcher)
    first = await service.request(REPO, SHA, "u1")
    await store.save_result(
        first.analysis_id, SAMPLE.model_copy(update={"analysis_id": first.analysis_id})
    )

    again = await service.request(REPO, SHA, "u1")

    assert (again.analysis_id, again.status, again.cached) == (
        first.analysis_id,
        AnalysisStatus.DONE,
        True,
    )
    assert len(launcher.launched) == 1


async def test_launch_failure_marks_failed_so_retry_can_start(
    store: InMemoryAnalysisStore,
) -> None:
    service = AnalysisService(store, RecordingLauncher(error=ConnectionError("queue down")))

    with pytest.raises(ConnectionError):
        await service.request(REPO, SHA, "u1")
    [record] = list(store._records.values())
    assert record.state.status is AnalysisStatus.FAILED
    assert record.state.error == "ConnectionError"

    # 실패한 기록에 막히지 않고 다시 요청할 수 있습니다.
    retry = AnalysisService(store, RecordingLauncher())
    again = await retry.request(REPO, SHA, "u1")
    assert again.analysis_id != record.analysis_id


async def test_state_is_visible_only_to_requester(store: InMemoryAnalysisStore) -> None:
    service = AnalysisService(store, RecordingLauncher())
    created = await service.request(REPO, SHA, "u1")

    mine = await service.state(created.analysis_id, "u1")

    assert mine is not None and mine.status is AnalysisStatus.QUEUED
    assert await service.state(created.analysis_id, "u2") is None
    assert await service.state("missing", "u1") is None
