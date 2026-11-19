"""2단계 주제 후보 고르기. LLM 없이 규칙으로만 고릅니다.

LLM은 여기서 고른 후보마다 질문 재료를 쓰기만 하고, 어떤 심볼을 주제로 삼을지는 정하지 않습니다.
"""

import re
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass
from itertools import groupby

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


# TypeScript·JavaScript 테스트 관례입니다 (#72). 폴더 이름과 `이름.test.ts`, `이름.spec.js` 같은 파일 이름입니다.
_SCRIPT_TEST_DIRS = frozenset({"__tests__", "__mocks__"})
_SCRIPT_TEST_NAME = re.compile(r"\.(?:test|spec)\.[cm]?[jt]sx?$")


def is_test_path(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    return (
        any(p in {"test", "tests", "testing"} or p in _SCRIPT_TEST_DIRS for p in parts[:-1])
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
        or _SCRIPT_TEST_NAME.search(name) is not None
    )


# 경로 조각에 이 단어가 들어 있으면 지금 쓰는 코드가 아니거나 자동 생성 코드로 봅니다.
_INACTIVE_WORDS = ("legacy", "deprecated", "backup", "archive", "migration", "example", "sample")
_INACTIVE_PARTS = frozenset({"old", "bak"})
_SOURCE_SUFFIX = re.compile(r"\.(?:py|[cm]?[jt]sx?)$")


def is_inactive_path(path: str) -> bool:
    parts = [_SOURCE_SUFFIX.sub("", p.lower()) for p in path.split("/")]
    return any(p in _INACTIVE_PARTS or any(w in p for w in _INACTIVE_WORDS) for p in parts)


def is_eligible(symbol: SymbolRef) -> bool:
    return (
        line_count(symbol) >= MIN_LINES
        and not is_test_path(symbol.path)
        and not is_inactive_path(symbol.path)
    )


# 실제 동작하는 줄이 이보다 적으면 껍데기 코드로 보고 주제로 쓰지 않습니다.
# 예: 메서드마다 로그 한 줄이나 다른 메서드 호출만 있는 클래스. LLM이 없는 설계 의도를 지어내기 쉽습니다(PR #54 리뷰).
MIN_LOGIC_LINES = 5
_DOCSTRING_QUOTES = ('"""', "'''")
_HEADER = re.compile(r"(?:@|(?:async\s+)?def\s|class\s)")
_LOG_CALL = re.compile(r"(?:\w+\.)*(?:log|logger|logging|_log|_logger|console)\.\w+\(|print\(")
_NO_OP = frozenset({"pass", "...", "return", "return None", ")", "]", "}", "{", "):", "]:"})


def logic_line_count(code: str) -> int:
    """심볼 코드에서 실제로 동작하는 줄 수를 셉니다.

    빈 줄, 주석, 문서 문자열, 정의 머리줄(def, class, 데코레이터), 로그·print 호출, pass, 빈 return은 빼고 셉니다.
    여러 줄짜리 호출의 이어지는 줄은 그대로 세므로, 실제보다 많게 셀 수는 있어도 적게 세지는 않습니다.
    """
    count = 0
    in_docstring: str | None = None
    # 문서 문자열이 아닌 여러 줄 문자열 값(예: 프롬프트) 안입니다. 이 줄들은 셉니다.
    in_string: str | None = None
    for raw in code.splitlines():
        line = raw.strip()
        if in_docstring is not None:
            if in_docstring in line:
                in_docstring = None
            continue
        if in_string is not None:
            count += 1
            if line.count(in_string) % 2 == 1:
                in_string = None
            continue
        quote = next((q for q in _DOCSTRING_QUOTES if line.lstrip("rRbBuUfF").startswith(q)), None)
        if quote is not None:
            body = line.lstrip("rRbBuUfF")[len(quote) :]
            if quote not in body:
                in_docstring = quote
            continue
        if (
            not line
            or line.startswith(("#", "//"))
            or _HEADER.match(line)
            or _LOG_CALL.match(line)
            or line in _NO_OP
        ):
            continue
        count += 1
        in_string = next((q for q in _DOCSTRING_QUOTES if line.count(q) % 2 == 1), None)
    return count


_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_SCRIPT_HEADER = re.compile(
    r"(?:export\s+)?(?:default\s+)?(?:abstract\s+)?(?:async\s+)?(?:function\b|class\s)"
)
_SCRIPT_NO_OP = frozenset(
    {"}", "};", "})", "});", "},", ")", ");", "]", "];", "return;", "{", "{}", "/>", ">"}
)


def script_logic_line_count(code: str) -> int:
    """TypeScript·JavaScript 심볼 코드에서 실제로 동작하는 줄 수를 셉니다 (#72).

    빈 줄, 주석, 함수·클래스 머리줄, 데코레이터, console 같은 로그 호출, 닫는 괄호만 있는 줄은 빼고 셉니다.
    JSX 태그로 시작하는 줄(`<div>`, `</Button>`, `/>`)도 뺍니다. 마크업뿐인 컴포넌트가 껍데기 판별을 통과하지 않게 합니다.
    태그 사이의 글자와 `{items.map(...)}` 같은 식은 그대로 셉니다.
    변수에 담은 화살표 함수의 머리줄과 메서드 머리줄도 그대로 세므로 대체로 많게 셉니다.
    `<`로 시작하는 줄은 모두 태그로 보므로, 줄 맨 앞에 온 제네릭이나 비교 연산은 세지 않습니다.
    문자열 안의 주석 모양(`"/* */"`)은 가리지 않습니다.
    """
    count = 0
    for raw in _BLOCK_COMMENT.sub("", code).splitlines():
        line = raw.strip()
        if (
            not line
            or line.startswith(("//", "@", "<"))
            or _SCRIPT_HEADER.match(line)
            or _LOG_CALL.match(line)
            or line in _SCRIPT_NO_OP
        ):
            continue
        count += 1
    return count


def has_enough_logic(code: str, path: str = "") -> bool:
    """`path`의 확장자가 TypeScript·JavaScript면 그 문법 기준으로, 아니면 Python 기준으로 셉니다."""
    script = _SOURCE_SUFFIX.search(path.lower()) is not None and not path.lower().endswith(".py")
    count = script_logic_line_count(code) if script else logic_line_count(code)
    return count >= MIN_LOGIC_LINES


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
    ordered = _alternate_markup(chosen, label_of)
    return [Candidate(symbol=s, related_choices=_related(s, symbols)) for s in ordered]


# 화면 마크업(JSX)을 함께 담는 파일입니다. 컴포넌트는 마크업 때문에 줄 수가 많습니다.
_MARKUP_SUFFIXES = (".tsx", ".jsx")


def is_markup_path(path: str) -> bool:
    return path.lower().endswith(_MARKUP_SUFFIXES)


def _alternate_markup(chosen: list[SymbolRef], label_of: LabelOf) -> list[SymbolRef]:
    """화면 컴포넌트 파일(.tsx, .jsx)의 후보와 나머지 파일의 후보를 번갈아 놓습니다 (#72).

    줄 수가 많은 순으로만 고르면 마크업이 긴 컴포넌트가 앞자리를 다 차지해서,
    API 라우트나 서버 로직처럼 짧지만 동작이 있는 코드가 주제가 되지 못합니다.
    기여 라벨 순서는 그대로 두고, 같은 라벨 안에서만 번갈아 놓습니다. 각 묶음 안의 순서(줄 수 순)도 그대로입니다.
    .tsx, .jsx가 없는 레포(Python 레포 등)는 순서가 바뀌지 않습니다.
    """
    ordered: list[SymbolRef] = []
    for _, group in groupby(chosen, key=lambda s: _LABEL_RANK[label_of(s)]):
        items = list(group)
        # 가장 앞선 후보가 속한 쪽부터 시작해 맨 앞 후보는 바뀌지 않습니다.
        first_is_markup = is_markup_path(items[0].path)
        first = deque(s for s in items if is_markup_path(s.path) == first_is_markup)
        second = deque(s for s in items if is_markup_path(s.path) != first_is_markup)
        while first or second:
            if first:
                ordered.append(first.popleft())
            if second:
                ordered.append(second.popleft())
    return ordered


def _related(main: SymbolRef, symbols: list[SymbolRef]) -> tuple[SymbolRef, ...]:
    # 주 심볼 안의 메서드(클래스 주제)나 안쪽 함수(TS/JS 함수 주제)와 같은 파일의 다른 함수가 꼬리질문 하이라이트 대상입니다.
    same_file = [s for s in symbols if s.path == main.path and s.symbol_id != main.symbol_id]
    inside = [s for s in same_file if overlaps(s, main)]
    outside = [s for s in same_file if not overlaps(s, main)]
    return tuple((inside + outside)[:MAX_RELATED_CHOICES])
