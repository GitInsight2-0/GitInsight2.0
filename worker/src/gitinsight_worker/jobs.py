"""분석 작업 실행: 저장소의 분석 기록 하나를 받아 run_analysis를 돌리고 결과를 저장합니다 (#12).

- StoreReporter: 진행 상태를 저장소(AnalysisStore.update_state)에 씁니다.
- execute: 분석 하나를 끝까지 돌리고 결과를 save_result로 저장합니다.
- InProcessLauncher: 첫 배포(#19)에서 쓰는 Launcher입니다. 서버 프로세스 안에서 asyncio 작업으로 분석을 돌립니다.

내려받는 방법과 요청자 신원은 분석 기록과 요청자 정보(LaunchContext)를 받는 함수로 정합니다.
기본은 GitHub https 주소에서 요청자 토큰으로 받고(github_cloner), 요청자 GitHub 계정의 noreply 이메일로
기여 라벨을 붙입니다(requester_from_login). 토큰과 계정 정보는 작업이 끝나면 버리고 저장하지 않습니다.
"""

import asyncio
import logging
import re
from collections.abc import Callable
from pathlib import Path

from gitinsight_api.analyses.service import NO_CONTEXT, LaunchContext
from gitinsight_api.analyses.store import AnalysisRecord, AnalysisStore
from gitinsight_api.llm import LLMClient
from gitinsight_contracts.api import AnalysisState, AnalysisStatus
from gitinsight_worker.contribution import Requester
from gitinsight_worker.git import GITHUB_HTTPS, Cloner, git_cloner
from gitinsight_worker.pipeline import run_analysis
from gitinsight_worker.version import ANALYZER_VERSION

logger = logging.getLogger(__name__)

# 서버 프로세스 안에서 동시에 돌리는 분석 수입니다. 분석 하나가 LLM을 동시에 2개까지 부릅니다.
MAX_CONCURRENT_ANALYSES = 2
# 서버가 다시 시작되어 사라진 분석의 오류 이름입니다.
INTERRUPTED_ERROR = "InterruptedAnalysis"
# 계약 AnalysisResult.repo_full_name과 같은 형식입니다.
_REPO_FULL_NAME = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")

CloneFor = Callable[[AnalysisRecord, LaunchContext], Cloner]
RequesterFor = Callable[[AnalysisRecord, LaunchContext], Requester | None]


def github_cloner(record: AnalysisRecord, context: LaunchContext) -> Cloner:
    """GitHub https 주소에서 고정 커밋을 내려받습니다. 요청자 토큰이 있으면 그 토큰으로 인증합니다."""
    repo = record.key.repo_full_name
    if not _REPO_FULL_NAME.fullmatch(repo) or ".." in repo:
        raise ValueError("repo_full_name은 owner/name 형식이어야 합니다")
    return git_cloner(
        f"{GITHUB_HTTPS}{repo}.git", record.key.commit_sha, token=context.github_token
    )


def requester_from_login(record: AnalysisRecord, context: LaunchContext) -> Requester | None:
    """요청자 GitHub 계정의 noreply 이메일로 본인 커밋을 맞춥니다.

    GitHub App에 이메일 읽기 권한이 없어 개인 이메일로 한 커밋은 맞추지 못합니다(확인 불가로 남습니다).
    """
    if not context.login:
        return None
    user_id = int(record.key.user_id) if record.key.user_id.isdigit() else None
    return Requester.of(login=context.login, user_id=user_id)


def no_requester(_record: AnalysisRecord, _context: LaunchContext) -> Requester | None:
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

    # 서비스가 캐시 키에 넣는 분석기 버전입니다 (#171).
    analyzer_version = ANALYZER_VERSION

    def __init__(
        self,
        store: AnalysisStore,
        llm: LLMClient,
        *,
        clone_for: CloneFor = github_cloner,
        requester_for: RequesterFor = requester_from_login,
        model: str | None = None,
        work_dir: Path | None = None,
        max_concurrent: int = MAX_CONCURRENT_ANALYSES,
    ) -> None:
        self._store = store
        self._llm = llm
        self._clone_for = clone_for
        self._requester_for = requester_for
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

    async def launch(self, record: AnalysisRecord, context: LaunchContext = NO_CONTEXT) -> None:
        # 주소 형식이 잘못되었으면 작업을 만들지 않고 바로 실패시킵니다. 서비스가 기록을 failed로 닫습니다.
        clone = self._clone_for(record, context)
        requester = self._requester_for(record, context)
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
