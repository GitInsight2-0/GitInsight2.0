"""세션 상태와 턴 기록. Postgres(Task 9)에는 JSON으로 저장할 수 있게 Pydantic 모델로 둡니다."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from gitinsight_contracts.common import SymbolRef
from gitinsight_contracts.interview import Action, BrainReply, InterviewMode
from gitinsight_contracts.llm import Judgment
from gitinsight_contracts.report import FallbackReason


class TopicProgress(BaseModel):
    topic_id: str
    # 이 주제에서 나간 FOLLOW_UP 수입니다.
    depth: int = 0
    hint_given: bool = False
    # PROBE로 이미 물어본 포인트입니다.
    probed_points: list[str] = Field(default_factory=list)
    # 이 주제에서 지금까지 covered로 판단된 포인트입니다.
    covered_points: list[str] = Field(default_factory=list)
    off_topic_count: int = 0
    # 사용한 미리 만든 꼬리질문 수입니다.
    prepared_used: int = 0
    # 세션 시작 기준으로 이 주제를 시작한 시각(초)입니다.
    started_at_sec: float = 0.0


class SessionState(BaseModel):
    session_id: UUID
    analysis_id: str
    # 인증(D의 T4)이 붙으면 소유자 확인에 씁니다.
    owner_id: str | None = None
    mode: InterviewMode
    duration_sec: int
    started_at: datetime
    topic_order: list[str]
    main_topic_count: int
    current_index: int = 0
    progress: TopicProgress
    # REPEAT에서 다시 말할 직전 질문 한 문장입니다.
    last_question: str
    last_action: Action
    last_highlight: SymbolRef | None
    persona_keywords: list[str] = Field(default_factory=list)
    plan_source: str = "rules"
    turn_count: int = 0
    closed: bool = False

    def elapsed_sec(self, now: datetime) -> float:
        return max(0.0, (now - self.started_at).total_seconds())

    def remaining_sec(self, now: datetime) -> int:
        return max(0, int(self.duration_sec - self.elapsed_sec(now)))

    @property
    def current_topic_id(self) -> str:
        return self.topic_order[self.current_index]

    def has_next_topic(self) -> bool:
        return self.current_index + 1 < len(self.topic_order)


class TurnRecord(BaseModel):
    session_id: UUID
    turn_id: UUID
    index: int
    topic_id: str | None
    # 지원자가 답한 질문입니다.
    question: str
    transcript: str
    judgment: Judgment | None
    fallback_reason: FallbackReason | None
    reply: BrainReply
    received_at: datetime
    # 클라이언트가 보낸 시각(epoch ms)입니다. 지연 측정에만 씁니다.
    client_speech_started_at: int | None = None
    client_speech_ended_at: int | None = None
    # 서버가 턴을 받은 뒤 응답할 때까지 걸린 시간입니다.
    server_ms: float | None = None
    judge_ms: float | None = None
