"""2단계 분석: 주제 후보마다 LLM으로 면접 재료를 만들고 서버 규칙으로 검증합니다.

- 어떤 심볼을 주제로 삼을지는 규칙(candidates.py)이 정합니다. LLM은 재료 문장만 씁니다.
- 코드 위치(SymbolRef), topic_id, point_id, 기여 라벨, 커밋은 서버가 붙입니다. LLM 출력에서 가져오지 않습니다.
- LLM이 고르는 ID는 관련 심볼뿐이고, 후보마다 정해 둔 허용 목록 안에 있어야 합니다.
- 코드와 README는 untrusted_block 안에만 넣고, 메모리에서만 쓰고 저장하지 않습니다.
- 검증에 실패하면 문제를 알려 주고 한 번 다시 요청합니다. 그래도 실패하면 그 후보를 버리고 다음 후보로 채웁니다.
- 1단계가 센 레포 안 참조 수는 서버가 만든 사실로 untrusted_block 밖에 넣습니다 (#58).
  심볼 이름은 레포에서 온 값이라 그 밖에 쓰지 않고, 블록 안 목록의 순서로만 가리킵니다.
- 면접관이 일부러 하는 틀린 말(#133, #142)도 같은 요청에서 받습니다. 방식과 부른다고 말할 함수는 서버가 정합니다
  (misstatement.py). 틀린 말만 검증에 실패하면 다시 요청하지 않고 틀린 말만 비운 채 주제를 씁니다.
  틀린 말 때문에 주제를 잃거나 호출이 늘지 않게 하기 위해서입니다.
- 요점마다 강조할 범위(#156)도 같은 요청에서 받습니다. 범위 후보와 줄 번호는 서버가 뽑고(focus.py),
  LLM은 후보 id만 고릅니다. 모르는 id나 null이면 그 요점은 범위 없이 두고, 다시 요청하지 않습니다.
- 형식 검증을 통과한 재료는 검토 LLM이 코드와 대조하고, 서버가 요점의 근거 문장을 확인합니다(#199, review.py).
  고칠 곳이 있으면 사유를 붙여 최대 MAX_REVIEW_ROUNDS번 다시 만들게 하고, 그래도 남으면 그 후보를 버립니다.
"""

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import Field, ValidationError

from gitinsight_api.brain.utterance import LINE_NUMBER
from gitinsight_api.llm import LLMClient, LLMError, LLMResult, LLMUnavailableError, Message
from gitinsight_api.llm.untrusted import UNTRUSTED_NOTICE, untrusted_block
from gitinsight_contracts.analysis import (
    AnalysisResult,
    ContributionLabel,
    ExpectedPoint,
    MisstatementKind,
    PreparedFollowup,
    Topic,
)
from gitinsight_contracts.base import LLMOutput, count_sentences
from gitinsight_contracts.common import SymbolRef
from gitinsight_worker.candidates import Candidate, has_enough_logic, select_candidates
from gitinsight_worker.definitions import external_definitions_text, top_level_index
from gitinsight_worker.focus import FocusCandidate, focus_candidates
from gitinsight_worker.languages import PYTHON, language_of
from gitinsight_worker.misstatement import MisstatementPlan, MisstatementPlanner
from gitinsight_worker.review import (
    REVIEW_PURPOSE,
    REVIEW_TIMEOUT_S,
    ReviewOutcome,
    ReviewSource,
    TopicReview,
    build_review_messages,
    judge_review,
    review_feedback,
)
from gitinsight_worker.stage1 import Stage1Result

logger = logging.getLogger(__name__)

TOPIC_PURPOSE = "analysis_topic"
# 본 주제 5개와 여분 1개입니다 (면접 설정의 본 주제 수 5).
TARGET_TOPICS = 6
# 실패한 후보를 대신할 수 있게 목표의 3배까지만 후보로 봅니다. 코드도 이 후보만 읽습니다.
MAX_CANDIDATES = TARGET_TOPICS * 3
MIN_POINTS = 3
MAX_POINTS = 5
MAX_FOLLOWUPS = 3
MAX_RELATED = 3
# 면접관이 말하는 문장의 길이 상한입니다. 음성으로 듣고 한 번에 이해할 수 있어야 합니다.
MAX_SPOKEN_CHARS = 120
# 후보 순서대로 요청하는 목표 난이도입니다. 주제마다 따로 만들기 때문에 서버가 섞어 줍니다.
DIFFICULTY_CYCLE = (1, 2, 3)
# 주제 하나의 LLM 호출 제한 시간(초)입니다. 스키마 재시도를 포함합니다. 검토 호출도 같은 시간을 씁니다.
TOPIC_TIMEOUT_S = 60.0
# 검토에서 고칠 곳이 나왔을 때 다시 만들게 하는 최대 횟수입니다 (#199). 형식 검증의 재요청(1번)과 따로 셉니다.
MAX_REVIEW_ROUNDS = 2
# 제공사 사용량 제한(분당 요청 수)에 덜 걸리도록 동시에 2개까지만 요청합니다.
MAX_CONCURRENCY = 2
# 제공사 장애나 사용량 제한으로 실패하면 이 간격(초)으로 기다렸다 다시 시도합니다.
UNAVAILABLE_BACKOFF_S: tuple[float, ...] = (5.0, 20.0)
# 기다리면 풀릴 수 있는 제공사 오류(LiteLLM 예외 이름)입니다.
RETRYABLE_ERRORS = frozenset(
    {"RateLimitError", "ServiceUnavailableError", "InternalServerError", "APIConnectionError"}
)
# 키나 모델 이름이 잘못된 경우입니다. 기다리거나 다른 후보로 바꿔도 해결되지 않습니다.
CONFIG_ERRORS = frozenset({"AuthenticationError", "PermissionDeniedError", "NotFoundError"})
# 백오프를 다 쓰고도 사용량 제한이었던 후보가 이만큼 연속이면 분석을 멈춥니다.
QUOTA_BREAKER_LIMIT = 2
# 아주 긴 함수는 앞부분만 보냅니다. 비용과 지연을 제한합니다.
MAX_CODE_CHARS = 8_000
MAX_README_CHARS = 3_000

