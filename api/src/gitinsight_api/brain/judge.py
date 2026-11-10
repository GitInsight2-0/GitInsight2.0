"""답변 판단: 판단 프롬프트, Judgment 검증기, 제한 시간 안의 판단 호출.

LLM은 답변이 기대 포인트를 다뤘는지와 꼬리질문 후보만 냅니다. 행동은 rules.decide가 정합니다.
프롬프트에는 원본 코드를 넣지 않고 분석 결과의 요약과 기대 포인트만 넣습니다.
"""

import json
import logging
import re
import time
from dataclasses import dataclass

from gitinsight_api.brain.quotes import EXACT, QuoteMatcher
from gitinsight_api.llm import (
    LLMClient,
    LLMError,
    LLMRefusalError,
    LLMSchemaError,
    LLMTimeoutError,
    Message,
)
from gitinsight_api.llm.untrusted import UNTRUSTED_NOTICE, untrusted_block
from gitinsight_contracts.analysis import Topic
from gitinsight_contracts.base import count_sentences
from gitinsight_contracts.llm import CoverageStatus, Judgment
from gitinsight_contracts.report import FallbackReason

logger = logging.getLogger(__name__)

JUDGE_PURPOSE = "judge"
MAX_FOLLOWUP_CHARS = 150
# 남은 시간이 이보다 적으면 재시도하지 않습니다.
MIN_RETRY_SEC = 0.3

# 꼬리질문에 줄 번호가 들어가면 안 됩니다. 코드 위치는 분석 결과에서만 가져옵니다.
_LINE_NUMBER = re.compile(r"\d+\s*(번째\s*)?(줄|행|라인)|\bline\s*\d+|\bL\d+\b", re.IGNORECASE)

JUDGE_SYSTEM_PROMPT = f"""당신은 한국어 기술 면접에서 지원자 답변을 판단하는 평가 보조자입니다.
다음 행동이나 점수는 서버가 정합니다. 당신은 아래 세 가지만 판단합니다.

1. intent: 답변의 성격
- ANSWER: 질문에 답하려고 시도함 (틀려도 ANSWER)
- DONT_KNOW: 모른다, 기억나지 않는다고 함
- CLARIFY: 질문의 뜻이나 범위를 되물음
- REPEAT: 질문을 다시 말해 달라고 함
- OFF_TOPIC: 질문과 관계없는 말

2. coverage: 기대 포인트마다 status를 정합니다.
- covered: 답변이 그 요점을 맞게 말함
- wrong: 그 요점에 대해 틀리게 말함
- missing: 언급하지 않음
- covered와 wrong은 evidence_quote에 지원자 답변의 문장 일부를 글자 그대로 옮깁니다. 바꿔 쓰거나 요약하지 마세요.
- missing은 evidence_quote를 null로 둡니다.
- 기대 포인트 목록에 있는 point_id만 쓰세요.

3. followup: 지원자가 방금 한 말을 한 단계 더 파고드는 꼬리질문 한 문장 (없으면 null)
- 질문 수준은 표면 이해 → 설계 의도와 트레이드오프 → 장애 대응 순서로 깊어집니다.
- 좋은 예: "예외 처리가 없는데 DB 연결이 끊기면 어떻게 되나요?", "6개월 뒤 다시 본다면 무엇을 고치시겠어요?"
- 금지: 정의 암기 질문("REST API란?"), 검색하면 나오는 비교 질문, 코드에 없는 내용을 상상한 질문
- 금지: 파일의 줄 번호를 말하는 것. 코드 위치가 필요하면 symbol_id에 허용 심볼 목록의 ID를 쓰고, 아니면 null로 둡니다.
- 금지: "직접 작성하셨네요"처럼 지원자가 코드를 썼다고 단정하는 표현. git 기록은 마지막 수정자만 보여 줍니다.
- 존댓말 한 문장, {MAX_FOLLOWUP_CHARS}자 이내로 씁니다.

{UNTRUSTED_NOTICE}"""


class JudgmentInvalidError(Exception):
    """스키마는 맞지만 서버 검증을 통과하지 못한 판단입니다. 메시지에 답변 본문을 넣지 않습니다."""


@dataclass(frozen=True)
class JudgeOutcome:
    judgment: Judgment | None
    fallback_reason: FallbackReason | None
    elapsed_ms: float
    attempts: int


