"""분석 전체 흐름: 내려받기 → 1단계 → 2단계 → 내려받은 레포 삭제.

2단계는 코드 본문이 필요해서, 레포를 지우기 전에 같은 작업 폴더에서 이어서 실행합니다.
진행률은 1단계 0~0.5, 2단계 0.5~0.95입니다.
`done` 상태는 결과를 저장한 쪽(#12)이 보고합니다. 저장 전에 done이 보이면 결과 없는 완료 상태가 생기기 때문입니다.
"""

import asyncio
import logging
import tempfile
from pathlib import Path

from gitinsight_api.llm import LLMClient
from gitinsight_contracts.analysis import AnalysisResult
from gitinsight_contracts.api import AnalysisState, AnalysisStatus
from gitinsight_worker.git import Cloner
from gitinsight_worker.stage1 import (
    PROGRESS_CLONED,
    PROGRESS_STAGE1_DONE,
    PROGRESS_STARTED,
    StatusReporter,
    analyze_tree,
    remove_tree,
)
from gitinsight_worker.stage2 import ContributionOf, run_stage2

logger = logging.getLogger(__name__)

PROGRESS_STAGE2_END = 0.95


def _state(status: AnalysisStatus, progress: float, commit_sha: str) -> AnalysisState:
    return AnalysisState(status=status, progress=progress, commit_sha=commit_sha)


async def run_analysis(
    clone: Cloner,
    commit_sha: str,
    reporter: StatusReporter,
    llm: LLMClient,
    *,
    analysis_id: str,
    repo_full_name: str,
    contribution_of: ContributionOf | None = None,
    model: str | None = None,
    work_dir: Path | None = None,
) -> AnalysisResult:
    """레포를 분석해 AnalysisResult를 돌려줍니다. 성공, 실패, 취소 모두 내려받은 레포를 지웁니다.

    실패하면 `failed` 상태(예외 종류만)를 보고한 뒤 예외를 그대로 올립니다.
    취소는 failed로 보고하지 않습니다.
    """
    checkout = Path(tempfile.mkdtemp(prefix="gitinsight-", dir=work_dir))
    try:
        await reporter.report(_state(AnalysisStatus.STAGE1, PROGRESS_STARTED, commit_sha))
        await asyncio.to_thread(clone, checkout)
        await reporter.report(_state(AnalysisStatus.STAGE1, PROGRESS_CLONED, commit_sha))
        stage1 = await asyncio.to_thread(analyze_tree, checkout, commit_sha)
        await reporter.report(_state(AnalysisStatus.STAGE2, PROGRESS_STAGE1_DONE, commit_sha))

        async def progress(fraction: float) -> None:
            span = PROGRESS_STAGE2_END - PROGRESS_STAGE1_DONE
            value = PROGRESS_STAGE1_DONE + span * fraction
            await reporter.report(_state(AnalysisStatus.STAGE2, value, commit_sha))

        return await run_stage2(
            checkout,
            stage1,
            llm,
            analysis_id=analysis_id,
            repo_full_name=repo_full_name,
            contribution_of=contribution_of,
            model=model,
            progress=progress,
        )
    except Exception as error:
        logger.info("analysis_failed error=%s", type(error).__name__)
        await reporter.report(
            AnalysisState(
                status=AnalysisStatus.FAILED,
                progress=PROGRESS_STARTED,
                commit_sha=commit_sha,
                error=type(error).__name__,
            )
        )
        raise
    finally:
        await asyncio.to_thread(remove_tree, checkout)