_SLUG_INVALID = re.compile(r"[^a-z0-9]+")
# 코드 주석의 단락 표시([A], [B], [1] 등)입니다 (#85). 지원자는 코드 주석을 보지 못하므로 무엇인지 알 수 없습니다.
# 이름이나 괄호 바로 뒤의 인덱스(results[0], data["a"][1])는 코드 표현이라 잡지 않습니다.
COMMENT_MARKER = re.compile(r"(?<![\w\]\)'\"])\[(?:[A-Z]|\d{1,2})\]")

TOPIC_SYSTEM_PROMPT = f"""당신은 한국어 기술 모의면접의 질문 재료를 만듭니다.
지원자 본인의 레포에 있는 코드 하나(target_code)를 보고, 그 코드에 대한 면접 주제 하나를 만드세요.

규칙:
- 모든 문장은 한국어로 씁니다. base_question, question_intent, hint, probe_question, prepared_followups의 question은 면접관이 소리 내어 그대로 말하는 문장이므로 존댓말 한 문장, {MAX_SPOKEN_CHARS}자 이하로 씁니다.
- base_question은 한 가지만 묻는 60자 안팎의 짧은 질문입니다. 코드의 동작이나 설명을 질문 안에 늘어놓지 않습니다.
- question_intent는 지원자가 질문 뜻을 되물을 때 면접관이 말하는 문장입니다. 무엇을 묻는지 쉬운 말로 풀어 주되, 답이 되는 내용(코드의 구체적인 동작, 조건, 값, 이름)은 쓰지 않습니다.
- 줄 번호나 "몇 번째 줄" 같은 위치 표현을 쓰지 않습니다. 코드는 함수, 클래스, 변수 이름으로 가리킵니다.
- 코드 주석의 단락 표시([A], [B], [1] 같은 것)를 그대로 쓰지 않습니다. 지원자는 주석을 보지 못하므로 그 단락이 하는 일을 말로 풀어 씁니다. 예: "[A]와 [B]로 나뉜 절차" 대신 "클라이언트를 찾는 부분과 임베딩 모델을 찾는 부분".
- summary는 이 코드가 실제로 하는 일을 3문장 이하로 설명합니다.
- expected_points는 좋은 답변이라면 다뤄야 할 요점 {MIN_POINTS}~{MAX_POINTS}개입니다. target_code, external_definitions, 서버가 알려 준 사실에서 직접 확인할 수 있는 내용만 쓰고, 코드에 없는 내용을 지어내지 않습니다.
- external_definitions는 주제 코드가 쓰는 다른 파일 정의의 요약(시그니처, 클래스 속성과 메서드 이름)입니다. 주제 코드가 그 정의에 없는 속성이나 메서드 이름을 찾거나 쓰고 있으면, 그 점을 다루는 요점이나 꼬리질문을 하나 넣습니다. 이름으로 찾은 요약이라 틀릴 수 있으므로 "정의에서 찾지 못했다"처럼 쓰고 단정하지 않습니다. 맞지 않는 점이 없으면 이 요약으로 요점을 만들지 않습니다.
- 지원자는 external_definitions를 보지 못합니다. 요점과 질문에 "정의 요약", "외부 정의" 같은 말을 쓰지 말고 코드 이름으로 직접 말합니다. 예: "VectorService에서 _client 속성을 찾지 못했는데, 이 함수는 실제로 클라이언트를 얻나요?"
- 다른 함수나 메서드의 본문은 보이지 않습니다. 그 안에서 일어나는 일(예외를 잡는지, 무엇을 돌려주는지 등)은 시그니처로 알 수 있는 것 말고는 단정하지 않습니다. 예를 들어 주제 코드에 try가 없다고 해서 예외가 호출자에게 그대로 올라간다고 쓰지 않습니다.
- 서버가 알려 준 레포 안 참조 수가 0이면 레포에서 그 코드를 부르는 곳을 찾지 못했다는 뜻입니다. 주제 코드가 그렇다면 그 사실을 다루는 요점을 하나 넣습니다. 관련 심볼이 그렇다면 그 심볼을 가리키는 꼬리질문으로 물을 수 있습니다.
- 이 요점도 다른 요점처럼 좋은 답변이 말해야 할 내용으로 씁니다. 예: "레포 안에 이 코드를 부르는 곳이 없다는 것을 알고, 어디에 연결하려던 것인지 말한다". "~확인 필요", "~설명"처럼 과제나 메모로 쓰지 않습니다.
- 이 사실을 묻는 질문은 코드가 어딘가에서 불린다고 전제하지 않습니다. 예: "레포 안에서 부르는 곳을 찾지 못했는데, 지금 실제로 쓰이고 있나요?". "쓰이지 않는다", "0곳으로 확인된다"처럼 단정하지 말고 "부르는 곳을 찾지 못했다"처럼 씁니다.
- 각 요점의 probe_question은 지원자가 그 요점을 빠뜨렸을 때 그대로 묻는 질문입니다. 요점의 답을 질문에 넣지 않습니다.
- 각 요점의 focus_id는 그 요점이 가리키는 코드 부분입니다. focus_candidates 목록에서 그 부분을 담은 후보의 id를 고릅니다. 요점이 코드 전체에 대한 것이거나, 맞는 후보가 없거나, focus_candidates가 없으면 null로 둡니다. 목록에 없는 id는 쓰지 않습니다.
- prepared_followups는 1~{MAX_FOLLOWUPS}개의 꼬리질문입니다. 특정 관련 심볼을 보여 주며 물을 때만 symbol_id에 related_symbols의 symbol_id를 쓰고, 아니면 null로 둡니다.
- related_symbol_ids에는 related_symbols 중 이 주제와 관련 깊은 것을 최대 {MAX_RELATED}개 고릅니다. 목록에 없는 ID는 쓰지 않습니다.
- difficulty는 base_question이 묻는 수준입니다: 1(무엇을 어떤 순서로 하는지), 2(왜 이렇게 설계했는지, 다른 방법과 비교), 3(실패, 예외, 부하 상황에서 어떻게 되는지).
- 요청받은 난이도로 base_question을 만듭니다. 그 난이도를 물을 근거가 코드에 없으면 가장 가까운 난이도로 만들고, difficulty에는 실제로 만든 난이도를 씁니다.
- title은 20자 안팎의 짧은 한국어 제목입니다.
- hint는 답을 알려 주지 않고, 코드의 어느 부분을 보면 되는지 한 문장으로 알려 줍니다.

misstatement 규칙 (면접관이 확인을 위해 일부러 하는 틀린 말):
- statement는 면접관이 그대로 읽는 틀린 말, correction은 지원자가 짚지 못했을 때 면접관이 바로 이어서 읽는 정답입니다. 둘 다 존댓말 한 문장, {MAX_SPOKEN_CHARS}자 이하입니다.
- statement는 확인하는 말투로 씁니다. "틀렸다", "일부러", "맞나요 아닌가요" 같은 말로 틀린 말이라는 것을 드러내지 않습니다.
- correction은 그 문장만 들어도 앞의 말을 바로잡는 것으로 들려야 합니다. "방금 말씀드린 것과 달리"로 시작합니다.
- 함수 이름은 백틱이나 따옴표 없이 글자 그대로 씁니다.
- 서버가 absent_function을 주면: statement는 주제 코드가 그 함수를 직접 호출한다고 말하는 확인 질문입니다. 예: "여기서 직접 save_report를 호출해서 결과를 저장하죠?" correction은 주제 코드가 그 함수를 직접 호출하지 않는다고 말합니다. 예: "방금 말씀드린 것과 달리 이 코드는 save_report를 직접 호출하지 않습니다." statement와 correction 모두 absent_function의 name을 그대로 넣습니다. 그 함수의 본문은 보이지 않으므로 그 함수가 하는 일을 지어내지 않습니다. point_index는 null입니다.
- 서버가 absent_function을 주지 않으면: 내가 쓴 expected_points 중 하나를 골라 point_index(1부터)에 번호를 쓰고, statement는 그 요점을 반대로 말합니다. target_code에서 분명히 확인되는 사실(무엇을 돌려주는지, 어떤 조건에서 무엇을 하는지)만 뒤집어서 틀린 것이 확실해야 합니다. 설계 의도나 좋고 나쁨 같은 의견은 뒤집지 않습니다. correction은 그 요점을 바르게 말합니다.

{UNTRUSTED_NOTICE}"""


