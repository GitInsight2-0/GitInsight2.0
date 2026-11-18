"""리포트와 성장 비교 계약 (/api/reports, /api/me/progress)."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, model_validator

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


class ExplainStatus(StrEnum):
    """질문받은 함수를 얼마나 설명했는지입니다. 서버 규칙이 턴 기록으로 정하고, 점수와는 따로입니다."""

    EXPLAINED = "explained"
    EXPLAINED_AFTER_HINT = "explained_after_hint"
    STUCK = "stuck"


class CodeMapEntry(Contract):
    """이번 면접에서 질문받은 함수 하나와 그 상태입니다. 코드 위치는 분석 결과에서 가져옵니다."""

    symbol: SymbolRef
    # https://github.com/{owner}/{repo}/blob/{sha}/{path}#L{start}-L{end}
    url: str
    topic_id: str
    status: ExplainStatus


class CodeMap(Contract):
    """설명 못 한 코드 지도입니다 (#107). LLM 없이 턴 기록을 집계해 만듭니다.

    entries는 이번 면접에서 질문받은 함수입니다.
    숫자 네 개는 같은 사용자가 이 레포로 본 면접을 모두 합친 값이고, 함수마다 가장 최근 상태로 셉니다.
    """

    analysis_id: str
    repo_full_name: str
    entries: list[CodeMapEntry]
    # 이 레포의 핵심 함수(분석 주제의 주 코드) 수입니다.
    key_function_count: Annotated[int, Field(ge=0)]
    # 핵심 함수 중 힌트 없이 설명한 수, 힌트를 받고 설명한 수, 막힌 수입니다.
    explained_count: Annotated[int, Field(ge=0)]
    after_hint_count: Annotated[int, Field(ge=0)]
    stuck_count: Annotated[int, Field(ge=0)]


class ListenerQuestion(Contract):
    """AI 청자 테스트의 객관식 문제 하나와 청자가 고른 보기입니다. 보기 번호는 0부터 셉니다."""

    topic_id: str
    # 이 문제가 확인하는 기대 요점입니다.
    point_id: str
    question: str
    # 마지막 보기는 항상 "알 수 없음"입니다.
    choices: Annotated[list[str], Field(min_length=3)]
    correct_index: Annotated[int, Field(ge=0)]
    # 청자가 답변 기록 없이 고른 보기입니다 (대조군). 근거가 없으면 "알 수 없음"을 고르게 합니다.
    blind_index: Annotated[int, Field(ge=0)]
    # 청자가 지원자의 답변 기록만 읽고 고른 보기입니다.
    informed_index: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def _check(self) -> Self:
        # "알 수 없음"은 정답이 될 수 없습니다.
        if self.correct_index >= len(self.choices) - 1:
            raise ValueError("correct_index가 보기 범위를 벗어났습니다")
        if max(self.blind_index, self.informed_index) >= len(self.choices):
            raise ValueError("청자가 고른 보기가 보기 범위를 벗어났습니다")
        return self


class ListenerTest(Contract):
    """AI 청자 테스트입니다 (#111). 설명이 전달된 정도를 봅니다.

    코드를 본 적 없는 LLM(청자)이 지원자의 답변 기록만 읽고 그 코드에 대한 객관식 문제를 풉니다.
    문제는 면접에서 물어본 주제의 기대 요점으로 만들고, 맞았는지는 서버가 보기 번호를 비교해 정합니다.
    역량 수준(점수)과는 따로입니다.
    """

    questions: Annotated[list[ListenerQuestion], Field(min_length=1)]
    # 답변 기록 없이 맞힌 문제 수입니다 (대조군). 설명 없이도 답이 드러나는 문제의 수라서 0에 가까워야 합니다.
    blind_correct: Annotated[int, Field(ge=0)]
    # 답변 기록을 읽고 맞힌 문제 수입니다.
    informed_correct: Annotated[int, Field(ge=0)]


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
    # 이 필드가 생기기 전에 저장한 리포트에는 없어서 null입니다.
    code_map: CodeMap | None = None
    # 청자 테스트를 하지 못했거나 이 필드가 생기기 전에 저장한 리포트에서는 null입니다.
    listener_test: ListenerTest | None = None


class CompetencyLevel(Contract):
    name: Competency
    level: Level


class ProgressEntry(Contract):
    session_id: UUID
    date: datetime
    competencies: list[CompetencyLevel]
