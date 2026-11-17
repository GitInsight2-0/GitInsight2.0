"""분석 요청 → 워커 실행 → 결과 저장 → 면접 시작까지 메모리 저장소로 이어서 확인합니다."""

import asyncio
from pathlib import Path

import pytest
from pydantic import SecretStr

from gitinsight_api.analyses.service import (
    NO_CONTEXT,
    AnalysisLaunchError,
    AnalysisService,
    LaunchContext,
)
from gitinsight_api.analyses.store import AnalysisKey, AnalysisRecord, InMemoryAnalysisStore
from gitinsight_api.brain.repository import InMemorySessionRepository
from gitinsight_api.brain.service import InterviewBrain
from gitinsight_api.llm import LLMTimeoutError
from gitinsight_api.llm.fake import FakeLLM, FakeStep
from gitinsight_contracts.analysis import AnalysisResult
from gitinsight_contracts.api import AnalysisStatus
from gitinsight_contracts.interview import InterviewMode, SessionCreate
from gitinsight_worker.contribution import Requester
from gitinsight_worker.git import Cloner, git_cloner
from gitinsight_worker.jobs import (
    INTERRUPTED_ERROR,
    CloneFor,
    InProcessLauncher,
    execute,
    github_cloner,
    requester_from_login,
)

from worker_test_support import make_origin, material

REPO = "owner/repo"


@pytest.fixture
def origin(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "origin"
    return path, make_origin(path)


def local_cloner(origin: Path) -> CloneFor:
    def clone_for(record: AnalysisRecord, _context: LaunchContext) -> Cloner:
        return git_cloner(str(origin), record.key.commit_sha, allowed_protocols="file")

    return clone_for


def ok_llm() -> FakeLLM:
    return FakeLLM(responder=lambda *_: FakeStep(material()))


def launcher_for(
    store: InMemoryAnalysisStore, origin: Path, llm: FakeLLM, work: Path
) -> InProcessLauncher:
    work.mkdir(exist_ok=True)
    return InProcessLauncher(
        store,
        llm,
        clone_for=local_cloner(origin),
        work_dir=work,
    )


async def test_request_runs_analysis_and_result_starts_interview(
    origin: tuple[Path, str], tmp_path: Path
) -> None:
    store = InMemoryAnalysisStore()
    launcher = launcher_for(store, origin[0], ok_llm(), tmp_path / "work")
    service = AnalysisService(store, launcher)

    created = await service.request(REPO, origin[1], "u1")
    await launcher.join()

    record = await store.get_record(created.analysis_id)
    assert record is not None and record.state.status is AnalysisStatus.DONE
    result = await store.get(created.analysis_id)
    assert isinstance(result, AnalysisResult)
    assert (result.analysis_id, result.repo_full_name, result.commit_sha) == (
        created.analysis_id,
        REPO,
        origin[1],
    )
    # 같은 요청은 이제 캐시에서 바로 돌려줍니다.
    again = await service.request(REPO, origin[1], "u1")
    assert (again.analysis_id, again.cached) == (created.analysis_id, True)
    # 저장된 결과로 면접을 시작합니다.
    brain = InterviewBrain(analyses=store, repository=InMemorySessionRepository(), llm=FakeLLM())
    _, first = await brain.start(
        SessionCreate(analysis_id=created.analysis_id, mode=InterviewMode.TEXT)
    )
    assert first.highlight is not None
    assert list((tmp_path / "work").iterdir()) == []


async def test_analysis_failure_is_saved_as_failed_with_last_progress(
    origin: tuple[Path, str], tmp_path: Path
) -> None:
    store = InMemoryAnalysisStore()
    llm = FakeLLM(responder=lambda *_: FakeStep(error=LLMTimeoutError("느림")))
    launcher = launcher_for(store, origin[0], llm, tmp_path / "work")

    created = await AnalysisService(store, launcher).request(REPO, origin[1], "u1")
    await launcher.join()

    record = await store.get_record(created.analysis_id)
    assert record is not None
    assert (record.state.status, record.state.error) == (AnalysisStatus.FAILED, "Stage2Error")
    assert record.state.progress == 0.5
    assert await store.get(created.analysis_id) is None


async def test_result_for_closed_record_is_dropped(
    origin: tuple[Path, str], tmp_path: Path
) -> None:
    store = InMemoryAnalysisStore()
    record, _ = await store.get_or_create(AnalysisKey(REPO, origin[1], "u1"))
    work = tmp_path / "work"
    work.mkdir()

    class ClosingLLM(FakeLLM):
        """분석 도중 서버가 다시 시작되어 기록이 닫힌 상황을 만듭니다."""

        async def complete_structured(self, *args, **kwargs):  # type: ignore[override]
            await store.fail_in_progress(INTERRUPTED_ERROR)
            return await super().complete_structured(*args, **kwargs)

    saved = await execute(
        store,
        record,
        ClosingLLM(responder=lambda *_: FakeStep(material())),
        clone=git_cloner(str(origin[0]), origin[1], allowed_protocols="file"),
        work_dir=work,
    )

    # 닫힌 기록에는 결과를 저장하지 않고, 기록은 실패 상태로 남습니다.
    assert not saved
    closed = await store.get_record(record.analysis_id)
    assert closed is not None and closed.state.error == INTERRUPTED_ERROR
    assert await store.get(record.analysis_id) is None


async def test_shutdown_cancels_and_restart_recovers(
    origin: tuple[Path, str], tmp_path: Path
) -> None:
    store = InMemoryAnalysisStore()
    started = asyncio.Event()

    class SlowLLM(FakeLLM):
        async def complete_structured(self, *args, **kwargs):  # type: ignore[override]
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("취소되어야 합니다")

    launcher = launcher_for(store, origin[0], SlowLLM(), tmp_path / "work")
    created = await AnalysisService(store, launcher).request(REPO, origin[1], "u1")
    await started.wait()

    await launcher.aclose()

    # 취소는 실패로 보고하지 않아서 기록은 진행 중으로 남고, 내려받은 레포는 지워집니다.
    record = await store.get_record(created.analysis_id)
    assert record is not None and record.state.status is AnalysisStatus.STAGE2
    assert list((tmp_path / "work").iterdir()) == []

    # 다음 서버 시작에서 정리하면 화면이 실패를 알 수 있고, 같은 요청은 새로 분석합니다.
    restarted = launcher_for(store, origin[0], ok_llm(), tmp_path / "work")
    assert await restarted.recover() == 1
    record = await store.get_record(created.analysis_id)
    assert record is not None and record.state.error == INTERRUPTED_ERROR
    again = await AnalysisService(store, restarted).request(REPO, origin[1], "u1")
    assert again.analysis_id != created.analysis_id
    await restarted.join()


async def test_concurrent_analyses_are_limited(origin: tuple[Path, str], tmp_path: Path) -> None:
    store = InMemoryAnalysisStore()
    running = 0
    peak = 0

    class CountingLLM(FakeLLM):
        async def complete_structured(self, *args, **kwargs):  # type: ignore[override]
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01)
            running -= 1
            return await super().complete_structured(*args, **kwargs)

    work = tmp_path / "work"
    work.mkdir()
    launcher = InProcessLauncher(
        store,
        CountingLLM(responder=lambda *_: FakeStep(material())),
        clone_for=local_cloner(origin[0]),
        work_dir=work,
        max_concurrent=1,
    )
    service = AnalysisService(store, launcher)

    for user in ("u1", "u2", "u3"):
        await service.request(REPO, origin[1], user)
    await launcher.join()

    # 분석 하나 안에서는 LLM을 동시에 2개까지 부릅니다. 분석끼리는 겹치지 않습니다.
    assert peak <= 2
    records = [r for r in store._records.values()]
    assert {r.state.status for r in records} == {AnalysisStatus.DONE}


