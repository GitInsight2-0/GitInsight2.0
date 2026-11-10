"""LLM 구조화 출력 계약.

LLM은 판단 근거만 냅니다. 다음 행동, 점수, 상태 변경은 서버 규칙이 정합니다.
모든 모델은 OpenAI strict 모드 규칙(모든 필드 required, 추가 필드 금지)을 따릅니다.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import Field

from gitinsight_contracts.base import LLMOutput


class Intent(StrEnum):
    ANSWER = "ANSWER"
    DONT_KNOW = "DONT_KNOW"
    CLARIFY = "CLARIFY"
    REPEAT = "REPEAT"
    OFF_TOPIC = "OFF_TOPIC"


class CoverageStatus(StrEnum):
    COVERED = "covered"
    MISSING = "missing"
    WRONG = "wrong"


class PointCoverage(LLMOutput):
    point_id: str
    status: CoverageStatus
    # 지원자 답변에서 그대로 옮긴 문장입니다. 서버가 자막과 대조합니다.
    evidence_quote: str | None


class Followup(LLMOutput):
    question: str
    # 주제의 허용 심볼 목록 안에서만 고릅니다. 서버가 다시 확인합니다.
    symbol_id: str | None


class Judgment(LLMOutput):
    intent: Intent
    coverage: list[PointCoverage]
    followup: Followup | None


class InterviewPlanOutput(LLMOutput):
    """JD가 있을 때 주제 순서를 바꾸는 LLM 출력입니다. 허용된 topic_id만 씁니다."""

    topic_order: list[str]
    persona_keywords: list[str]


class Competency(StrEnum):
    CODE_UNDERSTANDING = "code_understanding"
    DESIGN_JUDGMENT = "design_judgment"
    PROBLEM_SOLVING = "problem_solving"
    SCALABILITY_OPERATIONS = "scalability_operations"
    COMMUNICATION = "communication"
    ROLE_DECISION = "role_decision"


COMPETENCY_TEXT: dict[Competency, str] = {
    Competency.CODE_UNDERSTANDING: "코드 이해도",
    Competency.DESIGN_JUDGMENT: "설계 판단",
    Competency.PROBLEM_SOLVING: "문제 해결·장애 대응",
    Competency.SCALABILITY_OPERATIONS: "확장성·운영",
    Competency.COMMUNICATION: "커뮤니케이션",
    Competency.ROLE_DECISION: "역할·의사결정 설명",
}


class EvidenceDraft(LLMOutput):
    turn_id: str
    quote: str
    symbol_id: str | None


class CompetencyDraft(LLMOutput):
    name: Competency
    level: Annotated[int, Field(ge=1, le=4)]
    rationale: str
    evidence: list[EvidenceDraft]


class TopicDraft(LLMOutput):
    topic_id: str
    model_answer: str


class ReportDraft(LLMOutput):
    competencies: list[CompetencyDraft]
    topics: list[TopicDraft]
    strengths: list[str]
    improvements: list[str]
    next_topics: list[str]
