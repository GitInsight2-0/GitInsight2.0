"""2단계 주제 후보 고르기. LLM 없이 규칙으로만 고릅니다.

LLM은 여기서 고른 후보마다 질문 재료를 쓰기만 하고, 어떤 심볼을 주제로 삼을지는 정하지 않습니다.
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from gitinsight_contracts.analysis import ContributionLabel
from gitinsight_contracts.common import SymbolRef

# 이보다 짧은 심볼은 2분 동안 물어볼 거리가 부족합니다.
MIN_LINES = 8
# 한 파일에서 주제가 몰리지 않게 합니다.
MAX_PER_FILE = 2
# 꼬리질문 하이라이트용으로 LLM에 보여 줄 같은 파일의 다른 심볼 수입니다.
MAX_RELATED_CHOICES = 8

# 기여 근거가 있는 코드를 먼저 고릅니다. 질문 순서와 같은 기준이고, 점수에는 쓰지 않습니다.
_LABEL_RANK = {
    ContributionLabel.VERIFIED: 0,
    ContributionLabel.SHARED: 1,
    ContributionLabel.UNVERIFIED: 2,
}

LabelOf = Callable[[SymbolRef], ContributionLabel]


@dataclass(frozen=True)
class Candidate:
    symbol: SymbolRef
    # 같은 파일의 다른 심볼입니다(클래스 주제면 그 메서드 포함). LLM은 이 안에서만 관련 심볼을 고릅니다.
    related_choices: tuple[SymbolRef, ...]


def line_count(symbol: SymbolRef) -> int:
    return symbol.end_line - symbol.start_line + 1


def is_test_path(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    return (
        any(p in {"test", "tests", "testing"} for p in parts[:-1])
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


# 경로 조각에 이 단어가 들어 있으면 지금 쓰는 코드가 아니거나 자동 생성 코드로 봅니다.
_INACTIVE_WORDS = ("legacy", "deprecated", "backup", "archive", "migration", "example", "sample")
_INACTIVE_PARTS = frozenset({"old", "bak"})


def is_inactive_path(path: str) -> bool:
    parts = [p.lower().removesuffix(".py") for p in path.split("/")]
    return any(p in _INACTIVE_PARTS or any(w in p for w in _INACTIVE_WORDS) for p in parts)


def is_eligible(symbol: SymbolRef) -> bool:
    return (
        line_count(symbol) >= MIN_LINES
        and not is_test_path(symbol.path)
        and not is_inactive_path(symbol.path)
    )


def overlaps(a: SymbolRef, b: SymbolRef) -> bool:
    return a.path == b.path and a.start_line <= b.end_line and b.start_line <= a.end_line


def _unverified(_symbol: SymbolRef) -> ContributionLabel:
    return ContributionLabel.UNVERIFIED


def select_candidates(symbols: list[SymbolRef], label_of: LabelOf = _unverified) -> list[Candidate]:
    """주제 후보를 우선순위대로 돌려줍니다. 앞에서부터 쓰고, 생성에 실패하면 다음 후보로 넘어갑니다."""
    eligible = [s for s in symbols if is_eligible(s)]
    ranked = sorted(
        eligible,
        key=lambda s: (_LABEL_RANK[label_of(s)], -line_count(s), s.symbol_id),
    )
    chosen: list[SymbolRef] = []
    per_file: Counter[str] = Counter()
    for symbol in ranked:
        if per_file[symbol.path] >= MAX_PER_FILE:
            continue
        if any(overlaps(symbol, c) for c in chosen):
            continue
        chosen.append(symbol)
        per_file[symbol.path] += 1
    return [Candidate(symbol=s, related_choices=_related(s, symbols)) for s in chosen]


def _related(main: SymbolRef, symbols: list[SymbolRef]) -> tuple[SymbolRef, ...]:
    # 주 심볼 안의 메서드(클래스 주제)와 같은 파일의 다른 함수가 꼬리질문 하이라이트 대상입니다.
    same_file = [s for s in symbols if s.path == main.path and s.symbol_id != main.symbol_id]
    inside = [s for s in same_file if overlaps(s, main)]
    outside = [s for s in same_file if not overlaps(s, main)]
    return tuple((inside + outside)[:MAX_RELATED_CHOICES])
