"""2단계 주제 후보 고르기. LLM 없이 규칙으로만 고릅니다.

LLM은 여기서 고른 후보마다 질문 재료를 쓰기만 하고, 어떤 심볼을 주제로 삼을지는 정하지 않습니다.
"""

import re
from collections import Counter, deque
from collections.abc import Callable, Iterable
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
# Java 테스트 관례입니다 (#182). `src/test/` 폴더는 위의 test 폴더 규칙으로 빠지고, 이름은 `UserServiceTest.java` 등입니다.
_JAVA_TEST_NAME = re.compile(r"(?:Tests?|IT)\.java$")


def is_test_path(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    return (
        any(p in {"test", "tests", "testing"} or p in _SCRIPT_TEST_DIRS for p in parts[:-1])
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
        or _SCRIPT_TEST_NAME.search(name) is not None
        or _JAVA_TEST_NAME.search(name) is not None
    )


# 경로 조각에 이 단어가 들어 있으면 지금 쓰는 코드가 아니거나 자동 생성 코드로 봅니다.
_INACTIVE_WORDS = ("legacy", "deprecated", "backup", "archive", "migration", "example", "sample")
_INACTIVE_PARTS = frozenset({"old", "bak"})
_SOURCE_SUFFIX = re.compile(r"\.(?:py|java|[cm]?[jt]sx?)$")
_SCRIPT_SUFFIX = re.compile(r"\.[cm]?[jt]sx?$")


def shared_dirs(paths: Iterable[str]) -> tuple[str, ...]:
    """모든 경로가 함께 들어 있는 폴더입니다. 레포의 코드가 한 폴더 아래에 다 있으면 그 폴더 경로입니다.

    레포 전체가 `samples/` 아래에 있으면 그건 지금 쓰지 않는 예제 폴더가 아니라 그 레포의 패키지 이름입니다
    (예: spring-petclinic의 `org/springframework/samples/petclinic`, #182).
    그래서 지금 쓰지 않는 경로인지 볼 때 이 폴더는 빼고 봅니다.
    """
    # 테스트 코드는 어차피 후보에서 빠지므로 셈에 넣지 않습니다. 넣으면 `src/main`과 `src/test`가 갈라져 `src`만 남습니다.
    split = [p.split("/")[:-1] for p in paths if not is_test_path(p)]
    if not split:
        return ()
    common: list[str] = []
    for parts in zip(*split, strict=False):
        if len(set(parts)) != 1:
            break
        common.append(parts[0])
    return tuple(common)


# Java 패키지 이름의 앞부분(도메인과 프로젝트 이름) 단계 수입니다: com/example/demo, org/springframework/samples
_JAVA_PACKAGE_PREFIX = 3


def _without_java_package_prefix(parts: list[str]) -> list[str]:
    """Java 소스 폴더(`java/`) 바로 아래 패키지 이름의 앞 세 단계를 뺍니다 (#182).

    `com.example`은 Spring Initializr의 기본 패키지라 학생 프로젝트에 아주 흔합니다.
    패키지 이름에 들어간 example, samples를 지금 쓰지 않는 코드로 보면 Java 코드가 모두 빠집니다.
    그보다 깊은 패키지(`com/x/demo/legacy/`)는 그대로 봅니다.
    """
    if not parts or not parts[-1].lower().endswith(".java") or "java" not in parts[:-1]:
        return parts
    root = parts.index("java")
    package_end = min(root + 1 + _JAVA_PACKAGE_PREFIX, len(parts) - 1)
    return parts[: root + 1] + parts[package_end:]


def is_inactive_path(path: str, shared: tuple[str, ...] = ()) -> bool:
    """지금 쓰지 않는 코드(legacy, example 등)의 경로인지입니다. shared는 레포의 코드가 모두 들어 있는 폴더라 빼고 봅니다."""
    parts = path.split("/")
    if shared and tuple(parts[: len(shared)]) == shared:
        parts = parts[len(shared) :]
    parts = _without_java_package_prefix(parts)
    lowered = [_SOURCE_SUFFIX.sub("", p.lower()) for p in parts]
    return any(p in _INACTIVE_PARTS or any(w in p for w in _INACTIVE_WORDS) for p in lowered)


def is_eligible(symbol: SymbolRef, shared: tuple[str, ...] = ()) -> bool:
    return (
        line_count(symbol) >= MIN_LINES
        and not is_test_path(symbol.path)
        and not is_inactive_path(symbol.path, shared)
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


_JAVA_HEADER = re.compile(
    r"(?:(?:public|private|protected|abstract|final|static|sealed|non-sealed|strictfp)\s+)*"
    r"(?:class|interface|enum|record|@interface)\s"
)
# 메서드·생성자 머리줄입니다: `public User create(String name) throws IOException {`
_JAVA_METHOD_HEADER = re.compile(
    r"^[\w<>\[\],.?@ ]*\b\w+\s*\([^;{}]*\)\s*(?:throws\s+[\w., ]+)?\s*\{$"
)
# 필드 선언과 값만 넣는 지역 변수 선언입니다: `private final UserRepository repo;`, `private int count = 0;`
_JAVA_FIELD = re.compile(
    r"^(?:(?:private|protected|public|static|final|transient|volatile)\s+)*"
    r"[\w.<>\[\]?, ]+\s+\w+(?:\s*=\s*[^;()]*)?;$"
)
# getter와 setter의 본문입니다: `return name;`, `return this.name;`, `this.name = name;`
_JAVA_ACCESSOR = re.compile(r"^(?:return\s+(?:this\.)?\w+;|this\.\w+\s*=\s*\w+;)$")


def java_logic_line_count(code: str) -> int:
    """Java 심볼 코드에서 실제로 동작하는 줄 수를 셉니다 (#182).

    TypeScript·JavaScript 규칙(빈 줄, 주석, 어노테이션 `@`, 로그 호출, 닫는 괄호)에 더해,
    클래스·메서드 머리줄, 필드 선언, getter·setter 본문(`return name;`, `this.name = name;`)을 뺍니다.
    엔티티·DTO처럼 필드만 있거나 getter·setter뿐인 클래스가 주제가 되지 않게 하기 위해서입니다.
    Lombok(`@Getter`, `@Data`)을 쓰면 메서드가 소스에 없어 필드 선언만 남는데, 이것도 같이 빠집니다.
    """
    count = 0
    for raw in _BLOCK_COMMENT.sub("", code).splitlines():
        line = raw.strip()
        if (
            not line
            or line.startswith(("//", "@", "*"))
            or _JAVA_HEADER.match(line)
            or _JAVA_METHOD_HEADER.match(line)
            or _JAVA_FIELD.match(line)
            or _JAVA_ACCESSOR.match(line)
            or _LOG_CALL.match(line)
            or line in _SCRIPT_NO_OP
        ):
            continue
        count += 1
    return count


def has_enough_logic(code: str, path: str = "") -> bool:
    """`path`의 확장자에 맞는 문법 기준으로 셉니다. 모르는 확장자는 Python 기준입니다."""
    if path.lower().endswith(".java"):
        count = java_logic_line_count(code)
    elif is_script_path(path):
        count = script_logic_line_count(code)
    else:
        count = logic_line_count(code)
    return count >= MIN_LOGIC_LINES


def overlaps(a: SymbolRef, b: SymbolRef) -> bool:
    return a.path == b.path and a.start_line <= b.end_line and b.start_line <= a.end_line


def _unverified(_symbol: SymbolRef) -> ContributionLabel:
    return ContributionLabel.UNVERIFIED


def select_candidates(symbols: list[SymbolRef], label_of: LabelOf = _unverified) -> list[Candidate]:
    """주제 후보를 우선순위대로 돌려줍니다. 앞에서부터 쓰고, 생성에 실패하면 다음 후보로 넘어갑니다."""
    shared = shared_dirs(s.path for s in symbols)
    eligible = [s for s in symbols if is_eligible(s, shared)]
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
    ordered = _alternate_groups(chosen, label_of)
    return [Candidate(symbol=s, related_choices=_related(s, symbols)) for s in ordered]


# 화면 마크업(JSX)을 함께 담는 파일입니다. 컴포넌트는 마크업 때문에 줄 수가 많습니다.
_MARKUP_SUFFIXES = (".tsx", ".jsx")


def is_markup_path(path: str) -> bool:
    return path.lower().endswith(_MARKUP_SUFFIXES)


def is_script_path(path: str) -> bool:
    """TypeScript·JavaScript 파일인지 봅니다. Python과 Java는 아닙니다."""
    return _SCRIPT_SUFFIX.search(path.lower()) is not None


def _alternate(items: list[SymbolRef], in_first: Callable[[SymbolRef], bool]) -> list[SymbolRef]:
    """두 묶음을 번갈아 놓습니다. 맨 앞 후보가 속한 묶음부터 시작하고, 각 묶음 안의 순서는 그대로입니다."""
    if not items:
        return []
    head = in_first(items[0])
    first = deque(s for s in items if in_first(s) == head)
    second = deque(s for s in items if in_first(s) != head)
    ordered: list[SymbolRef] = []
    while first or second:
        if first:
            ordered.append(first.popleft())
        if second:
            ordered.append(second.popleft())
    return ordered


def _alternate_groups(chosen: list[SymbolRef], label_of: LabelOf) -> list[SymbolRef]:
    """줄 수가 많은 순으로만 고르면 한쪽 코드가 앞자리를 다 차지하므로, 두 번 번갈아 놓습니다.

    1. TypeScript·JavaScript 안에서 화면 컴포넌트 파일(.tsx, .jsx)과 나머지 파일(.ts, .js)을 번갈아 놓습니다 (#72).
       마크업이 긴 컴포넌트에 밀려 API 라우트처럼 짧지만 동작이 있는 코드가 주제가 되지 못하는 것을 막습니다.
    2. TypeScript·JavaScript와 그 밖의 언어(Python)를 번갈아 놓습니다 (#129).
       TypeScript는 컴포넌트와 클로저에 함수를 모아 한 심볼이 길고 Python은 클래스와 메서드로 나뉘어 짧아서,
       두 언어가 섞인 레포에서는 Python이 대부분이어도 주제가 TypeScript로 쏠립니다.

    기여 라벨 순서는 그대로 두고, 같은 라벨 안에서만 번갈아 놓습니다. 각 묶음 안의 순서(줄 수 순)도 그대로입니다.
    맨 앞 후보는 바뀌지 않습니다. 한 언어만 있는 레포는 2번에서 순서가 바뀌지 않습니다.
    한계: 파일 수 비율대로 나누지 않습니다. 한 언어가 조금뿐인 레포도 그 후보가 남아 있는 동안은 한 자리 건너 하나씩 옵니다.
    """
    ordered: list[SymbolRef] = []
    for _, group in groupby(chosen, key=lambda s: _LABEL_RANK[label_of(s)]):
        items = list(group)
        scripts = iter(
            _alternate(
                [s for s in items if is_script_path(s.path)], lambda s: is_markup_path(s.path)
            )
        )
        # 스크립트 자리에 1번에서 정한 순서대로 다시 넣습니다. 다른 언어의 자리는 그대로입니다.
        mixed = [next(scripts) if is_script_path(s.path) else s for s in items]
        ordered.extend(_alternate(mixed, lambda s: is_script_path(s.path)))
    return ordered


def _related(main: SymbolRef, symbols: list[SymbolRef]) -> tuple[SymbolRef, ...]:
    # 주 심볼 안의 메서드(클래스 주제)나 안쪽 함수(TS/JS 함수 주제)와 같은 파일의 다른 함수가 꼬리질문 하이라이트 대상입니다.
    same_file = [s for s in symbols if s.path == main.path and s.symbol_id != main.symbol_id]
    inside = [s for s in same_file if overlaps(s, main)]
    outside = [s for s in same_file if not overlaps(s, main)]
    return tuple((inside + outside)[:MAX_RELATED_CHOICES])
