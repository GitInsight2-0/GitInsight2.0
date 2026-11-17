"""분석 작업 실행: 저장소의 분석 기록 하나를 받아 run_analysis를 돌리고 결과를 저장합니다 (#12).

- StoreReporter: 진행 상태를 저장소(AnalysisStore.update_state)에 씁니다.
- execute: 분석 하나를 끝까지 돌리고 결과를 save_result로 저장합니다.
- InProcessLauncher: 첫 배포(#19)에서 쓰는 Launcher입니다. 서버 프로세스 안에서 asyncio 작업으로 분석을 돌립니다.

내려받는 주소와 요청자 신원은 함수로 받습니다.
요청에 RepoAccess(요청자 GitHub 토큰과 이메일, #18)가 있으면 그 토큰으로 내려받고 요청자 기준으로 기여 라벨을 붙입니다.
없으면 공개 레포 https 주소와 "요청자 없음"(기여 라벨은 모두 확인 불가)을 씁니다 (데모 스냅샷, 테스트).
"""

import asyncio
import logging
import re
from collections.abc import Callable
from pathlib import Path

from gitinsight_api.analyses.access import RepoAccess
from gitinsight_api.analyses.store import AnalysisRecord, AnalysisStore
from gitinsight_api.llm import LLMClient
from gitinsight_contracts.api import AnalysisState, AnalysisStatus
from gitinsight_worker.contribution import Requester
from gitinsight_worker.git import Cloner, git_cloner
from gitinsight_worker.pipeline import run_analysis

logger = logging.getLogger(__name__)

# 서버 프로세스 안에서 동시에 돌리는 분석 수입니다. 분석 하나가 LLM을 동시에 2개까지 부릅니다.
MAX_CONCURRENT_ANALYSES = 2
# 서버가 다시 시작되어 사라진 분석의 오류 이름입니다.
INTERRUPTED_ERROR = "InterruptedAnalysis"
# 계약 AnalysisResult.repo_full_name과 같은 형식입니다.
_REPO_FULL_NAME = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")

CloneFor = Callable[[AnalysisRecord], Cloner]
RequesterFor = Callable[[AnalysisRecord], Requester | None]
AccessCloneFor = Callable[[AnalysisRecord, RepoAccess], Cloner]


def _github_url(record: AnalysisRecord) -> str:
    repo = record.key.repo_full_name
    if not _REPO_FULL_NAME.fullmatch(repo) or ".." in repo:
        raise ValueError("repo_full_name은 owner/name 형식이어야 합니다")
    return f"https://github.com/{repo}.git"


def public_cloner(record: AnalysisRecord) -> Cloner:
    """공개 레포를 GitHub https 주소로 고정 커밋에 내려받습니다."""
    return git_cloner(_github_url(record), record.key.commit_sha)


def user_token_cloner(record: AnalysisRecord, access: RepoAccess) -> Cloner:
    """요청자 본인의 GitHub 토큰으로 내려받습니다. 비공개 레포도 요청자가 읽을 수 있으면 받습니다."""
    return git_cloner(_github_url(record), record.key.commit_sha, token=access.token)


def requester_of(access: RepoAccess) -> Requester:
    """GitHub 인증 이메일과 noreply 주소로 요청자 신원을 만듭니다."""
    return Requester.of(emails=access.emails, login=access.login, user_id=access.github_id)


def no_requester(_record: AnalysisRecord) -> Requester | None:
    return None


class StoreReporter:
    """워커의 진행 상태를 저장소의 분석 기록에 씁니다."""

    def __init__(self, store: AnalysisStore, analysis_id: str) -> None:
        self._store = store
        self._analysis_id = analysis_id

    async def report(self, state: AnalysisState) -> None:
        await self._store.update_state(self._analysis_id, state)


