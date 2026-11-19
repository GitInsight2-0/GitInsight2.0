"""면접관이 일부러 하는 틀린 말의 재료를 서버 규칙으로 고릅니다 (#133, #142).

방식은 두 가지이고, 무엇을 쓸지는 여기서 정합니다. LLM은 고른 재료로 문장만 씁니다.
- absent_call: 주제 코드가 직접 부르지 않는 레포 함수를 부른다고 말합니다. 먼저 씁니다.
- inverted_point: 고를 함수가 없을 때만, 주제의 요점 하나를 반대로 말합니다.

absent_call 함수는 "주제 코드가 그 함수를 직접 부르지 않는다"가 확실해야 합니다.
그래서 주제 코드 범위의 식별자가 아니라 **주제 파일 전체 텍스트에 그 이름이 단어로 한 번도 나오지 않는 함수**만 고릅니다.
- 별칭 import(`import { postTurn as send }`, `from x import a as b`)는 원래 이름이 import 문에 남아 걸러집니다.
- 문자열로 부르는 동적 호출(`obj["save"]()`, `getattr(m, "save")`)과 범위 밖에서 붙인 별칭도 걸러집니다.
- 주석에만 이름이 나와도 빼는데, 후보가 줄어드는 쪽이라 안전합니다.
- 이름이 바뀌는 default 내보내기(`export default`, `module.exports =`)는 받는 쪽 이름을 알 수 없어 후보에서 뺍니다.
같은 파일의 함수는 정의가 파일 안에 있으므로 자연히 빠지고, 고른 함수는 늘 다른 파일에 있습니다.

그럴듯한 함수를 고르려고 아래도 뺍니다.
- 다른 언어의 함수, 테스트와 지금 쓰지 않는 경로(legacy 등)의 함수
- 같은 이름이 레포에 둘 이상인 함수(어느 것인지 모름), 밑줄로 시작하는 비공개 함수, 너무 짧은 이름
- 화면 컴포넌트(`.tsx`, `.jsx`의 대문자 이름): 부르는 것이 아니라 그리는 것이라 "호출하죠?"가 어색합니다
- 거꾸로 부르는 함수: 그 함수의 파일에 주제 심볼 이름이 나오면 주제를 부르는 쪽일 수 있어,
  "주제가 그 함수를 부르죠?"가 너무 쉽게 짚힙니다 (#144)
- 주제와 함께 넘겨지는 함수: 레포 어딘가에서 두 이름이 같은 줄에 나오면(`startVoiceSession(connectGemini, ...)`)
  한쪽이 다른 쪽을 받아 부를 수 있어 같은 이유로 뺍니다. 매개변수 이름으로 부르면 위 조건으로는 못 찾기 때문입니다 (#144)
- 프레임워크가 부르거나 아무도 이름으로 부르지 않는 Python 함수: 데코레이터가 붙은 함수(라우트 등)와
  레포 안 참조 수가 0인 함수입니다 (#144). 1단계가 이런 함수를 참조 수 목록에서 빼거나 0으로 세므로,
  참조 수가 1 이상인 Python 함수만 고릅니다. TypeScript·JavaScript는 참조 수가 없어 이 조건이 없습니다.

남은 것은 아래 순서로 고릅니다. 같은 입력이면 늘 같은 함수가 나옵니다.
1. 주제 파일이 import하는 모듈의 함수: 그 모듈을 가져다 쓰니 이 함수도 부를 것처럼 들립니다
2. 주제와 같은 폴더, 3. 같은 최상위 폴더
import하지도 않고 최상위 폴더도 다른 함수(예: `frontend/`의 화면 함수에 `app/agents/`의 함수)는 고르지 않습니다.
레포의 다른 영역이라 "부르죠?"가 어색하기 때문입니다. 그런 주제는 inverted_point로 갑니다.
같은 순위면 이 분석에서 다른 주제에 덜 고른 함수, 긴 함수(짧은 도우미보다 하는 일이 분명함), 경로와 줄 순서입니다.
같은 함수가 여러 주제에 반복해서 나오지 않게 하기 위해서입니다 (#144). 후보 순서대로 고르므로 결과는 늘 같습니다.

import한 모듈인지는 import 문의 모듈 경로를 함수 파일의 경로와 맞춰 봅니다 (#144).
- 상대 경로(`./api`, `from .judge import`)는 주제 파일 위치에서 풀어 경로가 같아야 합니다.
- 절대 경로(`gitinsight_api.brain.judge`, `@/lib/voice/api`)는 함수 파일 경로가 그 경로 조각으로 끝나야 합니다.
  그래서 파일 이름만 같은 다른 패키지의 모듈(`gitinsight_worker.misstatement`와 `api/.../brain/misstatement.py`)은 아닙니다.
- `from 패키지 import 모듈`도 `패키지.모듈`로 봅니다. 여러 줄에 걸친 import도 읽습니다.
순서에만 쓰므로 틀려도 틀린 말이 맞는 말이 되지는 않습니다.

기여 라벨은 쓰지 않습니다. 파일은 읽기만 하고 저장하지 않습니다.
"""

