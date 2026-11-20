"""1단계 분석: 레포 구조, 언어 통계, 심볼 목록, 심볼별 기여 근거.

결과에는 원본 코드가 들어가지 않습니다. 내려받기, 2단계 연결, 레포 삭제는 pipeline.run_analysis가 맡습니다.
주제(Topic)를 만드는 2단계는 이 결과를 입력으로 받습니다.
내려받은 레포가 2단계 전에 지워지므로, 기여 근거(blame)는 내보내는 모든 심볼에 대해 여기서 미리 구합니다.

진행률(AnalysisState.progress)은 분석 전체 기준입니다. 1단계는 앞쪽 절반(0~0.5)입니다.
"""

import logging
import os
import shutil
import stat
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from gitinsight_contracts.analysis import LanguageStat
from gitinsight_contracts.api import AnalysisState
from gitinsight_contracts.common import SymbolRef
from gitinsight_worker.contribution import (
    BLAME_BUDGET_S,
    BLAME_TIMEOUT_S,
    UNKNOWN,
    BlameError,
    BlameLine,
    Contribution,
    Requester,
    blame_file,
    summarize,
)
from gitinsight_worker.java_symbols import extract_java
from gitinsight_worker.languages import (
    JAVA,
    JAVASCRIPT,
    PYTHON,
    TYPESCRIPT,
    count_languages,
    language_of,
)
from gitinsight_worker.script_symbols import extract_script
from gitinsight_worker.symbols import ExtractedFile, extract_python

logger = logging.getLogger(__name__)

# 의존성과 빌드 결과는 레포 작성자의 코드가 아니므로 건너뜁니다.
IGNORED_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        "dist",
        "build",
        ".tox",
        ".next",
        # Gradle 캐시입니다 (#182). 소스 폴더 이름으로는 쓰이지 않습니다.
        ".gradle",
    }
)
# Maven 빌드 결과 폴더입니다 (#182). `target`은 소스 폴더 이름으로도 쓰일 수 있어서(`src/target/`)
# 같은 폴더에 `pom.xml`이 있을 때만 건너뜁니다.
MAVEN_OUTPUT_DIR = "target"
MAVEN_POM = "pom.xml"


def _skipped_dir(name: str, files: list[str]) -> bool:
    return name in IGNORED_DIRS or (name == MAVEN_OUTPUT_DIR and MAVEN_POM in files)


# 언어별 심볼 추출기입니다. 여기 없는 언어는 파일 수만 셉니다.
_EXTRACTORS: dict[str, Callable[[bytes, str, str], ExtractedFile]] = {
    PYTHON: extract_python,
    JAVASCRIPT: extract_script,
    TYPESCRIPT: extract_script,
    JAVA: extract_java,
}
# 압축한 배포용 파일은 사람이 쓴 코드가 아니라 심볼을 뽑지 않습니다.
_MINIFIED_SUFFIXES = (".min.js", ".min.mjs", ".min.cjs")

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
    # symbol_id별로 레포 안 다른 곳에서 이름으로 참조한 횟수입니다 (#58). 2단계 프롬프트에만 쓰고 저장하지 않습니다.
    # 데코레이터가 붙었거나 특수 메서드라 프레임워크가 부를 수 있는 심볼은 넣지 않습니다(알 수 없음).
    # 참조는 Python 파일에서만 세므로 TypeScript·JavaScript 심볼도 넣지 않습니다 (#72).
    references: dict[str, int] = field(default_factory=dict)


class NoSupportedCodeError(Exception):
    """레포에 지원하는 언어(Python, TypeScript, JavaScript, Java)의 함수·클래스가 없습니다 (#70, #72, #182).

    분석 기록의 error에는 이 예외 종류 이름이 저장되고, 웹은 이 이름으로 "지원하지 않는 언어" 안내를 보여 줍니다.
    """


class StatusReporter(Protocol):
    """분석 상태를 저장하는 쪽(T11의 DB 구현)이 맡습니다."""

    async def report(self, state: AnalysisState) -> None: ...


def iter_repo_files(root: Path) -> list[str]:
    """분석 대상 파일의 상대 경로(슬래시 구분)를 정렬해서 돌려줍니다. 심볼릭 링크는 따라가지 않습니다."""
    found: list[str] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not _skipped_dir(d, files))
        for name in files:
            full = Path(current) / name
            # 레포 밖의 파일을 읽지 않도록 링크는 건너뜁니다.
            if full.is_symlink():
                continue
            found.append(full.relative_to(root).as_posix())
    return sorted(found)


def _blame_or_none(
    root: Path, path: str, commit_sha: str, deadline: float
) -> list[BlameLine] | None:
    remaining = deadline - time.monotonic()
    if remaining <= 0 or not (root / ".git").exists():
        return None
    try:
        return blame_file(root, path, commit_sha, timeout=min(BLAME_TIMEOUT_S, remaining))
    except BlameError as error:
        # 한 파일의 blame 실패로 분석 전체를 멈추지 않습니다. 라벨은 "확인 불가"가 됩니다.
        logger.info("blame_failed error=%s", type(error).__name__)
        return None


def analyze_tree(
    root: Path,
    commit_sha: str,
    requester: Requester | None = None,
    *,
    blame_budget_s: float = BLAME_BUDGET_S,
) -> Stage1Result:
    """`blame_budget_s`를 넘기면 남은 파일은 blame하지 않고 기여 근거를 "확인 불가"로 둡니다."""
    deadline = time.monotonic() + blame_budget_s
    paths = iter_repo_files(root)
    symbols: list[SymbolRef] = []
    contributions: dict[str, Contribution] = {}
    name_references: Counter[str] = Counter()
    self_references: dict[str, int] = {}
    implicit: set[str] = set()
    symbol_count = 0
    for path in paths:
        extract = _EXTRACTORS.get(language_of(path) or "")
        if extract is None or path.lower().endswith(_MINIFIED_SUFFIXES):
            continue
        extracted = extract((root / path).read_bytes(), path, commit_sha)
        symbols.extend(extracted.symbols)
        symbol_count += extracted.definition_count
        name_references.update(extracted.references)
        self_references.update(extracted.self_references)
        implicit.update(extracted.implicitly_called)
        if not extracted.symbols:
            continue
        blame = _blame_or_none(root, path, commit_sha, deadline)
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
        references={
            s.symbol_id: name_references[s.name] - self_references.get(s.symbol_id, 0)
            for s in symbols
            if s.symbol_id not in implicit and language_of(s.path) == PYTHON
        },
    )


def _make_writable_and_retry(func: Callable[[str], object], path: str, _error: object) -> None:
    # Windows에서는 .git의 읽기 전용 파일 때문에 삭제가 막힙니다.
    os.chmod(path, stat.S_IWRITE)
    func(path)


def remove_tree(path: Path) -> None:
    shutil.rmtree(path, onexc=_make_writable_and_retry)
