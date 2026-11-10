"""면접 세션 API 계약 (/api/sessions)."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from gitinsight_contracts.base import Contract, count_sentences
from gitinsight_contracts.common import SymbolRef

MAX_UTTERANCE_SENTENCES = 2


class Action(StrEnum):
    ASK = "ASK"
    FOLLOW_UP = "FOLLOW_UP"
    PROBE = "PROBE"
    HINT = "HINT"
    NEXT_TOPIC = "NEXT_TOPIC"
    REPEAT = "REPEAT"
    CLARIFY = "CLARIFY"
    WRAP_UP = "WRAP_UP"


class InterviewMode(StrEnum):
    VOICE = "voice"
    TEXT = "text"


class SessionCreate(Contract):
    """POST /api/sessions 요청입니다."""

    analysis_id: Annotated[str, Field(min_length=1, max_length=100)]
    jd_text: Annotated[str, Field(max_length=5000)] | None = None
    mode: InterviewMode
    duration_min: Annotated[int, Field(ge=3, le=15)] = 10


class BrainReply(Contract):
    """면접 두뇌가 정한 다음 발화입니다. 음성 모델은 utterance의 뜻을 바꾸지 않고 말합니다."""

    # 첫 질문(세션 시작)에는 응답할 턴이 없어서 null입니다.
    turn_id: UUID | None
    action: Action
    utterance: Annotated[str, Field(min_length=1, max_length=400)]
    highlight: SymbolRef | None
    topic_id: str | None
    depth: Annotated[int, Field(ge=0)]
    remaining_sec: Annotated[int, Field(ge=0)]
    done: bool

    @model_validator(mode="after")
    def _check_utterance(self) -> Self:
        sentences = count_sentences(self.utterance)
        if sentences > MAX_UTTERANCE_SENTENCES:
            raise ValueError(
                f"utterance는 {MAX_UTTERANCE_SENTENCES}문장 이내여야 합니다 (현재 {sentences}문장)"
            )
        return self


class SessionCreated(Contract):
    session_id: UUID
    first: BrainReply


class TurnSource(StrEnum):
    VOICE = "voice"
    TEXT = "text"


class TurnRequest(Contract):
    """POST /api/sessions/{id}/turns 요청입니다.

    turn_id는 클라이언트가 만든 UUID이고, 같은 값이 다시 오면 처음 결과를 그대로 돌려줍니다.
    시각은 클라이언트 epoch 밀리초이며 지연 측정에만 씁니다.
    """

    turn_id: UUID
    transcript: Annotated[str, Field(max_length=4000)]
    speech_started_at: Annotated[int, Field(ge=0)] | None = None
    speech_ended_at: Annotated[int, Field(ge=0)] | None = None
    source: TurnSource


class SessionEnded(Contract):
    report_id: UUID


class VoiceToken(Contract):
    """POST /api/sessions/{id}/voice-token 응답입니다. 브라우저에는 임시 토큰만 보냅니다."""

    provider: Literal["openai", "gemini"]
    client_secret: str
    expires_at: datetime
    model: str
    instructions: str
    tools: list[dict[str, Any]]
