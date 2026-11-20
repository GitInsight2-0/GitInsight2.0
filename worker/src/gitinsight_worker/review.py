"""2단계 질문 재료 검토 (#199): 출제 LLM이 만든 재료를 검토 LLM이 코드와 대조하고, 서버가 근거를 확인합니다.

- 검토 LLM은 항목마다 통과 / 고칠 것 / 확인할 수 없음을 내고, 재료를 직접 고치지 않습니다. 쓰는 쪽은 출제 하나입니다.
- 요점마다 근거가 되는 자료의 문장을 그대로 옮기게 하고, 서버가 그 문장이 실제로 준 자료(코드, 정의 요약, 서버 사실)에 있는지 글자로 대조합니다.
  근거를 대지 못한 요점은 검토 LLM이 통과라고 해도 고칠 것으로 봅니다. 코드에 없는 이유를 덧붙이는 오류를 모델과 상관없이 거르기 위해서입니다.
  여러 줄 근거는 통째로 없어도 줄마다 자료에 있으면 인정합니다(#206). 떨어진 줄을 이어 붙인 인용이 잦았기 때문입니다.
- 확인할 수 없음(주어진 자료 밖의 코드가 필요함)은 고칠 것으로 보지 않고 세기만 합니다.
  이 수가 많으면 검토가 스스로 다른 코드를 찾아보게 하는 다음 단계를 검토합니다.
- 일부러 하는 틀린 말(misstatement)은 일부러 틀린 문장이라 검토에 보여 주지 않습니다.
- 검토 LLM의 사유는 레포 코드의 영향을 받은 LLM 출력이라, 출제 LLM에 돌려줄 때 untrusted_block 안에 넣습니다.
- 근거 문장은 분석 중 메모리에서 대조에만 쓰고 분석 결과에 저장하지 않습니다.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel

from gitinsight_api.brain.factory import build_analysis_llm
from gitinsight_api.llm import CostRecorder, LiteLLMClient, LLMClient, LLMResult, Message
from gitinsight_api.llm.untrusted import UNTRUSTED_NOTICE, untrusted_block
from gitinsight_api.settings import Settings
from gitinsight_contracts.base import LLMOutput

REVIEW_PURPOSE = "analysis_review"
# 검토는 출제보다 꼼꼼히 보도록 출제(low)보다 한 단계 높게 생각하게 합니다.
REVIEW_REASONING_EFFORT = "medium"
# 검토 호출 하나의 제한 시간(초)입니다. 출제(60초)보다 오래 생각하므로 따로 둡니다(#200 리뷰).
# 평가에서 검토 지연이 최대 57초였고, 출제와 같은 60초에서는 복잡한 주제가 먼저 검토를 건너뛰었습니다.
REVIEW_TIMEOUT_S = 90.0
# 근거 문장은 공백을 하나로 줄인 뒤 이 길이 이상이어야 합니다. `return x` 같은 짧은 조각은 무엇이든 맞아서 근거가 되지 않습니다.
MIN_QUOTE_CHARS = 10
# 출제 LLM에 돌려줄 검토 사유의 수와 길이 상한입니다.
MAX_FEEDBACK_ITEMS = 10
MAX_REASON_CHARS = 200

_FIELD = re.compile(
    r"^(?:title|summary|base_question|question_intent|hint)$"
    r"|^(?:expected_points|prepared_followups)\[(\d+)\]$"
)

REVIEW_SYSTEM_PROMPT = f"""당신은 한국어 기술 모의면접의 질문 재료를 검토합니다.
다른 출제자가 지원자 코드(target_code) 하나를 보고 만든 면접 재료(material)를 받습니다.
재료를 직접 고치지 말고, 문제가 있는 항목과 그 사유만 알려 주세요.

검토 기준:
1. 사실: 요점(expected_points), 질문의 전제, 힌트가 target_code, external_definitions, 서버가 알려 준 사실로 확인되는가. 코드에 없는 이유, 의도, 상황(예: "성능 때문에", "여러 스레드가 동시에 부르면")을 덧붙이지 않았는가. 코드와 반대로 말하지 않았는가.
2. 답할 수 있는가: 지원자가 자기 코드만 보고 답할 수 있는 질문인가.
3. 가치: 동작의 이유, 설계 선택, 실패·예외 처리를 묻는가. 이름이나 문법처럼 사소한 것만 묻지 않는가.
4. 답 노출: base_question, probe_question, hint, question_intent가 요점의 답을 그대로 말하지 않는가.