import logging
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from gitinsight_contracts.analysis import MisstatementKind
from gitinsight_contracts.common import SymbolKind, SymbolRef
from gitinsight_worker.candidates import is_inactive_path, is_markup_path, is_test_path
from gitinsight_worker.languages import PYTHON, language_of

logger = logging.getLogger(__name__)

# 이보다 짧은 이름(run, noop, load 등)은 흔하거나 하는 일이 불분명해 틀린 말로 그럴듯하지 않습니다.
MIN_NAME_LENGTH = 6

# import 문의 모듈 경로입니다. `[^;]*?`로 여러 줄에 걸친 `import { ... } from "..."`도 읽습니다.
_SCRIPT_FROM = re.compile(
    r"(?:^|[;\n])\s*(?:import|export)\b[^;]*?\bfrom\s*[\"']([^\"']+)[\"']", re.S
)
_SCRIPT_CALL = re.compile(r"\b(?:require|import)\s*\(\s*[\"']([^\"']+)[\"']\s*\)")
_SCRIPT_SIDE_EFFECT = re.compile(r"^\s*import\s*[\"']([^\"']+)[\"']", re.M)
_PY_FROM = re.compile(r"^\s*from\s+(\.*[\w.]*)\s+import\s+(\([^)]*\)|[^\n]*)", re.M)
_PY_IMPORT = re.compile(r"^\s*import\s+([^\n]+)", re.M)
_NAME = re.compile(r"[A-Za-z_]\w*")
# 경로 앞의 별칭(`@/`, `~/`)입니다. 어느 폴더인지 몰라 경로 끝 맞추기로 찾습니다.
_ALIAS_PREFIX = re.compile(r"^[@~]/")


class _Family(StrEnum):
    PYTHON = "python"
    SCRIPT = "script"


@dataclass(frozen=True)
class MisstatementPlan:
    """주제 하나에서 쓸 틀린 말 방식입니다. absent_call이면 부른다고 말할 함수가 있습니다."""

    kind: MisstatementKind
    absent_symbol: SymbolRef | None = None


def _family(path: str) -> _Family:
    return _Family.PYTHON if language_of(path) == PYTHON else _Family.SCRIPT


def _is_top_level(symbol: SymbolRef) -> bool:
    return "." not in symbol.symbol_id.split("::", 1)[1]


def _mentions(text: str, name: str) -> bool:
    """이름이 단어로 나오는지입니다. JavaScript 이름에 쓰는 `$`도 이름 글자로 봅니다."""
    return re.search(rf"(?<![\w$]){re.escape(name)}(?![\w$])", text) is not None


def _default_exported(text: str, name: str) -> bool:
    """받는 쪽이 이름을 새로 정하는 내보내기인지입니다 (`export default`, `module.exports =`)."""
    escaped = re.escape(name)
    patterns = (
        rf"export\s+default\s+(?:async\s+)?(?:function\s*\*?\s*)?{escaped}(?![\w$])",
        rf"module\.exports\s*=\s*(?:async\s+)?(?:function\s*\*?\s*)?{escaped}(?![\w$])",
        rf"export\s*\{{[^}}]*(?<![\w$]){escaped}\s+as\s+default\b",
    )
    return any(re.search(p, text) for p in patterns)


def _is_component(symbol: SymbolRef) -> bool:
    return is_markup_path(symbol.path) and symbol.name[:1].isupper()


def absent_choices(symbols: Iterable[SymbolRef]) -> list[SymbolRef]:
    """부른다고 말할 수 있는 레포 함수입니다. 주제와 상관없는 조건만 봅니다."""
    symbols = list(symbols)
    # 같은 이름의 최상위 정의가 둘 이상이면(다른 파일, 테스트 포함) 어느 것인지 몰라 뺍니다.
    names = Counter(s.name for s in symbols if _is_top_level(s))
    return [
        s
        for s in symbols
        if s.kind is SymbolKind.FUNCTION
        and _is_top_level(s)
        and names[s.name] == 1
        and len(s.name) >= MIN_NAME_LENGTH
        and not s.name.startswith("_")
        and not is_test_path(s.path)
        and not is_inactive_path(s.path)
        and not _is_component(s)
    ]