async def execute(
    store: AnalysisStore,
    record: AnalysisRecord,
    llm: LLMClient,
    *,
    clone: Cloner,
    requester: Requester | None = None,
    model: str | None = None,
    work_dir: Path | None = None,
) -> bool:
    """분석 하나를 돌리고 결과를 저장합니다. 저장까지 끝나면 True입니다.

    분석이 실패하면 run_analysis가 failed 상태를 저장소에 보고하므로 여기서는 False만 돌려줍니다.
    결과 저장이 거부되면(오래되어 교체된 기록 등) 결과를 버리고 False를 돌려줍니다.
    취소는 그대로 올립니다. 진행 중으로 남은 기록은 재시작 정리나 오래됨 판단이 닫습니다.
    """
    try:
        result = await run_analysis(
            clone,
            record.key.commit_sha,
            StoreReporter(store, record.analysis_id),
            llm,
            analysis_id=record.analysis_id,
            repo_full_name=record.key.repo_full_name,
            requester=requester,
            model=model,
            work_dir=work_dir,
        )
    except Exception as error:
        logger.info("analysis_job_failed error=%s", type(error).__name__)
        return False
    try:
        await store.save_result(record.analysis_id, result)
    except Exception as error:
        logger.warning("analysis_result_not_saved error=%s", type(error).__name__)
        # 저장소 장애로 저장하지 못했다면, 진행 중으로 남지 않게 실패로 닫습니다.
        # 이미 닫힌 기록이면 update_state가 무시합니다.
        await store.update_state(
            record.analysis_id,
            AnalysisState(
                status=AnalysisStatus.FAILED,
                progress=1.0,
                commit_sha=record.key.commit_sha,
                error=type(error).__name__,
            ),
        )
        return False
    return True


class InProcessLauncher:
    """서버 프로세스 안에서 분석을 asyncio 작업으로 돌립니다 (AnalysisService의 Launcher).

    서버를 시작할 때 recover()로 지난 프로세스에서 진행 중이던 기록을 닫고,
    서버를 끌 때 aclose()로 돌고 있는 작업을 취소합니다.
    대기 중인 분석은 동시 실행 수가 비면 시작합니다. 기다리는 동안에는 상태를 갱신하지 않으므로,
    대기가 STALE_AFTER(10분)를 넘지 않을 만큼 동시 실행 수를 둡니다.
    """

    def __init__(
        self,
        store: AnalysisStore,
        llm: LLMClient,
        *,
        clone_for: CloneFor = public_cloner,
        requester_for: RequesterFor = no_requester,
        access_clone_for: AccessCloneFor = user_token_cloner,
        model: str | None = None,
        work_dir: Path | None = None,
        max_concurrent: int = MAX_CONCURRENT_ANALYSES,
    ) -> None:
        self._store = store
        self._llm = llm
        self._clone_for = clone_for
        self._requester_for = requester_for
        self._access_clone_for = access_clone_for
        self._model = model
        self._work_dir = work_dir
        self._slots = asyncio.Semaphore(max_concurrent)
        self._tasks: set[asyncio.Task[bool]] = set()

    async def recover(self) -> int:
        """지난 프로세스에서 진행 중이던 기록을 failed로 닫습니다. 서버를 시작할 때 한 번 부릅니다."""
        closed = await self._store.fail_in_progress(INTERRUPTED_ERROR)
        if closed:
            logger.info("analysis_recovered closed=%d", closed)
        return closed

    async def launch(self, record: AnalysisRecord, access: RepoAccess | None = None) -> None:
        # 주소 형식이 잘못되었으면 작업을 만들지 않고 바로 실패시킵니다. 서비스가 기록을 failed로 닫습니다.
        if access is None:
            clone = self._clone_for(record)
            requester = self._requester_for(record)
        else:
            # 토큰은 이 작업의 메모리에만 있고, 작업이 끝나면 함께 사라집니다.
            clone = self._access_clone_for(record, access)
            requester = requester_of(access)
        task = asyncio.create_task(self._run(record, clone, requester))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(
        self, record: AnalysisRecord, clone: Cloner, requester: Requester | None
    ) -> bool:
        async with self._slots:
            return await execute(
                self._store,
                record,
                self._llm,
                clone=clone,
                requester=requester,
                model=self._model,
                work_dir=self._work_dir,
            )

    async def join(self) -> None:
        """지금 돌고 있는 작업이 모두 끝날 때까지 기다립니다."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def aclose(self) -> None:
        """돌고 있는 작업을 취소합니다. 서버를 끌 때 부릅니다."""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