재료의 구조:
- expected_points는 base_question 하나의 답이 아니라, 이 코드에 대해 좋은 답변이 다룰 내용입니다. 지원자가 빠뜨린 요점은 면접관이 그 요점의 probe_question으로 따로 묻습니다.
  그래서 요점이 base_question과 직접 이어지지 않는다는 이유만으로 fix하지 않습니다. 요점 자체가 사실인지, 물을 가치가 있는지를 봅니다.
- prepared_followups도 base_question과 다른 부분을 물을 수 있습니다.

출력 규칙:
- items에는 문제가 있는 항목만 넣습니다. field는 title, summary, base_question, question_intent, hint, expected_points[번호], prepared_followups[번호] 중 하나이고 번호는 1부터입니다.
- verdict는 fix(고쳐야 함) 또는 unverifiable(확인할 수 없음)입니다. 통과한 항목은 넣지 않습니다.
- unverifiable은 다른 함수의 본문처럼 받은 자료에 없는 코드를 봐야 맞는지 알 수 있을 때만 씁니다. 받은 자료로 근거를 찾을 수 없는 이유나 의도는 fix입니다.
- reason은 무엇이 왜 문제인지 한두 문장으로 씁니다. 코드를 길게 옮기지 않습니다.
- evidence에는 요점마다 그 요점을 뒷받침하는 문장을 target_code, external_definitions, 서버가 알려 준 사실 중 한 곳에서 한 줄 이상 글자 그대로 옮깁니다. 공백은 달라도 되지만 글자를 바꾸거나 줄이지 않습니다.
- 근거 문장은 요점과 같은 말을 해야 합니다. 요점마다 근거 문장을 고른 뒤, 그 문장이 요점이 말하는 동작과 같은지 다시 읽어 확인합니다.
  관련된 줄이지만 요점과 반대이거나 요점이 덧붙인 이유·의도까지는 보여 주지 않으면, 그 요점은 evidence에 넣지 않고 items에 fix로 넣습니다.
- 뒷받침하는 문장이 없는 요점은 evidence에 넣지 않고 items에 fix로 넣습니다.
- 주제 전체가 사소하거나 고칠 수 없을 만큼 틀렸으면 drop을 true로 하고 drop_reason에 이유를 씁니다. 아니면 drop은 false, drop_reason은 빈 문자열입니다.

