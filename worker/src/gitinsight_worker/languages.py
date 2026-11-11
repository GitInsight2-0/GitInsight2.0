"""파일 확장자로 언어별 파일 수를 셉니다."""

from collections import Counter
from collections.abc import Iterable
from pathlib import PurePosixPath

from gitinsight_contracts.analysis import LanguageStat

PYTHON = "Python"

LANGUAGE_BY_SUFFIX: dict[str, str] = {
    ".py": PYTHON,
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".go": "Go",
    ".sh": "Shell",
    ".md": "Markdown",
    ".yml": "YAML",
    ".yaml": "YAML",
    ".toml": "TOML",
    ".json": "JSON",
    ".html": "HTML",
    ".css": "CSS",
}


def language_of(path: str) -> str | None:
    return LANGUAGE_BY_SUFFIX.get(PurePosixPath(path).suffix.lower())


def count_languages(paths: Iterable[str]) -> list[LanguageStat]:
    """파일 수가 많은 언어부터, 같으면 이름 순으로 돌려줍니다."""
    counts = Counter(lang for path in paths if (lang := language_of(path)) is not None)
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [LanguageStat(language=lang, file_count=count) for lang, count in ordered]
