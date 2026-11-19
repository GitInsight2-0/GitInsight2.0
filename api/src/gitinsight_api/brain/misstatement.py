"""면접관이 일부러 한 틀린 말에 대한 답변 판단 (#133).

틀린 말과 정답은 2단계 분석에서 만들어 서버가 검증한 문장입니다. 여기서 지어내지 않습니다.
LLM은 면접자가 틀린 점을 짚었는지만 판단합니다. 정정할지와 다음 행동은 rules.decide_after_misstatement가 정합니다.
결과는 리포트에만 적고 역량 점수에는 쓰지 않습니다.
"""

import json
import logging
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
from gitinsight_contracts.analysis import Misstatement, Topic
from gitinsight_contracts.llm import CatchStatus, Intent, MisstatementJudgment
from gitinsight_contracts.report import FallbackReason

logger = logging.getLogger(__name__)

MISSTATEMENT_PURPOSE = "misstatement"
# 남은 시간이 이보다 적으면 재시도하지 않습니다.
MIN_RETRY_SEC = 0.3

MISSTATEMENT_SYSTEM_PROMPT = f"""당신은 한국어 기술 면접에서 지원자 답변을 판단하는 평가 보조자입니다.
면접관이 확인을 위해 코드에 대해 일부러 틀린 말을 했습니다. 지원자가 그 말이 틀렸다는 것을 짚었는지 판단합니다.
다음 행동이나 점수는 서버가 정합니다. 당신은 아래 세 가지만 판단합니다.

1. intent: 답변의 성격
- ANSWER: 면접관의 말에 답함 (동의해도, 반박해도 ANSWER)
- DONT_KNOW: 모른다, 기억나지 않는다고 함
- CLARIFY: 말의 뜻이나 범위를 되물음
- REPEAT: 다시 말해 달라고 함
- OFF_TOPIC: 면접관의 말과 관계없는 말

2. status: intent가 ANSWER일 때 지원자가 틀린 점을 짚었는지 (ANSWER가 아니면 missed)
- caught: 면접관의 말이 틀렸다고 하고, 그 이유가 정답(correction)과 같은 내용임
- partial: 틀렸다고는 했지만 이유를 말하지 않았거나, 이유가 정답과 다름
- missed: 면접관의 말에 동의했거나, 틀렸다고 하지 않음

3. evidence_quote: caught와 partial은 지원자 답변에서 근거가 되는 문장 일부를 글자 그대로 옮깁니다.
- 바꿔 쓰거나 요약하지 마세요.
- missed는 null로 둡니다.

{UNTRUSTED_NOTICE}"""


class MisstatementJudgmentInvalidError(Exception):
    """스키마는 맞지만 서버 검증을 통과하지 못한 판단입니다. 메시지에 답변 본문을 넣지 않습니다."""


@dataclass(frozen=True)
class MisstatementOutcome:
    judgment: MisstatementJudgment | None
    fallback_reason: FallbackReason | None
    elapsed_ms: float
    attempts: int


def build_misstatement_messages(
    topic: Topic, misstatement: Misstatement, transcript: str
) -> list[Message]:
    material = {
        "title": topic.title,
        "summary": topic.summary,
        # 면접관이 한 틀린 말과, 서버가 가진 정답입니다.
        "statement": misstatement.statement,
        "correction": misstatement.correction,
    }
    user = "\n\n".join(
        [
            "면접 주제 자료와 면접관이 일부러 한 틀린 말(statement), 정답(correction)입니다.",
            untrusted_block("misstatement", json.dumps(material, ensure_ascii=False)),
            "지원자 답변(음성 인식 자막)입니다.",
            untrusted_block("transcript", transcript),
        ]
    )
    return [
        {"role": "system", "content": MISSTATEMENT_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def validate_misstatement_judgment(
    judgment: MisstatementJudgment, transcript: str, matcher: QuoteMatcher = EXACT
) -> MisstatementJudgment:
    """인용문을 자막과 대조합니다. 답변이 아니면 짚었는지와 인용문을 비웁니다."""
    if judgment.intent is not Intent.ANSWER:
        return judgment.model_copy(update={"status": CatchStatus.MISSED, "evidence_quote": None})
    if judgment.status is CatchStatus.MISSED:
        return judgment.model_copy(update={"evidence_quote": None})
    if not judgment.evidence_quote:
        raise MisstatementJudgmentInvalidError(
            "caught와 partial 판단에는 evidence_quote가 있어야 합니다"
        )
    if not matcher.matches(judgment.evidence_quote, transcript):
        raise MisstatementJudgmentInvalidError("evidence_quote가 지원자 답변에 없습니다")
    return judgment


def catch_status(judgment: MisstatementJudgment | None) -> CatchStatus | None:
    """검증을 통과한 판단에서 짚었는지를 정합니다. 판단이 없거나 답하지 않았으면 None입니다.

    모르겠다고 하거나 다른 이야기를 한 것은 틀린 말을 짚지 못한 것입니다.
    다시 말해 달라고 한 것은 아직 답하지 않은 것입니다.
    """
    if judgment is None:
        return None
    if judgment.intent is Intent.ANSWER:
        return judgment.status
    if judgment.intent in (Intent.DONT_KNOW, Intent.OFF_TOPIC):
        return CatchStatus.MISSED
    return None


def _retry_feedback(reason: str) -> str:
    return f"앞 판단이 규칙에 맞지 않았습니다: {reason}. 규칙에 맞게 다시 판단하세요."


async def judge_misstatement(
    llm: LLMClient,
    topic: Topic,
    misstatement: Misstatement,
    transcript: str,
    *,
    timeout_s: float,
    model: str | None = None,
    quote_matcher: QuoteMatcher = EXACT,
) -> MisstatementOutcome:
    """제한 시간 안에서 판단합니다. 검증 실패는 남은 시간이 있으면 1회 재시도합니다."""
    started = time.perf_counter()
    messages = build_misstatement_messages(topic, misstatement, transcript)
    reason: FallbackReason | None = None
    attempts = 0
    while attempts < 2:
        remaining = timeout_s - (time.perf_counter() - started)
        if attempts > 0 and remaining < MIN_RETRY_SEC:
            break
        attempts += 1
        try:
            result = await llm.complete_structured(
                MisstatementJudgment,
                messages,
                purpose=MISSTATEMENT_PURPOSE,
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
            judgment = validate_misstatement_judgment(result.value, transcript, quote_matcher)
        except MisstatementJudgmentInvalidError as error:
            reason = FallbackReason.INVALID
            logger.info("misstatement_judgment_invalid attempt=%d reason=%s", attempts, error)
            messages = [
                *messages,
                {"role": "assistant", "content": result.value.model_dump_json()},
                {"role": "user", "content": _retry_feedback(str(error))},
            ]
            continue
        return MisstatementOutcome(judgment, None, (time.perf_counter() - started) * 1000, attempts)
    return MisstatementOutcome(None, reason, (time.perf_counter() - started) * 1000, attempts)