{UNTRUSTED_NOTICE}"""


class ReviewVerdict(StrEnum):
    PASS = "pass"
    FIX = "fix"
    UNVERIFIABLE = "unverifiable"


class ReviewItem(LLMOutput):
    field: str
    verdict: ReviewVerdict
    reason: str


class PointEvidence(LLMOutput):
    # 요점 번호입니다(expected_points 순서, 1부터).
    point: int
    quote: str


class TopicReview(LLMOutput):
    """검토 LLM의 출력입니다. 워커 안에서만 쓰는 모델이라 계약(contracts)에 두지 않습니다."""

    items: list[ReviewItem]
    evidence: list[PointEvidence]
    drop: bool
    drop_reason: str


@dataclass(frozen=True)
class ReviewSource:
    """검토 LLM에 보여 주는 자료입니다. 출제 LLM이 받은 것과 같고, 근거 대조도 이 자료로 합니다."""

    target: str
    code: str
    external: str = ""
    facts: str | None = None

    def corpus(self) -> str:
        """근거 대조 자료입니다. 서버 사실은 숫자가 든 줄(`- `로 시작)만 넣습니다.

        안내 문장("서버가 레포 전체에서 이름으로 찾은 참조 수입니다…")은 어느 주제에나 있어서,
        넣으면 아무 요점의 근거로 옮겨도 대조를 통과합니다(#200 리뷰).
        """
        facts = [line for line in (self.facts or "").splitlines() if line.startswith("- ")]
        return _normalize("\n".join([self.code, self.external, *facts]))


@dataclass(frozen=True)
class ReviewOutcome:
    """검토 결과를 서버 규칙으로 정리한 것입니다."""

    # 서버가 확인한 문제입니다(근거 없음, 근거가 자료에 없음). 출제 LLM에 그대로 보여 줍니다.
    server_problems: list[str]
    # 검토 LLM이 고치라고 한 사유입니다. LLM 출력이라 출제 LLM에 돌려줄 때 untrusted_block 안에 넣습니다.
    reviewer_problems: list[str]
    unverifiable: int
    drop: bool
    # server_problems의 내역입니다. 근거를 내지 않은 요점, 낸 근거가 모두 줄마다 MIN_QUOTE_CHARS보다 짧았던 요점,
    # 낸 근거가 자료에 없던 요점입니다(#200 리뷰, #206).
    # 마지막이 많으면 검토 LLM이 근거를 잘못 옮겨 맞는 요점을 고치게 하는 것일 수 있습니다.
    no_quote: int = 0
    bad_quote: int = 0
    short_quote: int = 0

    @property
    def passed(self) -> bool:
        return not self.drop and not self.server_problems and not self.reviewer_problems


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _quote_lines(quote: str) -> list[str]:
    return [line for line in (_normalize(raw) for raw in quote.splitlines()) if line]


def _quote_is_short(quote: str) -> bool:
    """줄마다 MIN_QUOTE_CHARS보다 짧은 인용입니다. 한 줄이면 전체 길이로, 여러 줄이면 가장 긴 줄로 봅니다(#209 리뷰)."""
    return all(len(line) < MIN_QUOTE_CHARS for line in _quote_lines(quote))


def quote_in_source(quote: str, corpus: str) -> bool:
    """근거 문장이 대조 자료(정규화한 corpus)에 글자 그대로 있는지입니다.

    통째로 있으면 인정합니다. 아니면 줄마다 대조합니다(#206).
    검토 LLM은 떨어진 줄을 한 인용으로 이어 붙이곤 합니다. #206 진단에서 떨어진 근거 14건이 모두 이 경우였고, 줄마다는 코드에 그대로 있었습니다.
    줄마다 글자 그대로여야 하므로, 코드에 없는 이유를 덧붙인 문장은 여전히 걸러집니다.
    `}`처럼 짧은 줄은 어디에나 맞아서, 줄 중 하나는 MIN_QUOTE_CHARS 이상이어야 합니다.

    한계: 줄 순서와 줄 전체 일치는 보지 않습니다. 각 줄이 자료 어딘가에 부분 문자열로 있으면 됩니다.
    그래서 줄 순서를 뒤집거나 `not`, `None` 같은 짧은 조각을 이어 붙여 뜻을 바꾼 인용도 통과합니다(#209 리뷰).
    한 줄 인용에도 같은 위험이 있어 지금은 프롬프트("근거 문장이 요점과 같은 말을 하는지 다시 확인")로 다룹니다.
    로그에서 이런 사례가 보이면, 짧은 줄은 자료의 한 줄 전체와 같아야 한다는 조건을 붙입니다.
    """
    whole = _normalize(quote)
    if len(whole) >= MIN_QUOTE_CHARS and whole in corpus:
        return True
    lines = _quote_lines(quote)
    return (
        len(lines) > 1
        and all(line in corpus for line in lines)
        and any(len(line) >= MIN_QUOTE_CHARS for line in lines)
    )


def material_for_review(material: BaseModel) -> str:
    """검토에 보여 줄 재료입니다. 틀린 말, 강조 범위 id, 관련 심볼 id는 빼고 번호를 붙입니다."""
    data = material.model_dump()
    shown = {
        "title": data["title"],
        "summary": data["summary"],
        "base_question": data["base_question"],
        "question_intent": data["question_intent"],
        "hint": data["hint"],
        "expected_points": [
            {"number": i, "text": p["text"], "probe_question": p["probe_question"]}
            for i, p in enumerate(data["expected_points"], 1)
        ],
        "prepared_followups": [
            {"number": i, "question": f["question"]}
            for i, f in enumerate(data["prepared_followups"], 1)
        ],
    }
    return json.dumps(shown, ensure_ascii=False)


def build_review_messages(source: ReviewSource, material: BaseModel) -> list[Message]:
    parts = [
        "주제 코드의 정보입니다.",
        untrusted_block("target_symbol", source.target),
        "주제 코드입니다.",
        untrusted_block("target_code", source.code),
    ]
    if source.facts:
        parts.append(source.facts)
    if source.external:
        parts += [
            "주제 코드가 쓰는 다른 파일의 레포 정의 요약입니다. 이름으로 찾아 틀릴 수 있고, 본문은 넣지 않았습니다.",
            untrusted_block("external_definitions", source.external),
        ]
    parts += [
        "검토할 면접 재료입니다. 요점과 꼬리질문의 number가 field의 번호입니다.",
        untrusted_block("material", material_for_review(material)),
    ]
    return [
        {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def _field_index(field: str) -> tuple[bool, int | None]:
    """field가 아는 이름인지와, 번호가 있으면 그 번호입니다."""
    match = _FIELD.match(field.strip())
    if match is None:
        return False, None
    return True, int(match.group(1)) if match.group(1) else None


def judge_review(review: TopicReview, source: ReviewSource, point_count: int) -> ReviewOutcome:
    """검토 LLM의 출력을 서버 규칙으로 정리합니다.

    - 모르는 field나 없는 번호의 항목은 버립니다.
    - unverifiable로 표시된 요점은 근거를 요구하지 않고 세기만 합니다.
      그래서 검토 LLM이 unverifiable을 내면 근거 대조를 건너뛰는 길이 됩니다. 평가에서는 0건이었습니다.
      이 수가 늘면 unverifiable 요점도 근거를 요구하거나, 검토가 다른 코드를 찾아보게 하는 쪽을 검토합니다(#200 리뷰).
    - 나머지 요점은 자료에 실제로 있는 근거 문장이 하나 이상 있어야 합니다(quote_in_source).
    """
    corpus = source.corpus()
    reviewer: list[str] = []
    unverifiable_points: set[int] = set()
    unverifiable = 0
    for item in review.items:
        known, index = _field_index(item.field)
        if not known or (index is not None and index < 1):
            continue
        field = item.field.strip()
        if field.startswith("expected_points") and index is not None and index > point_count:
            continue
        if item.verdict is ReviewVerdict.UNVERIFIABLE:
            unverifiable += 1
            if field.startswith("expected_points") and index is not None:
                unverifiable_points.add(index)
        elif item.verdict is ReviewVerdict.FIX:
            reviewer.append(f"{field}: {item.reason.strip()[:MAX_REASON_CHARS]}")

    supported: set[int] = set()
    quotes: dict[int, list[str]] = {}
    for evidence in review.evidence:
        if _normalize(evidence.quote):
            quotes.setdefault(evidence.point, []).append(evidence.quote)
        if quote_in_source(evidence.quote, corpus):
            supported.add(evidence.point)
    unsupported = [
        i for i in range(1, point_count + 1) if i not in supported and i not in unverifiable_points
    ]
    server = [
        f"expected_points[{i}]: 주제 코드나 서버가 알려 준 사실로 확인되지 않음. 코드에서 직접 확인되는 내용만 쓰세요"
        for i in unsupported
    ]
    quoted = [i for i in unsupported if i in quotes]
    short_quote = sum(all(_quote_is_short(q) for q in quotes[i]) for i in quoted)
    return ReviewOutcome(
        server_problems=server,
        reviewer_problems=reviewer[:MAX_FEEDBACK_ITEMS],
        unverifiable=unverifiable,
        drop=review.drop,
        no_quote=len(unsupported) - len(quoted),
        bad_quote=len(quoted) - short_quote,
        short_quote=short_quote,
    )


def review_feedback(outcome: ReviewOutcome) -> str:
    """검토 결과를 출제 LLM에 돌려줄 문장입니다. 검토 LLM의 사유는 untrusted_block 안에만 넣습니다."""
    parts = ["앞 응답을 검토한 결과 고칠 곳이 있습니다."]
    if outcome.server_problems:
        parts.append(
            "서버가 확인한 문제: " + "; ".join(outcome.server_problems[:MAX_FEEDBACK_ITEMS])
        )
    if outcome.reviewer_problems:
        parts += [
            "검토자가 짚은 문제입니다. 코드와 대조해 맞는 지적만 반영하세요.",
            untrusted_block("review_feedback", "\n".join(outcome.reviewer_problems)),
        ]
    parts.append("규칙을 지켜 같은 형식의 JSON을 다시 출력하세요.")
    return "\n\n".join(parts)


class PurposeRouter:
    """호출 목적(purpose)에 따라 다른 LLM 클라이언트로 보냅니다.

    검토만 다른 설정(더 높은 reasoning_effort, 다른 모델)으로 부르면서 분석 작업과 파이프라인은 클라이언트 하나만 넘기게 합니다.
    """

    def __init__(self, default: LLMClient, routes: Mapping[str, LLMClient]) -> None:
        self._default = default
        self._routes = dict(routes)

    async def complete_structured[T: BaseModel](
        self,
        schema: type[T],
        messages: list[Message],
        *,
        purpose: str,
        timeout_s: float,
        model: str | None = None,
    ) -> LLMResult[T]:
        client = self._routes.get(purpose, self._default)
        return await client.complete_structured(
            schema, messages, purpose=purpose, timeout_s=timeout_s, model=model
        )


def build_stage2_llm(settings: Settings, cost_recorder: CostRecorder | None = None) -> LLMClient:
    """2단계용 클라이언트입니다. 출제는 분석 설정(LLM_ANALYSIS_*)을, 검토는 같은 모델에 REVIEW_REASONING_EFFORT를 씁니다."""
    review = LiteLLMClient(
        settings.llm_analysis_model,
        cost_recorder=cost_recorder,
        reasoning_effort=REVIEW_REASONING_EFFORT,
    )
    return PurposeRouter(build_analysis_llm(settings, cost_recorder), {REVIEW_PURPOSE: review})
