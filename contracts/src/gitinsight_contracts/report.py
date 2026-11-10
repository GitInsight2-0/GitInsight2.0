"""리포트와 성장 비교 계약 (/api/reports, /api/me/progress)."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import Field

from gitinsight_contracts.base import Contract
from gitinsight_contracts.common import SymbolRef
from gitinsight_contracts.interview import Action
from gitinsight_contracts.llm import Competency, Intent

Level = Annotated[int, Field(ge=1, le=4)]


class Evidence(Contract):
    turn_id: UUID
    quote: str
    symbol: SymbolRef | None


class CompetencyScore(Contract):
    name: Competency
    level: Level
    evidence: Annotated[list[Evidence], Field(min_length=1)]


class CodeLink(Contract):
    symbol: SymbolRef
    # https://github.com/{owner}/{repo}/blob/{sha}/{path}#L{start}-L{end}
    url: str


class TopicReport(Contract):
    topic_id: str
    question: str
    covered: list[str]
    missing: list[str]
    model_answer: str | None
    code_links: list[CodeLink]


class ReportKind(StrEnum):
    # 역량 평가까지 포함한 리포트입니다.
    FULL = "full"
    # 리포트 생성에 실패했을 때의 대비책인 턴별 판단 표입니다.
    TURN_TABLE = "turn_table"


class FallbackReason(StrEnum):
    TIMEOUT = "timeout"
    INVALID = "invalid"
    REFUSAL = "refusal"
    ERROR = "error"
    FORCED = "forced"


class TurnRow(Contract):
    """턴별 판단 표의 한 줄입니다."""

    turn_id: UUID
    topic_id: str | None
    question: str
    transcript: str
    intent: Intent | None
    covered_points: list[str]
    action: Action
    fallback_reason: FallbackReason | None


class Report(Contract):
    report_id: UUID
    session_id: UUID
    kind: ReportKind
    competencies: list[CompetencyScore]
    topics: list[TopicReport]
    strengths: list[str]
    improvements: list[str]
    next_topics: list[str]
    turns: list[TurnRow]


class CompetencyLevel(Contract):
    name: Competency
    level: Level


class ProgressEntry(Contract):
    session_id: UUID
    date: datetime
    competencies: list[CompetencyLevel]