# ---- import한 모듈 찾기 ----


def _module_parts(path: str) -> tuple[str, ...]:
    """파일 경로를 모듈 경로 조각으로 바꿉니다. 확장자를 빼고, `index`, `__init__`이면 폴더입니다."""
    pure = PurePosixPath(path)
    parts = (*pure.parent.parts, pure.name.split(".", 1)[0])
    return parts[:-1] if parts[-1] in ("index", "__init__") else parts


@dataclass(frozen=True)
class _Import:
    """import한 모듈입니다. 상대 경로면 레포 안 경로로 풀어 정확히 맞추고(exact), 아니면 경로 끝으로 맞춥니다."""

    parts: tuple[str, ...]
    exact: bool

    def matches(self, module: tuple[str, ...]) -> bool:
        if not self.parts:
            return False
        if self.exact:
            return module == self.parts
        return module[-len(self.parts) :] == self.parts


def _resolve(base: tuple[str, ...], relative: Iterable[str]) -> tuple[str, ...] | None:
    """주제 파일 폴더(base)에서 상대 경로 조각을 풉니다. 레포 밖으로 나가면 None입니다."""
    parts = list(base)
    for piece in relative:
        if piece in ("", "."):
            continue
        if piece == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(piece)
    return tuple(parts)


def _strip_suffix(parts: tuple[str, ...]) -> tuple[str, ...]:
    """`./api.ts`처럼 확장자를 적은 경로도 모듈 경로 조각으로 맞춥니다."""
    if not parts:
        return parts
    return (
        _module_parts("/".join(parts) + ".x")
        if "." not in parts[-1]
        else _module_parts("/".join(parts))
    )


def _script_imports(text: str, base: tuple[str, ...]) -> list[_Import]:
    specs = [m.group(1) for m in _SCRIPT_FROM.finditer(text)]
    specs += [m.group(1) for m in _SCRIPT_CALL.finditer(text)]
    specs += [m.group(1) for m in _SCRIPT_SIDE_EFFECT.finditer(text)]
    found: list[_Import] = []
    for spec in specs:
        if spec.startswith("."):
            resolved = _resolve(base, spec.split("/"))
            if resolved is not None:
                found.append(_Import(_strip_suffix(resolved), exact=True))
        else:
            tail = tuple(p for p in _ALIAS_PREFIX.sub("", spec).split("/") if p)
            found.append(_Import(_strip_suffix(tail), exact=False))
    return found


def _python_imports(text: str, base: tuple[str, ...]) -> list[_Import]:
    found: list[_Import] = []

    def add(module: str, names: list[str]) -> None:
        dots = len(module) - len(module.lstrip("."))
        pieces = tuple(p for p in module[dots:].split(".") if p)
        stems = [pieces, *((*pieces, n) for n in names)]
        if not dots:
            found.extend(_Import(p, exact=False) for p in stems)
            return
        # 점 하나는 주제 파일의 폴더, 점 둘은 그 위 폴더입니다.
        up = _resolve(base, [".."] * (dots - 1))
        if up is not None:
            found.extend(_Import((*up, *p), exact=True) for p in stems)

    for m in _PY_FROM.finditer(text):
        # `from 패키지 import 모듈`이면 패키지.모듈도 import한 모듈로 봅니다. `as` 뒤 별칭은 빼고 원래 이름만 씁니다.
        names: list[str] = []
        for item in m.group(2).strip("()").split(","):
            first = _NAME.findall(item)
            if first:
                names.append(first[0])
        add(m.group(1), names)
    for m in _PY_IMPORT.finditer(text):
        for item in m.group(1).split(","):
            module = item.split(" as ")[0].strip()
            if module:
                add(module, [])
    return found


def _imports(target: SymbolRef, text: str) -> list[_Import]:
    base = PurePosixPath(target.path).parent.parts
    if _family(target.path) is _Family.PYTHON:
        return _python_imports(text, base)
    return _script_imports(text, base)


def _closeness(target: SymbolRef, imports: list[_Import], symbol: SymbolRef) -> int | None:
    """주제와의 거리입니다. 레포의 다른 영역이라 고르지 않으면 None입니다."""
    module = _module_parts(symbol.path)
    if any(i.matches(module) for i in imports):
        return 0
    target_dir = PurePosixPath(target.path).parent
    symbol_dir = PurePosixPath(symbol.path).parent
    if symbol_dir == target_dir:
        return 1
    if target_dir.parts[:1] == symbol_dir.parts[:1]:
        return 2
    return None


