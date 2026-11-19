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
남은 것은 아래 순서로 고릅니다. 같은 입력이면 늘 같은 함수가 나옵니다.
1. 주제 파일이 import하는 모듈의 함수: 그 모듈을 가져다 쓰니 이 함수도 부를 것처럼 들립니다
2. 주제와 같은 폴더, 3. 같은 최상위 폴더
import하지도 않고 최상위 폴더도 다른 함수(예: `frontend/`의 화면 함수에 `app/agents/`의 함수)는 고르지 않습니다.
레포의 다른 영역이라 "부르죠?"가 어색하기 때문입니다. 그런 주제는 inverted_point로 갑니다.
같은 순위면 긴 함수(짧은 도우미보다 하는 일이 분명함), 그다음 경로와 줄 순서입니다.
모듈을 import하는지는 import 줄에 그 파일 이름(index면 폴더 이름)이 단어로 나오는지로 봅니다. 순서에만 쓰므로 틀려도 안전합니다.

기여 라벨은 쓰지 않습니다. 파일은 읽기만 하고 저장하지 않습니다.
"""

import logging
import re
from collections import Counter
from collections.abc import Iterable
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

# import 줄입니다. Python(import, from), TypeScript·JavaScript(import, export ... from, require)를 봅니다.
_IMPORT_LINE = re.compile(r"^\s*(?:import|from|export\s.*\sfrom)\s.*$|^.*\brequire\s*\(.*$", re.M)


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


def _module_name(path: str) -> str:
    """import 줄에 나올 모듈 이름입니다. `index`, `__init__` 파일이면 폴더 이름입니다."""
    pure = PurePosixPath(path)
    stem = pure.name.split(".", 1)[0]
    return pure.parent.name if stem in ("index", "__init__") else stem


def _rank(target: SymbolRef, imports: str, symbol: SymbolRef) -> tuple[int, int, str, int] | None:
    """고르는 순서입니다. 레포의 다른 영역이라 고르지 않으면 None입니다."""
    target_dir = PurePosixPath(target.path).parent
    symbol_dir = PurePosixPath(symbol.path).parent
    if _mentions(imports, _module_name(symbol.path)):
        closeness = 0
    elif symbol_dir == target_dir:
        closeness = 1
    elif target_dir.parts[:1] == symbol_dir.parts[:1]:
        closeness = 2
    else:
        return None
    length = symbol.end_line - symbol.start_line
    return closeness, -length, symbol.path, symbol.start_line


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
    target: SymbolRef, choices: list[SymbolRef], cache: _TextCache
) -> SymbolRef | None:
    """주제 파일에 이름이 한 번도 나오지 않는 함수 중 가장 가까운 것입니다. 없으면 None입니다."""
    target_text = cache.get(target.path)
    if target_text is None:
        return None
    family = _family(target.path)
    imports = "\n".join(_IMPORT_LINE.findall(target_text))
    ranked = [
        (rank, s)
        for s in choices
        if _family(s.path) == family
        and s.path != target.path
        and (rank := _rank(target, imports, s)) is not None
    ]
    for _, symbol in sorted(ranked, key=lambda pair: pair[0]):
        if _mentions(target_text, symbol.name):
            continue
        if family is _Family.SCRIPT:
            text = cache.get(symbol.path)
            if text is None or _default_exported(text, symbol.name):
                continue
        return symbol
    return None


class MisstatementPlanner:
    """주제 후보마다 틀린 말 방식을 정합니다. 분석 하나에 하나를 만듭니다."""

    def __init__(self, root: Path, symbols: Iterable[SymbolRef]) -> None:
        self.choices = absent_choices(symbols)
        self.cache = _TextCache(root)

    def plan(self, target: SymbolRef) -> MisstatementPlan:
        absent = pick_absent_function(target, self.choices, self.cache)
        if absent is None:
            return MisstatementPlan(MisstatementKind.INVERTED_POINT)
        return MisstatementPlan(MisstatementKind.ABSENT_CALL, absent)
