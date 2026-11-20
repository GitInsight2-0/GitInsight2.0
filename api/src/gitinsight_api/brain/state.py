"""세션 상태와 턴 기록. Postgres(brain/postgres.py)에 JSON으로 저장하므로 Pydantic 모델로 둡니다.

필드를 바꾸면 DB에 저장된 진행 중 세션을 읽지 못할 수 있습니다. 새 필드에는 기본값을 줍니다.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from gitinsight_contracts.common import LineSpan, SymbolRef
from gitinsight_contracts.interview import Action, BrainReply, InterviewMode
from gitinsight_contracts.llm import Judgment, MisstatementJudgment
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
    # 이 주제에서 판단 없이 지나간 답변 수입니다 (판단 LLM 시간 초과·검증 실패).
    # 그 답변이 어떤 포인트를 다뤘는지 모르므로, 0보다 크면 빠진 포인트를 다시 묻지(PROBE) 않습니다.
    unjudged_answers: int = 0
    # 세션 시작 기준으로 이 주제를 시작한 시각(초)입니다.
    started_at_sec: float = 0.0


class SessionState(BaseModel):
    session_id: UUID
    analysis_id: str
    # HTTP API가 로그인 사용자(auth.get_current_user)로 채우고 소유자 확인에 씁니다.
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
    # last_question이 가리키는 줄입니다 (#156). 확인 질문(PROBE)에서만 채우고, 질문이 바뀌면 비웁니다.
    last_focus: LineSpan | None = None
    persona_keywords: list[str] = Field(default_factory=list)
    plan_source: str = "rules"
    # 지난 면접에서 답변이 부족했던 주제만 묻는 다시 보기 면접입니다 (#107). 주제를 시작하는 문장을 고르는 데 씁니다.
    review_only: bool = False
    # 면접관이 일부러 틀린 말을 한 주제입니다 (#133). 면접 한 번에 최대 한 번이라, 값이 있으면 다시 하지 않습니다.
    misstatement_topic_id: str | None = None
    # 틀린 말을 했고 아직 답을 받지 못했습니다. 다음 답변은 그 말을 짚었는지만 봅니다.
    misstatement_pending: bool = False
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
    # 면접관이 일부러 한 틀린 말에 답한 턴입니다 (#133). 이 턴은 judgment가 없고 점수와 코드 지도에 쓰지 않습니다.
    misstatement: bool = False
    # 틀린 말을 짚었는지에 대한 판단입니다. 시간 초과·검증 실패면 None입니다.
    catch: MisstatementJudgment | None = None
    fallback_reason: FallbackReason | None
    reply: BrainReply
    received_at: datetime
    # 클라이언트가 보낸 시각(epoch ms)입니다. 지연 측정에만 씁니다.
    client_speech_started_at: int | None = None
    client_speech_ended_at: int | None = None
    # 서버가 턴을 받은 뒤 응답할 때까지 걸린 시간입니다.
    server_ms: float | None = None
    judge_ms: float | None = None