def build_judge_messages(
    topic: Topic, question: str, transcript: str, persona_keywords: list[str]
) -> list[Message]:
    material = {
        "title": topic.title,
        "summary": topic.summary,
        "expected_points": [
            {"point_id": p.point_id, "text": p.text} for p in topic.expected_points
        ],
        "allowed_symbols": [
            {"symbol_id": s.symbol_id, "name": s.name, "kind": s.kind.value}
            for s in (topic.symbol, *topic.related_symbols)
        ],
        "persona_keywords": persona_keywords,
    }
    user = "\n\n".join(
        [
            "면접 주제 자료입니다.",
            untrusted_block("topic", json.dumps(material, ensure_ascii=False)),
            "면접관이 한 질문입니다.",
            untrusted_block("question", question),
            "지원자 답변(음성 인식 자막)입니다.",
            untrusted_block("transcript", transcript),
        ]
    )
    return [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def quote_in_transcript(quote: str, transcript: str, matcher: QuoteMatcher = EXACT) -> bool:
    return matcher.matches(quote, transcript)


def validate_judgment(
    judgment: Judgment, topic: Topic, transcript: str, matcher: QuoteMatcher = EXACT
) -> Judgment:
    point_ids = {p.point_id for p in topic.expected_points}
    seen: set[str] = set()
    for coverage in judgment.coverage:
        if coverage.point_id not in point_ids:
            raise JudgmentInvalidError("기대 포인트 목록에 없는 point_id를 썼습니다")
        if coverage.point_id in seen:
            raise JudgmentInvalidError("같은 point_id를 두 번 판단했습니다")
        seen.add(coverage.point_id)
        if coverage.status is CoverageStatus.COVERED and not coverage.evidence_quote:
            raise JudgmentInvalidError("covered 판단에 evidence_quote가 없습니다")
        if coverage.evidence_quote and not matcher.matches(coverage.evidence_quote, transcript):
            raise JudgmentInvalidError("evidence_quote가 지원자 답변에 없습니다")
    followup = judgment.followup
    if followup is not None:
        question = followup.question.strip()
        if not question or len(question) > MAX_FOLLOWUP_CHARS or count_sentences(question) != 1:
            raise JudgmentInvalidError(
                f"꼬리질문은 {MAX_FOLLOWUP_CHARS}자 이내 한 문장이어야 합니다"
            )
        if _LINE_NUMBER.search(question):
            raise JudgmentInvalidError("꼬리질문에 줄 번호를 쓰면 안 됩니다")
        if followup.symbol_id is not None and followup.symbol_id not in topic.allowed_symbol_ids():
            raise JudgmentInvalidError("허용 심볼 목록에 없는 symbol_id를 썼습니다")
        if question != followup.question:
            judgment = judgment.model_copy(
                update={"followup": followup.model_copy(update={"question": question})}
            )
    return judgment


def _retry_feedback(reason: str) -> str:
    return f"앞 판단이 규칙에 맞지 않았습니다: {reason}. 규칙에 맞게 다시 판단하세요."


async def judge_answer(
    llm: LLMClient,
    topic: Topic,
    question: str,
    transcript: str,
    persona_keywords: list[str],
    *,
    timeout_s: float,
    model: str | None = None,
    quote_matcher: QuoteMatcher = EXACT,
) -> JudgeOutcome:
    """제한 시간 안에서 판단합니다. 검증 실패는 남은 시간이 있으면 1회 재시도합니다."""
    started = time.perf_counter()
    messages = build_judge_messages(topic, question, transcript, persona_keywords)
    reason: FallbackReason | None = None
    attempts = 0
    while attempts < 2:
        remaining = timeout_s - (time.perf_counter() - started)
        if attempts > 0 and remaining < MIN_RETRY_SEC:
            break
        attempts += 1
        try:
            result = await llm.complete_structured(
                Judgment,
                messages,
                purpose=JUDGE_PURPOSE,
                timeout_s=max(remaining, 0.01),
                model=model,
            )
        except LLMTimeoutError:
            reason = FallbackReason.TIMEOUT
            break
        except LLMRefusalError:
            reason = FallbackReason.REFUSAL
            break
        except LLMSchemaError:
            reason = FallbackReason.INVALID
            break
        except LLMError:
            reason = FallbackReason.ERROR
            break
        try:
            judgment = validate_judgment(result.value, topic, transcript, quote_matcher)
        except JudgmentInvalidError as error:
            reason = FallbackReason.INVALID
            logger.info("judgment_invalid attempt=%d reason=%s", attempts, error)
            messages = [
                *messages,
                {"role": "assistant", "content": result.value.model_dump_json()},
                {"role": "user", "content": _retry_feedback(str(error))},
            ]
            continue
        return JudgeOutcome(judgment, None, (time.perf_counter() - started) * 1000, attempts)
    return JudgeOutcome(None, reason, (time.perf_counter() - started) * 1000, attempts)
