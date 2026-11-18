"""면접 계획: 주제 순서와 본 주제 수를 정합니다.

기본 순서는 규칙으로 정합니다. 기여 근거가 있는 주제를 앞에 두고, 같은 라벨 안에서는 쉬운 주제부터 묻습니다.
라벨은 순서에만 쓰고 점수에는 쓰지 않습니다.
JD가 있으면 LLM이 허용된 topic_id 안에서만 순서를 바꾸고, 검증에 실패하면 규칙 순서를 씁니다.
같은 레포로 다시 보는 면접이면 지난 면접에서 막혔던 함수의 주제와 그 뒤 커밋이 들어온 함수의 주제를 맨 앞에 둡니다 (#107).
이 순서는 서버가 지난 리포트에서 정하고, 위의 두 순서보다 먼저입니다.
"""

import json
import logging
from dataclasses import dataclass
from typing import Protocol

from gitinsight_api.brain.config import InterviewConfig
from gitinsight_api.llm import LLMClient, LLMError, Message
from gitinsight_api.llm.untrusted import UNTRUSTED_NOTICE, untrusted_block
from gitinsight_contracts.analysis import AnalysisResult, ContributionLabel
from gitinsight_contracts.llm import InterviewPlanOutput

logger = logging.getLogger(__name__)

PLAN_PURPOSE = "plan"
MAX_PERSONA_KEYWORDS = 5
MAX_KEYWORD_LENGTH = 30

_LABEL_RANK = {
    ContributionLabel.VERIFIED: 0,
    ContributionLabel.SHARED: 1,
    ContributionLabel.UNVERIFIED: 2,
}


@dataclass(frozen=True)
class RevisitTargets:
    """같은 레포로 다시 면접을 볼 때 먼저 물을 주제입니다 (#107). 서버가 지난 리포트의 코드 지도에서 고릅니다."""

    # 지난 면접에서 막혔던 함수의 주제입니다.
    stuck: frozenset[str] = frozenset()
    # 지난 면접 이후 커밋이 들어온 함수의 주제입니다.
    changed: frozenset[str] = frozenset()

    def rank(self, topic_id: str) -> int:
        if topic_id in self.stuck:
            return 0
        if topic_id in self.changed:
            return 1
        return 2


NO_REVISIT = RevisitTargets()


class RevisitSource(Protocol):
    """지난 면접 기록에서 먼저 물을 주제를 찾습니다. 구현은 report/code_map.py의 ReportHistory입니다."""

    async def targets(self, owner_id: str | None, analysis: AnalysisResult) -> RevisitTargets: ...


@dataclass(frozen=True)
class InterviewPlan:
    topic_order: list[str]
    main_topic_count: int
    persona_keywords: list[str]
    source: str


def rule_order(analysis: AnalysisResult) -> list[str]:
    indexed = list(enumerate(analysis.topics))
    indexed.sort(key=lambda it: (_LABEL_RANK[it[1].contribution_label], it[1].difficulty, it[0]))
    return [topic.topic_id for _, topic in indexed]


def revisit_first(order: list[str], revisit: RevisitTargets) -> list[str]:
    """막혔던 함수의 주제, 커밋이 들어온 함수의 주제, 나머지 순서로 놓습니다. 같은 묶음 안의 순서는 그대로 둡니다."""
    return sorted(order, key=revisit.rank)


def _limit(order: list[str], config: InterviewConfig) -> tuple[list[str], int]:
    main = min(config.main_topics, len(order))
    return order[: main + config.spare_topics], main


def build_plan_messages(analysis: AnalysisResult, jd_text: str) -> list[Message]:
    topics = [
        {"topic_id": t.topic_id, "title": t.title, "summary": t.summary} for t in analysis.topics
    ]
    system = (
        "당신은 기술 면접 계획을 돕습니다. 채용공고(JD)와 면접 주제 목록을 보고, "
        "JD와 관련이 깊은 주제가 앞에 오도록 topic_order를 정하세요. "
        "topic_order에는 주제 목록에 있는 topic_id만 각각 한 번씩 쓰세요. "
        f"persona_keywords에는 JD에서 면접관이 강조할 기술 키워드를 최대 {MAX_PERSONA_KEYWORDS}개 적으세요. "
        + UNTRUSTED_NOTICE
    )
    user = "\n\n".join(
        [
            untrusted_block("job_description", jd_text),
            untrusted_block("topics", json.dumps(topics, ensure_ascii=False)),
        ]
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _validated_order(output: InterviewPlanOutput, rules: list[str]) -> list[str] | None:
    allowed = set(rules)
    order = output.topic_order
    if not order or len(order) != len(set(order)) or not set(order) <= allowed:
        return None
    # LLM이 일부만 골랐으면 나머지는 규칙 순서대로 뒤에 붙입니다.
    return [*order, *(t for t in rules if t not in set(order))]


def _clean_keywords(keywords: list[str]) -> list[str]:
    cleaned = [k.strip() for k in keywords if k.strip() and len(k.strip()) <= MAX_KEYWORD_LENGTH]
    return cleaned[:MAX_PERSONA_KEYWORDS]


async def plan_interview(
    analysis: AnalysisResult,
    jd_text: str | None,
    *,
    llm: LLMClient,
    config: InterviewConfig,
    timeout_s: float,
    model: str | None = None,
    revisit: RevisitTargets = NO_REVISIT,
) -> InterviewPlan:
    rules = revisit_first(rule_order(analysis), revisit)
    if not jd_text or not jd_text.strip():
        order, main = _limit(rules, config)
        return InterviewPlan(order, main, [], "rules")
    try:
        result = await llm.complete_structured(
            InterviewPlanOutput,
            build_plan_messages(analysis, jd_text),
            purpose=PLAN_PURPOSE,
            timeout_s=timeout_s,
            model=model,
        )
    except LLMError as error:
        logger.info("plan_fallback reason=%s", type(error).__name__)
        order, main = _limit(rules, config)
        return InterviewPlan(order, main, [], "rules")
    validated = _validated_order(result.value, rules)
    if validated is None:
        logger.info("plan_fallback reason=invalid_topic_order")
        order, main = _limit(rules, config)
        return InterviewPlan(order, main, [], "rules")
    order, main = _limit(revisit_first(validated, revisit), config)
    return InterviewPlan(order, main, _clean_keywords(result.value.persona_keywords), "llm")