class PointDraft(LLMOutput):
    text: str
    probe_question: str
    # 이 요점이 가리키는 강조 범위 후보 id입니다(focus_candidates의 b1, b2, ...). 없으면 null입니다 (#156).
    focus_id: str | None


class FollowupDraft(LLMOutput):
    question: str
    symbol_id: str | None


class MisstatementDraft(LLMOutput):
    statement: str
    correction: str
    # inverted_point일 때 반대로 말한 요점 번호입니다(expected_points 순서, 1부터). absent_call이면 null입니다.
    point_index: int | None


class TopicMaterial(LLMOutput):
    """주제 하나의 LLM 출력입니다. 워커 안에서만 쓰는 모델이라 계약(contracts)에 두지 않습니다."""

    title: str
    difficulty: Annotated[int, Field(ge=1, le=3)]
    summary: str
    base_question: str
    question_intent: str
    expected_points: list[PointDraft]
    prepared_followups: list[FollowupDraft]
    hint: str
    related_symbol_ids: list[str]
    misstatement: MisstatementDraft


class TopicMaterialError(Exception):
    """검증 문제 목록입니다. LLM에 돌려줄 때 응답 본문은 넣지 않습니다."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


class Stage2Error(Exception):
    """주제를 하나도 만들지 못했습니다."""


class Stage2AbortError(Stage2Error):
    """다른 후보로 바꿔도 해결되지 않아 분석을 바로 멈춥니다. 메시지에는 예외 종류 이름만 넣습니다."""


class LLMConfigError(Stage2AbortError):
    """LLM 키나 모델 설정이 잘못되었습니다."""


class LLMQuotaError(Stage2AbortError):
    """기다리고 다시 시도해도 사용량 제한이 풀리지 않습니다(하루 한도 소진 등)."""


class _QuotaBreaker:
    """백오프를 다 쓰고도 사용량 제한이었던 후보가 연속으로 나오면 분석을 멈춥니다.

    분당 한도는 기다리면 풀리지만, 하루 한도는 남은 후보를 다 시도해도 호출만 쓰고 몇 분 뒤 실패합니다.
    중간에 한 번이라도 응답을 받으면 다시 0부터 셉니다.
    """

    def __init__(self, limit: int = QUOTA_BREAKER_LIMIT) -> None:
        self.limit = limit
        self.consecutive = 0

    def succeeded(self) -> None:
        self.consecutive = 0

    def exhausted(self, kind: str) -> None:
        self.consecutive += 1
        if self.consecutive >= self.limit:
            raise LLMQuotaError(kind)


@dataclass(frozen=True)
class TopicContext:
    """서버가 정하는 주제 값입니다. LLM 출력에서 가져오지 않습니다."""

    topic_id: str
    contribution_label: ContributionLabel
    commits: list[str]


ContributionOf = Callable[[str], tuple[ContributionLabel, list[str]]]
ProgressFn = Callable[[float], Awaitable[None]]


def _no_contribution(_symbol_id: str) -> tuple[ContributionLabel, list[str]]:
    return ContributionLabel.UNVERIFIED, []


def topic_slug(symbol_name: str, used: set[str]) -> str:
    base = _SLUG_INVALID.sub("-", symbol_name.lower()).strip("-")[:90] or "topic"
    slug, n = base, 2
    while slug in used:
        slug, n = f"{base}-{n}", n + 1
    used.add(slug)
    return slug


# ---- 입력 준비 (동기, 파일 읽기) ----


def read_symbol_code(root: Path, candidate: Candidate) -> str:
    symbol = candidate.symbol
    text = (root / symbol.path).read_bytes().decode("utf-8", errors="replace")
    lines = text.splitlines()[symbol.start_line - 1 : symbol.end_line]
    code = "\n".join(lines)
    if len(code) > MAX_CODE_CHARS:
        # 잘린 표시는 그 언어의 주석으로 붙입니다. TypeScript·JavaScript에서 `#`은 주석이 아닙니다.
        comment = "#" if language_of(symbol.path) == PYTHON else "//"
        code = code[:MAX_CODE_CHARS] + f"\n{comment} ... (길어서 이하 생략)"
    return code


def read_readme(root: Path) -> str:
    for path in sorted(root.iterdir()):
        if path.is_file() and not path.is_symlink() and path.stem.lower() == "readme":
            text = path.read_bytes()[: MAX_README_CHARS * 4].decode("utf-8", errors="replace")
            return text[:MAX_README_CHARS]
    return ""


def visible_focus(root: Path, candidate: Candidate, code: str) -> list[FocusCandidate]:
    """LLM에 보낸 코드 안에서 시작하는 강조 범위 후보입니다. 코드가 길어 잘렸으면 잘린 뒤의 후보는 뺍니다.

    후보를 뽑다가 어떤 오류가 나도 빈 목록을 돌려줍니다. 강조 범위 때문에 주제나 분석을 잃지 않습니다 (#167).
    """
    symbol = candidate.symbol
    try:
        found = focus_candidates((root / symbol.path).read_bytes(), symbol)
    except Exception as error:
        # 예외 종류만 남깁니다. 메시지에 코드가 섞일 수 있습니다.
        logger.warning("focus_failed error=%s", type(error).__name__)
        return []
    # 잘린 표시 줄을 빼고, 보낸 코드의 마지막 줄 번호를 셉니다.
    sent = code.count("\n") + 1 - (1 if len(code) > MAX_CODE_CHARS else 0)
    last_visible = symbol.start_line + sent - 1
    return [f for f in found if f.start_line <= last_visible]


# ---- 프롬프트 ----


def _reference_text(count: int | None) -> str:
    return "알 수 없음" if count is None else f"{count}곳"


def reference_facts(candidate: Candidate, references: Mapping[str, int]) -> str | None:
    """레포 안 참조 수를 서버 사실 문장으로 만듭니다. 심볼 이름은 넣지 않습니다. 아는 값이 없으면 None입니다."""
    target = references.get(candidate.symbol.symbol_id)
    related = [references.get(s.symbol_id) for s in candidate.related_choices]
    if target is None and all(r is None for r in related):
        return None
    lines = [
        "서버가 레포 전체에서 이름으로 찾은 참조 수입니다(정의 자신과 import 문 제외).",
        "같은 이름의 다른 코드나 문자열로 부르는 동적 호출 때문에 정확하지 않을 수 있습니다.",
        "데코레이터가 붙었거나 특수 메서드라 프레임워크가 부를 수 있는 코드는 알 수 없음으로 둡니다.",
        f"- 주제 코드(target_symbol): {_reference_text(target)}",
    ]
    if related:
        joined = ", ".join(f"{i}번 {_reference_text(r)}" for i, r in enumerate(related, 1))
        lines.append(f"- 관련 심볼(related_symbols 목록 순서): {joined}")
    return "\n".join(lines)


def _target_json(candidate: Candidate) -> str:
    symbol = candidate.symbol
    target = {"symbol_id": symbol.symbol_id, "name": symbol.name, "kind": symbol.kind.value}
    return json.dumps(target, ensure_ascii=False)


def build_topic_messages(
    candidate: Candidate,
    code: str,
    readme: str,
    difficulty: int = 1,
    references: Mapping[str, int] | None = None,
    external: str = "",
    plan: MisstatementPlan | None = None,
    focus: list[FocusCandidate] | None = None,
) -> list[Message]:
    related = [
        {"symbol_id": s.symbol_id, "name": s.name, "kind": s.kind.value}
        for s in candidate.related_choices
    ]
    parts = [
        f"요청 난이도: {difficulty}",
        "주제로 삼을 코드의 정보입니다.",
        untrusted_block("target_symbol", _target_json(candidate)),
        "주제로 삼을 코드입니다.",
        untrusted_block("target_code", code),
        "관련 심볼 목록입니다. related_symbol_ids와 symbol_id는 이 안에서만 고르세요.",
        untrusted_block("related_symbols", json.dumps(related, ensure_ascii=False)),
    ]
    if focus:
        # 후보의 첫 줄은 레포 코드라 블록 안에만 넣습니다. 줄 번호는 LLM에 보여 주지 않습니다.
        listed = [{"id": f.focus_id, "first_line": f.head} for f in focus]
        parts += [
            "요점이 가리키는 부분을 고를 후보입니다. focus_id는 이 안에서만 고르세요. 각 후보는 target_code 안의 한 부분이고 first_line은 그 첫 줄입니다.",
            untrusted_block("focus_candidates", json.dumps(listed, ensure_ascii=False)),
        ]
    facts = reference_facts(candidate, references or {})
    if facts is not None:
        parts.append(facts)
    if external:
        parts += [
            "주제 코드가 쓰는 다른 파일의 레포 정의 요약입니다. 이름으로 찾아 틀릴 수 있고, 본문은 넣지 않았습니다.",
            untrusted_block("external_definitions", external),
        ]
    absent = plan.absent_symbol if plan is not None else None
    if absent is not None:
        # 함수 이름과 경로는 레포에서 온 값이라 블록 안에만 넣습니다. 블록 밖에는 서버가 확인한 사실만 씁니다.
        info = {"name": absent.name, "path": absent.path}
        parts += [
            "틀린 말 방식: absent_call. 아래 absent_function은 이 레포의 다른 파일에 있는 함수이고, "
            "서버가 확인한 결과 주제 코드의 파일에는 이 이름이 한 번도 나오지 않습니다.",
            untrusted_block("absent_function", json.dumps(info, ensure_ascii=False)),
        ]
    else:
        parts.append(
            "틀린 말 방식: inverted_point. 서버가 absent_function을 주지 않았습니다. 요점 하나를 반대로 말하세요."
        )
    if readme:
        parts += [
            "레포 README 앞부분입니다. 코드의 목적을 이해하는 데만 참고하세요.",
            untrusted_block("readme", readme),
        ]
    return [
        {"role": "system", "content": TOPIC_SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


# ---- 서버 검증 ----


def _spoken_problems(field: str, text: str) -> list[str]:
    problems: list[str] = []
    if not text.strip():
        problems.append(f"{field}: 비어 있음")
    elif count_sentences(text) != 1:
        problems.append(f"{field}: 한 문장이어야 함")
    if len(text.strip()) > MAX_SPOKEN_CHARS:
        problems.append(f"{field}: {MAX_SPOKEN_CHARS}자 이하여야 함")
    return problems


def validate_material(material: TopicMaterial, candidate: Candidate, ctx: TopicContext) -> Topic:
    """LLM 출력을 Topic으로 바꿉니다. 문제가 있으면 TopicMaterialError를 올립니다."""
    problems: list[str] = []
    allowed = {s.symbol_id: s for s in candidate.related_choices}

    spoken = {
        "base_question": material.base_question,
        "question_intent": material.question_intent,
        "hint": material.hint,
    }
    points = _dedupe([p for p in material.expected_points if p.text.strip()], key=lambda p: p.text)
    for i, point in enumerate(points, 1):
        spoken[f"expected_points[{i}].probe_question"] = point.probe_question
    followups = material.prepared_followups[:MAX_FOLLOWUPS]
    for i, followup in enumerate(followups, 1):
        spoken[f"prepared_followups[{i}].question"] = followup.question
    for field, text in spoken.items():
        problems += _spoken_problems(field, text)

    every_text = {
        **spoken,
        "title": material.title,
        "summary": material.summary,
        **{f"expected_points[{i}].text": p.text for i, p in enumerate(points, 1)},
    }
    problems += [
        f"{f}: 줄 번호를 쓰면 안 됨" for f, t in every_text.items() if LINE_NUMBER.search(t)
    ]
    problems += [
        f"{f}: [A] 같은 코드 주석 표시를 쓰면 안 됨 (가리키는 내용을 말로 풀어 쓰기)"
        for f, t in every_text.items()
        if COMMENT_MARKER.search(t)
    ]

    if not MIN_POINTS <= len(points) <= MAX_POINTS:
        problems.append(
            f"expected_points: {MIN_POINTS}~{MAX_POINTS}개여야 함 (지금 {len(points)}개)"
        )
    if not followups:
        problems.append("prepared_followups: 1개 이상이어야 함")
    for i, followup in enumerate(followups, 1):
        if followup.symbol_id is not None and followup.symbol_id not in allowed:
            problems.append(f"prepared_followups[{i}].symbol_id: 관련 심볼 목록에 없음")
    unknown = [s for s in material.related_symbol_ids if s not in allowed]
    if unknown:
        problems.append(f"related_symbol_ids: 관련 심볼 목록에 없는 ID {len(unknown)}개")
    if problems:
        raise TopicMaterialError(problems)

    related = [allowed[s] for s in _dedupe(material.related_symbol_ids, key=str)][:MAX_RELATED]
    # 꼬리질문이 가리키는 심볼은 하이라이트할 수 있게 관련 심볼에 넣습니다.
    for followup in followups:
        symbol = allowed.get(followup.symbol_id or "")
        if symbol is not None and symbol not in related:
            related.append(symbol)
    try:
        return Topic(
            topic_id=ctx.topic_id,
            title=material.title.strip(),
            symbol=candidate.symbol,
            related_symbols=related,
            contribution_label=ctx.contribution_label,
            difficulty=material.difficulty,
            summary=material.summary.strip(),
            base_question=material.base_question.strip(),
            question_intent=material.question_intent.strip(),
            expected_points=[
                ExpectedPoint(
                    point_id=f"p{i}",
                    text=p.text.strip(),
                    probe_question=p.probe_question.strip(),
                )
                for i, p in enumerate(points, 1)
            ],
            prepared_followups=[
                PreparedFollowup(question=f.question.strip(), symbol_id=f.symbol_id)
                for f in followups
            ],
            hint=material.hint.strip(),
            commits=ctx.commits,
        )
    except ValidationError as error:
        # 길이 제한 같은 계약 위반입니다. 입력값(응답 본문)은 넣지 않습니다.
        raise TopicMaterialError(
            [
                f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['type']}"
                for e in error.errors(include_input=False, include_url=False)[:10]
            ]
        ) from None


# 틀린 말이라는 것을 드러내는 말입니다. 면접관이 이렇게 말하면 답을 알려 주게 됩니다.
_GIVEAWAY = re.compile(r"일부러|틀린 말|틀렸|틀리게")


def _inverted_point_id(material: TopicMaterial, topic: Topic) -> str | None:
    """LLM이 고른 요점 번호를 서버의 point_id로 바꿉니다. 겹쳐서 뺀 요점이 있어도 글로 찾습니다."""
    index = material.misstatement.point_index
    if index is None or not 1 <= index <= len(material.expected_points):
        return None
    text = material.expected_points[index - 1].text.strip()
    return next((p.point_id for p in topic.expected_points if p.text == text), None)


def attach_misstatement(material: TopicMaterial, topic: Topic, plan: MisstatementPlan) -> Topic:
    """검증한 틀린 말을 주제에 붙입니다. 문제가 있으면 TopicMaterialError를 올립니다."""
    draft = material.misstatement
    # 면접관이 소리 내어 읽는 문장이라 이름을 감싼 백틱은 뺍니다.
    statement = draft.statement.replace("`", "").strip()
    correction = draft.correction.replace("`", "").strip()
    problems = _spoken_problems("misstatement.statement", statement)
    problems += _spoken_problems("misstatement.correction", correction)
    for field, text in (("statement", statement), ("correction", correction)):
        if LINE_NUMBER.search(text):
            problems.append(f"misstatement.{field}: 줄 번호를 쓰면 안 됨")
        if COMMENT_MARKER.search(text):
            problems.append(f"misstatement.{field}: [A] 같은 코드 주석 표시를 쓰면 안 됨")
    if _GIVEAWAY.search(statement):
        problems.append("misstatement.statement: 틀린 말이라는 것을 드러내면 안 됨")
    if statement == correction:
        problems.append("misstatement: statement와 correction이 같음")
    point_id: str | None = None
    absent = plan.absent_symbol
    if plan.kind is MisstatementKind.ABSENT_CALL and absent is not None:
        if draft.point_index is not None:
            problems.append("misstatement.point_index: absent_call이면 null이어야 함")
        for field, text in (("statement", statement), ("correction", correction)):
            if absent.name not in text:
                problems.append(f"misstatement.{field}: absent_function의 name이 없음")
    else:
        point_id = _inverted_point_id(material, topic)
        if point_id is None:
            problems.append("misstatement.point_index: expected_points 안의 번호여야 함")
    if problems:
        raise TopicMaterialError(problems)
    data = topic.model_dump(mode="json")
    data["misstatement"] = {
        "kind": plan.kind.value,
        "statement": statement,
        "correction": correction,
        "point_id": point_id,
        "absent_symbol": absent.model_dump(mode="json") if absent is not None else None,
    }
    try:
        # 계약 검증(150자, 이름이 statement에 있음, 주제 자신이 아님)도 거칩니다.
        return Topic.model_validate(data)
    except ValidationError as error:
        raise TopicMaterialError(
            [
                f"misstatement.{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['type']}"
                for e in error.errors(include_input=False, include_url=False)[:10]
            ]
        ) from None


def point_focus(
    material: TopicMaterial, topic: Topic, focus: Iterable[FocusCandidate]
) -> dict[str, FocusCandidate]:
    """요점(point_id)마다 LLM이 고른 강조 범위 후보입니다. 모르는 id와 null은 빼서, 그 요점은 범위 없이 둡니다.

    요점은 겹친 것을 빼고 point_id를 붙였으므로 글로 짝을 찾습니다.
    """
    by_id = {f.focus_id: f for f in focus}
    by_text = {p.text: p.point_id for p in topic.expected_points}
    chosen: dict[str, FocusCandidate] = {}
    unknown = 0
    for draft in material.expected_points:
        point_id = by_text.get(draft.text.strip())
        if point_id is None or point_id in chosen or draft.focus_id is None:
            continue
        found = by_id.get(draft.focus_id.strip())
        if found is None:
            unknown += 1
            continue
        chosen[point_id] = found
    if unknown:
        logger.info("focus_unknown_ids count=%d", unknown)
    return chosen


def attach_focus(material: TopicMaterial, topic: Topic, focus: Iterable[FocusCandidate]) -> Topic:
    """요점에 강조 범위(ExpectedPoint.focus)를 붙입니다. 고른 것이 없으면 주제를 그대로 돌려줍니다.

    계약 검증(주제 심볼 범위 안)을 다시 거칩니다. 후보는 늘 그 범위 안이라 실패하지 않지만,
    실패하면 범위 없이 주제를 씁니다. 강조 범위 때문에 주제를 잃지 않습니다.
    """
    chosen = point_focus(material, topic, focus)
    if not chosen:
        return topic
    data = topic.model_dump(mode="json")
    for point in data["expected_points"]:
        found = chosen.get(point["point_id"])
        if found is not None:
            point["focus"] = {"start_line": found.start_line, "end_line": found.end_line}
    try:
        return Topic.model_validate(data)
    except ValidationError:
        logger.warning("focus_invalid topic=%s", topic.topic_id)
        return topic


def _dedupe[T](items: list[T], *, key: Callable[[T], str]) -> list[T]:
    seen: set[str] = set()
    out: list[T] = []
    for item in items:
        k = key(item).strip()
        if k not in seen:
            seen.add(k)
            out.append(item)
    return out


def _feedback(problems: list[str]) -> str:
    return (
        "앞 응답이 규칙을 어겼습니다. 문제: "
        + "; ".join(problems[:10])
        + ". 규칙을 지켜 같은 형식의 JSON을 다시 출력하세요."
    )


# ---- 생성 ----


async def _complete_with_backoff[T: LLMOutput](
    llm: LLMClient,
    messages: list[Message],
    *,
    timeout_s: float,
    model: str | None,
    breaker: _QuotaBreaker,
    schema: type[T],
    purpose: str,
) -> LLMResult[T] | None:
    """제공사 장애·사용량 제한은 기다렸다 다시 시도합니다. 시간 초과와 거부는 다시 시도하지 않습니다.

    키나 모델 설정 오류는 다른 후보로도 해결되지 않으므로 LLMConfigError로 분석을 멈춥니다.
    다시 시도해도 사용량 제한이 계속되면 차단기가 LLMQuotaError로 분석을 멈춥니다.
    """
    for delay_s in (*UNAVAILABLE_BACKOFF_S, None):
        try:
            result = await llm.complete_structured(
                schema, messages, purpose=purpose, timeout_s=timeout_s, model=model
            )
        except LLMUnavailableError as error:
            # LLMClient는 제공사 예외의 종류 이름만 메시지로 넘깁니다.
            kind = str(error)
            logger.info("llm_failed purpose=%s error=%s retry=%s", purpose, kind, delay_s)
            if kind in CONFIG_ERRORS:
                raise LLMConfigError(kind) from None
            if kind not in RETRYABLE_ERRORS:
                return None
            if delay_s is None:
                if kind == "RateLimitError":
                    breaker.exhausted(kind)
                return None
            await asyncio.sleep(delay_s)
            continue
        except LLMError as error:
            logger.info("llm_failed purpose=%s error=%s", purpose, type(error).__name__)
            return None
        breaker.succeeded()
        return result
    return None


async def generate_topic(
    llm: LLMClient,
    candidate: Candidate,
    messages: list[Message],
    ctx: TopicContext,
    *,
    timeout_s: float = TOPIC_TIMEOUT_S,
    model: str | None = None,
    breaker: _QuotaBreaker | None = None,
    plan: MisstatementPlan | None = None,
    focus: tuple[FocusCandidate, ...] = (),
    review: ReviewSource | None = None,
) -> Topic | None:
    """주제 하나를 만듭니다. 검증 실패는 한 번 다시 요청하고, 그래도 실패하거나 LLM 오류면 None입니다.

    review가 있으면 형식 검증을 통과한 재료를 검토하고(#199), 고칠 곳이 있으면 사유를 붙여 최대
    MAX_REVIEW_ROUNDS번 다시 만들게 합니다. 그래도 고칠 곳이 남거나 검토가 주제를 빼라고 하면 None입니다.
    검토 LLM이 실패하면 검토 없이 주제를 씁니다. 검토 때문에 분석 전체를 잃지 않게 하기 위해서입니다.
    plan이 있으면 틀린 말도 붙입니다. 틀린 말만 검증에 실패하면 다시 요청하지 않고 틀린 말 없이 주제를 돌려줍니다.
    focus가 있으면 요점마다 고른 강조 범위를 붙입니다. 모르는 id는 범위 없이 둡니다.
    """
    attempt = await _generate_topic(
        llm,
        candidate,
        messages,
        ctx,
        timeout_s=timeout_s,
        model=model,
        breaker=breaker,
        plan=plan,
        focus=focus,
        review=review,
    )
    return attempt.topic


StepFn = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class _TopicAttempt:
    topic: Topic | None
    # 검토 때문에 버린 마지막 재료로 만든 주제입니다. 분석의 주제가 하나도 없을 때만 씁니다(#200 리뷰).
    rejected: Topic | None = None
    # 검토가 주제 전체를 빼라고 한(사소하거나 고칠 수 없을 만큼 틀린) 주제인지입니다. 대체로 쓸 때 뒤로 미룹니다.
    dropped: bool = False


def _finish(
    material: TopicMaterial,
    topic: Topic,
    focus: tuple[FocusCandidate, ...],
    plan: MisstatementPlan | None,
) -> Topic:
    """검증을 통과한 주제에 강조 범위와 틀린 말을 붙입니다."""
    topic = attach_focus(material, topic, focus)
    if plan is None:
        return topic
    try:
        return attach_misstatement(material, topic, plan)
    except TopicMaterialError as error:
        logger.info(
            "misstatement_invalid kind=%s problems=%d", plan.kind.value, len(error.problems)
        )
        return topic


async def _generate_topic(
    llm: LLMClient,
    candidate: Candidate,
    messages: list[Message],
    ctx: TopicContext,
    *,
    timeout_s: float,
    model: str | None,
    breaker: _QuotaBreaker | None,
    plan: MisstatementPlan | None,
    focus: tuple[FocusCandidate, ...],
    review: ReviewSource | None,
    on_step: StepFn | None = None,
) -> _TopicAttempt:
    """generate_topic의 본체입니다. on_step은 LLM 호출이 하나 끝날 때마다 부릅니다(진행 상태 갱신)."""
    breaker = breaker or _QuotaBreaker()
    format_retries = 1
    review_rounds = MAX_REVIEW_ROUNDS
    attempt = 0
    while True:
        attempt += 1
        result = await _complete_with_backoff(
            llm,
            messages,
            timeout_s=timeout_s,
            model=model,
            breaker=breaker,
            schema=TopicMaterial,
            purpose=TOPIC_PURPOSE,
        )
        if on_step is not None:
            await on_step()
        if result is None:
            return _TopicAttempt(None)
        answer: Message = {"role": "assistant", "content": result.value.model_dump_json()}
        try:
            topic = validate_material(result.value, candidate, ctx)
        except TopicMaterialError as error:
            logger.info("topic_invalid attempt=%d problems=%d", attempt, len(error.problems))
            if format_retries == 0:
                return _TopicAttempt(None)
            format_retries -= 1
            messages = [*messages, answer, {"role": "user", "content": _feedback(error.problems)}]
            continue
        if review is not None:
            outcome = await _review_material(
                llm, review, result.value, model=model, breaker=breaker
            )
            if on_step is not None:
                await on_step()
            if outcome is not None and not outcome.passed:
                if outcome.drop or review_rounds == 0:
                    logger.info("topic_rejected attempt=%d drop=%s", attempt, outcome.drop)
                    rejected = _finish(result.value, topic, focus, plan)
                    return _TopicAttempt(None, rejected, dropped=outcome.drop)
                review_rounds -= 1
                messages = [
                    *messages,
                    answer,
                    {"role": "user", "content": review_feedback(outcome)},
                ]
                continue
        return _TopicAttempt(_finish(result.value, topic, focus, plan))


async def _review_material(
    llm: LLMClient,
    source: ReviewSource,
    material: TopicMaterial,
    *,
    model: str | None,
    breaker: _QuotaBreaker,
) -> ReviewOutcome | None:
    """검토 LLM을 부르고 서버 규칙으로 정리합니다. 검토 LLM이 실패하면 None입니다.

    로그에는 개수만 남깁니다. 검토 사유에는 코드 내용이 섞일 수 있습니다.
    """
    result = await _complete_with_backoff(
        llm,
        build_review_messages(source, material),
        timeout_s=REVIEW_TIMEOUT_S,
        model=model,
        breaker=breaker,
        schema=TopicReview,
        purpose=REVIEW_PURPOSE,
    )
    if result is None:
        logger.info("topic_review_failed")
        return None
    outcome = judge_review(result.value, source, len(material.expected_points))
    logger.info(
        "topic_review passed=%s fix=%d no_quote=%d bad_quote=%d unverifiable=%d drop=%s",
        outcome.passed,
        len(outcome.reviewer_problems),
        outcome.no_quote,
        outcome.bad_quote,
        outcome.unverifiable,
        outcome.drop,
    )
    return outcome


@dataclass(frozen=True)
class _Prepared:
    candidate: Candidate
    messages: list[Message]
    plan: MisstatementPlan | None = None
    focus: tuple[FocusCandidate, ...] = ()
    review: ReviewSource | None = None


def _prepare(
    root: Path,
    candidates: list[Candidate],
    references: Mapping[str, int] | None = None,
    symbols: list[SymbolRef] | None = None,
) -> list[_Prepared]:
    readme = read_readme(root)
    # 다른 파일의 정의를 이름으로 찾는 색인입니다 (#66). 1단계 심볼 목록이 없으면 붙이지 않습니다.
    index = top_level_index(symbols) if symbols else {}
    # 틀린 말 방식과 부른다고 말할 함수를 정합니다 (#142).
    planner = MisstatementPlanner(root, symbols or [], references)
    prepared: list[_Prepared] = []
    hollow = 0
    for candidate in candidates:
        try:
            code = read_symbol_code(root, candidate)
        except OSError:
            continue
        if not has_enough_logic(code, candidate.symbol.path):
            # 실제 동작이 거의 없는 껍데기 코드는 LLM이 없는 설계 의도를 지어내기 쉬워 건너뜁니다.
            hollow += 1
            continue
        difficulty = DIFFICULTY_CYCLE[len(prepared) % len(DIFFICULTY_CYCLE)]
        external = external_definitions_text(root, candidate.symbol, index) if index else ""
        plan = planner.plan(candidate.symbol)
        focus = visible_focus(root, candidate, code)
        messages = build_topic_messages(
            candidate, code, readme, difficulty, references, external, plan, focus
        )
        # 검토는 출제가 받은 것과 같은 자료로 합니다. README는 목적 참고용이라 근거로 쓰지 않습니다.
        review = ReviewSource(
            _target_json(candidate), code, external, reference_facts(candidate, references or {})
        )
        prepared.append(_Prepared(candidate, messages, plan, tuple(focus), review))
    if hollow:
        logger.info("stage2_hollow_candidates skipped=%d", hollow)
    return prepared


async def generate_topics(
    root: Path,
    stage1: Stage1Result,
    llm: LLMClient,
    *,
    contribution_of: ContributionOf | None = None,
    target: int = TARGET_TOPICS,
    timeout_s: float = TOPIC_TIMEOUT_S,
    model: str | None = None,
    progress: ProgressFn | None = None,
    review: bool = True,
) -> list[Topic]:
    """후보 순서대로 주제를 만듭니다. 실패한 후보는 다음 후보로 채워서 최대 `target`개를 돌려줍니다.

    review가 False면 검토 LLM을 부르지 않습니다(#199 이전과 같음). 평가에서 검토 전후를 비교할 때 씁니다.
    후보를 다 써도 주제가 하나도 없으면, 검토 때문에 버린 주제를 대신 씁니다(#200 리뷰).
    후보가 적은 레포에서 검토가 모두 버려 분석 자체가 실패하는 것보다 낫다고 봅니다.
    고칠 곳이 남아 버린 주제를 먼저, 검토가 빼라고 한 주제를 나중에 씁니다. 각각 안에서는 후보 순서입니다.
    진행률은 LLM 호출이 하나 끝날 때마다 보고합니다. 값이 그대로여도 보고해서, 오래 걸리는 분석이
    오래된 기록(STALE_AFTER)으로 닫히지 않게 합니다(#200 리뷰).
    보고는 한 번에 하나씩, 이전 값보다 작지 않게 합니다. 동시에 끝난 호출의 보고가 뒤바뀌어 화면 진행률이 뒤로 가지 않게 하기 위해서입니다.
    """
    # 기여 근거를 넘기지 않으면 모두 "확인 불가"로 둡니다.
    contribution = contribution_of or _no_contribution
    candidates = select_candidates(stage1.symbols, lambda s: contribution(s.symbol_id)[0])
    prepared = await asyncio.to_thread(
        _prepare, root, candidates[:MAX_CANDIDATES], stage1.references, stage1.symbols
    )
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    used_slugs: set[str] = set()
    breaker = _QuotaBreaker()
    topics: list[Topic] = []
    rejected: list[_TopicAttempt] = []
    next_index = 0
    made = 0
    reported = 0.0
    progress_lock = asyncio.Lock()

    async def beat() -> None:
        nonlocal reported
        if progress is None:
            return
        async with progress_lock:
            reported = max(reported, min(made / target, 1.0))
            await progress(reported)

    async def one(item: _Prepared) -> _TopicAttempt:
        nonlocal made
        label, commits = contribution(item.candidate.symbol.symbol_id)
        ctx = TopicContext(topic_slug(item.candidate.symbol.name, used_slugs), label, commits)
        async with semaphore:
            attempt = await _generate_topic(
                llm,
                item.candidate,
                item.messages,
                ctx,
                timeout_s=timeout_s,
                model=model,
                breaker=breaker,
                plan=item.plan,
                focus=item.focus,
                review=item.review if review else None,
                on_step=beat,
            )
        if attempt.topic is not None:
            made += 1
            await beat()
        return attempt

    # 부족한 만큼만 다음 후보를 한 번에 요청합니다. 결과 순서는 후보 순서를 따릅니다.
    while len(topics) < target and next_index < len(prepared):
        wave = prepared[next_index : next_index + target - len(topics)]
        next_index += len(wave)
        # 즉시 중단 오류(설정 오류, 한도 소진)가 나면 TaskGroup이 같은 묶음의 다른 요청을 취소합니다.
        try:
            async with asyncio.TaskGroup() as group:
                tasks = [group.create_task(one(item)) for item in wave]
        except* Stage2AbortError as errors:
            raise errors.exceptions[0] from None
        results = [task.result() for task in tasks]
        topics += [r.topic for r in results if r.topic is not None]
        rejected += [r for r in results if r.rejected is not None]
    if not topics and rejected:
        # sorted는 안정 정렬이라 같은 종류 안에서는 후보 순서가 유지됩니다.
        ordered = sorted(rejected, key=lambda r: r.dropped)
        topics = [r.rejected for r in ordered if r.rejected is not None][:target]
        logger.info("stage2_review_fallback used=%d", len(topics))
    logger.info(
        "stage2_topics made=%d tried=%d misstatements=%d",
        len(topics),
        next_index,
        sum(t.misstatement is not None for t in topics),
    )
    return topics


async def run_stage2(
    root: Path,
    stage1: Stage1Result,
    llm: LLMClient,
    *,
    analysis_id: str,
    repo_full_name: str,
    contribution_of: ContributionOf | None = None,
    timeout_s: float = TOPIC_TIMEOUT_S,
    model: str | None = None,
    progress: ProgressFn | None = None,
    review: bool = True,
) -> AnalysisResult:
    """1단계 결과와 내려받은 레포(root)로 AnalysisResult를 만듭니다. 주제가 하나도 없으면 Stage2Error입니다."""
    topics = await generate_topics(
        root,
        stage1,
        llm,
        contribution_of=contribution_of,
        timeout_s=timeout_s,
        model=model,
        progress=progress,
        review=review,
    )
    if not topics:
        raise Stage2Error("면접 주제를 하나도 만들지 못했습니다")
    return AnalysisResult(
        analysis_id=analysis_id,
        repo_full_name=repo_full_name,
        commit_sha=stage1.commit_sha,
        languages=stage1.languages,
        symbol_count=stage1.symbol_count,
        topics=topics,
    )