# ---- 고르기 ----


class _TextCache:
    """같은 분석 안에서 파일을 한 번만 읽습니다. 메모리에서만 쓰고 저장하지 않습니다."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.texts: dict[str, str | None] = {}

    def get(self, path: str) -> str | None:
        if path not in self.texts:
            try:
                data = (self.root / path).read_bytes()
            except OSError:
                self.texts[path] = None
            else:
                self.texts[path] = data.decode("utf-8", errors="replace")
        return self.texts[path]


def pick_absent_function(
    target: SymbolRef,
    choices: list[SymbolRef],
    cache: _TextCache,
    used: Counter[str] | None = None,
    together: str = "",
) -> SymbolRef | None:
    """주제 파일에 이름이 한 번도 나오지 않는 함수 중 가장 그럴듯한 것입니다. 없으면 None입니다.

    used는 이 분석에서 다른 주제에 이미 고른 횟수입니다. 같은 거리면 덜 고른 함수를 먼저 고릅니다.
    together는 레포에서 주제 이름이 나오는 줄들입니다. 여기 함께 나오는 함수는 고르지 않습니다.
    """
    target_text = cache.get(target.path)
    if target_text is None:
        return None
    used = used if used is not None else Counter()
    family = _family(target.path)
    imports = _imports(target, target_text)
    ranked: list[tuple[tuple[int, int, int, str, int], SymbolRef]] = []
    for symbol in choices:
        if _family(symbol.path) != family or symbol.path == target.path:
            continue
        closeness = _closeness(target, imports, symbol)
        if closeness is None:
            continue
        length = symbol.end_line - symbol.start_line
        key = (closeness, used[symbol.symbol_id], -length, symbol.path, symbol.start_line)
        ranked.append((key, symbol))
    for _, symbol in sorted(ranked, key=lambda pair: pair[0]):
        if _mentions(target_text, symbol.name):
            continue
        text = cache.get(symbol.path)
        if text is None:
            continue
        # 그 함수의 파일에 주제 이름이 나오면 주제를 부르는 쪽일 수 있습니다 (거꾸로 부르기).
        if _mentions(text, target.name):
            continue
        # 같은 줄에서 함께 쓰이면 한쪽을 다른 쪽에 넘겨 부를 수 있습니다 (`startVoiceSession(connectGemini)`).
        if _mentions(together, symbol.name):
            continue
        if family is _Family.SCRIPT and _default_exported(text, symbol.name):
            continue
        return symbol
    return None


class MisstatementPlanner:
    """주제 후보마다 틀린 말 방식을 정합니다. 분석 하나에 하나를 만들고, 후보 순서대로 부릅니다."""

    def __init__(
        self,
        root: Path,
        symbols: Iterable[SymbolRef],
        references: Mapping[str, int] | None = None,
    ) -> None:
        symbols = list(symbols)
        choices = absent_choices(symbols)
        # 함께 쓰이는 줄을 찾을 레포 소스 파일입니다. 테스트도 넣습니다(함께 넘기는 곳이 테스트뿐일 수 있음).
        self.paths = sorted({s.path for s in symbols})
        if references is not None:
            # 참조 수 목록에 없거나(데코레이터, 특수 메서드) 0인 Python 함수는
            # 프레임워크가 부르거나 아무도 이름으로 부르지 않는 함수입니다.
            choices = [
                s
                for s in choices
                if _family(s.path) is not _Family.PYTHON or references.get(s.symbol_id, 0) > 0
            ]
        self.choices = choices
        self.cache = _TextCache(root)
        self.used: Counter[str] = Counter()

    def _lines_with(self, name: str) -> str:
        """레포 소스 파일에서 이 이름이 나오는 줄을 모읍니다."""
        lines: list[str] = []
        for path in self.paths:
            text = self.cache.get(path)
            if text is None or not _mentions(text, name):
                continue
            lines += [line for line in text.splitlines() if _mentions(line, name)]
        return "\n".join(lines)

    def plan(self, target: SymbolRef) -> MisstatementPlan:
        together = self._lines_with(target.name)
        absent = pick_absent_function(target, self.choices, self.cache, self.used, together)
        if absent is None:
            return MisstatementPlan(MisstatementKind.INVERTED_POINT)
        self.used[absent.symbol_id] += 1
        return MisstatementPlan(MisstatementKind.ABSENT_CALL, absent)