@pytest.mark.parametrize("repo", ["owner/repo/../x", "owner/repo.git?x=1", "owner", "a/b c"])
async def test_github_cloner_rejects_bad_repo_names(repo: str) -> None:
    store = InMemoryAnalysisStore()
    record, _ = await store.get_or_create(AnalysisKey(repo, "a" * 40, "u1"))

    with pytest.raises(ValueError, match="repo_full_name"):
        github_cloner(record, NO_CONTEXT)


async def test_bad_repo_name_fails_the_request_without_starting(tmp_path: Path) -> None:
    store = InMemoryAnalysisStore()
    launcher = InProcessLauncher(store, ok_llm(), work_dir=tmp_path)

    with pytest.raises(AnalysisLaunchError, match="ValueError"):
        await AnalysisService(store, launcher).request("owner/../evil", "a" * 40, "u1")

    [record] = list(store._records.values())
    assert record.state.status is AnalysisStatus.FAILED
    assert record.state.error == "ValueError"


async def test_launch_uses_requester_context_for_clone_and_labels(tmp_path: Path) -> None:
    store = InMemoryAnalysisStore()
    seen: list[tuple[str, LaunchContext]] = []

    def clone_for(record: AnalysisRecord, context: LaunchContext) -> Cloner:
        seen.append(("clone", context))
        return lambda _dest: None

    def requester_for(record: AnalysisRecord, context: LaunchContext) -> Requester | None:
        seen.append(("requester", context))
        return None

    launcher = InProcessLauncher(
        store, ok_llm(), clone_for=clone_for, requester_for=requester_for, work_dir=tmp_path
    )
    context = LaunchContext(github_token=SecretStr("ghu_secret"), login="octo")

    await AnalysisService(store, launcher).request(REPO, "a" * 40, "4242", context)
    await launcher.join()

    assert seen == [("clone", context), ("requester", context)]


async def test_requester_from_login_uses_noreply_addresses() -> None:
    store = InMemoryAnalysisStore()
    record, _ = await store.get_or_create(AnalysisKey(REPO, "a" * 40, "4242"))

    requester = requester_from_login(record, LaunchContext(login="octo"))

    assert requester is not None
    assert requester.emails == {
        "octo@users.noreply.github.com",
        "4242+octo@users.noreply.github.com",
    }
    # 계정 이름을 모르면(요청자 정보 없이 시작한 분석) 본인 기여를 맞추지 않습니다.
    lab, _ = await store.get_or_create(AnalysisKey(REPO, "a" * 40, "lab"))
    assert requester_from_login(lab, NO_CONTEXT) is None
