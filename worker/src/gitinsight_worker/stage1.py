"""1단계 분석: 레포 구조, 언어 통계, 심볼 목록, 심볼별 기여 근거.

결과에는 원본 코드가 들어가지 않습니다. 내려받은 레포는 분석이 끝나거나 실패하면 지웁니다.
주제(Topic)를 만드는 2단계는 이 결과를 입력으로 받습니다.
내려받은 레포가 2단계 전에 지워지므로, 기여 근거(blame)는 내보내는 모든 심볼에 대해 여기서 미리 구합니다.

진행률(AnalysisState.progress)은 분석 전체 기준입니다. 1단계는 앞쪽 절반(0~0.5)입니다.
"""

import asyncio
import logging
import os
import shutil
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from gitinsight_contracts.analysis import LanguageStat
from gitinsight_contracts.api import AnalysisState, AnalysisStatus
from gitinsight_contracts.common import SymbolRef
from gitinsight_worker.contribution import (
    UNKNOWN,
    BlameError,
    BlameLine,
    Contribution,
    Requester,
    blame_file,
    summarize,
)
from gitinsight_worker.git import Cloner
from gitinsight_worker.languages import PYTHON, count_languages, language_of
from gitinsight_worker.symbols import extract_python

logger = logging.getLogger(__name__)

# 의존성과 빌드 결과는 레포 작성자의 코드가 아니므로 건너뜁니다.
IGNORED_DIRS = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".tox"}
)

PROGRESS_STARTED = 0.0
PROGRESS_CLONED = 0.2
PROGRESS_STAGE1_DONE = 0.5


@dataclass(frozen=True)
class Stage1Result:
    commit_sha: str
    languages: list[LanguageStat]
    symbol_count: int
    symbols: list[SymbolRef]
    # symbol_id별 기여 라벨과 최근 커밋입니다. 작성자 정보는 들어가지 않습니다.
    contributions: dict[str, Contribution] = field(default_factory=dict)


class StatusReporter(Protocol):
    """분석 상태를 저장하는 쪽(T11의 DB 구현)이 맡습니다."""

    async def report(self, state: AnalysisState) -> None: ...


def iter_repo_files(root: Path) -> list[str]:
    """분석 대상 파일의 상대 경로(슬래시 구분)를 정렬해서 돌려줍니다. 심볼릭 링크는 따라가지 않습니다."""
    found: list[str] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS)
        for name in files:
            full = Path(current) / name
            # 레포 밖의 파일을 읽지 않도록 링크는 건너뜁니다.
            if full.is_symlink():
                continue
            found.append(full.relative_to(root).as_posix())
    return sorted(found)


def _blame_or_none(root: Path, path: str, commit_sha: str) -> list[BlameLine] | None:
    if not (root / ".git").exists():
        return None
    try:
        return blame_file(root, path, commit_sha)
    except BlameError as error:
        # 한 파일의 blame 실패로 분석 전체를 멈추지 않습니다. 라벨은 "확인 불가"가 됩니다.
        logger.info("blame_failed error=%s", type(error).__name__)
        return None


def analyze_tree(root: Path, commit_sha: str, requester: Requester | None = None) -> Stage1Result:
    paths = iter_repo_files(root)
    symbols: list[SymbolRef] = []
    contributions: dict[str, Contribution] = {}
    symbol_count = 0
    for path in paths:
        if language_of(path) != PYTHON:
            continue
        extracted = extract_python((root / path).read_bytes(), path, commit_sha)
        symbols.extend(extracted.symbols)
        symbol_count += extracted.definition_count
        if not extracted.symbols:
            continue
        blame = _blame_or_none(root, path, commit_sha)
        for symbol in extracted.symbols:
            contributions[symbol.symbol_id] = (
                UNKNOWN
                if blame is None
                else summarize(blame, symbol.start_line, symbol.end_line, requester)
            )
    return Stage1Result(
        commit_sha=commit_sha,
        languages=count_languages(paths),
        symbol_count=symbol_count,
        symbols=symbols,
        contributions=contributions,
    )


def _make_writable_and_retry(func: Callable[[str], object], path: str, _error: object) -> None:
    # Windows에서는 .git의 읽기 전용 파일 때문에 삭제가 막힙니다.
    os.chmod(path, stat.S_IWRITE)
    func(path)


def remove_tree(path: Path) -> None:
    shutil.rmtree(path, onexc=_make_writable_and_retry)


def _state(status: AnalysisStatus, progress: float, commit_sha: str) -> AnalysisState:
    return AnalysisState(status=status, progress=progress, commit_sha=commit_sha)


async def run_stage1(
    clone: Cloner,
    commit_sha: str,
    reporter: StatusReporter,
    *,
    requester: Requester | None = None,
    work_dir: Path | None = None,
) -> Stage1Result:
    """레포를 내려받아 분석하고 상태를 보고합니다. 끝나면 성공과 실패 모두 내려받은 레포를 지웁니다.

    실패하면 `failed` 상태를 보고한 뒤 예외를 그대로 올립니다. 보고하는 오류에는 예외 종류만 넣습니다.
    작업이 취소되면 failed로 보고하지 않고 정리만 한 뒤 취소를 그대로 올립니다.
    `requester`가 없으면 기여 라벨은 모두 "확인 불가"이고, 최근 커밋은 그대로 구합니다.
    """
    checkout = Path(tempfile.mkdtemp(prefix="gitinsight-", dir=work_dir))
    try:
        await reporter.report(_state(AnalysisStatus.STAGE1, PROGRESS_STARTED, commit_sha))
        await asyncio.to_thread(clone, checkout)
        await reporter.report(_state(AnalysisStatus.STAGE1, PROGRESS_CLONED, commit_sha))
        result = await asyncio.to_thread(analyze_tree, checkout, commit_sha, requester)
        await reporter.report(_state(AnalysisStatus.STAGE1, PROGRESS_STAGE1_DONE, commit_sha))
        return result
    except Exception as error:
        logger.info("stage1_failed error=%s", type(error).__name__)
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
