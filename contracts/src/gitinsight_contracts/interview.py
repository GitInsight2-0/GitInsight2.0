"""면접 세션 API 계약 (/api/sessions)."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from gitinsight_contracts.base import Contract, count_sentences
from gitinsight_contracts.common import LineSpan, SymbolRef

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
    # 면접관이 일부러 틀린 말을 하고 면접자가 짚는지 봅니다 (#133).
    MISSTATE = "MISSTATE"


class InterviewMode(StrEnum):
    VOICE = "voice"
    TEXT = "text"


class SessionCreate(Contract):
    """POST /api/sessions 요청입니다."""

    analysis_id: Annotated[str, Field(min_length=1, max_length=100)]
    jd_text: Annotated[str, Field(max_length=5000)] | None = None
    mode: InterviewMode
    duration_min: Annotated[int, Field(ge=3, le=15)] = 10
    # 지난 면접에서 답변이 부족했던 주제만 다시 묻습니다 (#107). 그런 주제가 없으면 409입니다.
    review_only: bool = False


class BrainReply(Contract):
    """면접 두뇌가 정한 다음 발화입니다. 음성 모델은 utterance의 뜻을 바꾸지 않고 말합니다."""

    # 첫 질문(세션 시작)에는 응답할 턴이 없어서 null입니다.
    turn_id: UUID | None
    action: Action
    utterance: Annotated[str, Field(min_length=1, max_length=400)]
    # 화면에 띄울 코드입니다. 늘 심볼 전체이고, 줄 범위를 좁혀 담지 않습니다.
    highlight: SymbolRef | None
    # highlight 안에서 이 발화와 관계있는 줄입니다 (#156). 없으면 highlight 전체를 봅니다.
    focus: LineSpan | None = None
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
        if self.focus is not None and (
            self.highlight is None or not self.focus.within(self.highlight)
        ):
            raise ValueError("focus는 highlight의 줄 범위 안에 있어야 합니다")
        return self


class SessionCreated(Contract):
    session_id: UUID
    first: BrainReply


class TurnSource(StrEnum):
    VOICE = "voice"
    TEXT = "text"


Ratio = Annotated[float, Field(ge=0, le=1)]
# 네 비율의 합이 1에서 이만큼까지 벗어나도 받습니다. 브라우저의 소수점 반올림 때문입니다.
GAZE_RATIO_TOLERANCE = 0.01


class GazeSummary(Contract):
    """면접자 시선 요약입니다 (#177). 면접자가 켰을 때만 브라우저가 웹캠으로 재서 보냅니다.

    카메라 영상은 브라우저 밖으로 나가지 않고, 서버에는 이 숫자만 옵니다.
    면접관 얼굴이 화면에 없으므로 카메라를 본 시간을 눈맞춤으로 칩니다.
    브라우저가 계산한 값이라 서버는 범위만 검사합니다. 그래서 역량 점수, 코드 지도, 성장 비교에는 쓰지 않습니다.
    네 비율은 시선을 잰 시간을 나눈 것이라 합이 1입니다.
    """

    # 시선을 잰 시간(초)입니다. 면접 한 번은 4시간을 넘지 않습니다.
    measured_seconds: Annotated[float, Field(gt=0, le=4 * 3600)]
    # 카메라(면접관)를 본 비율입니다.
    camera_ratio: Ratio
    # 화면(코드, 자막)을 본 비율입니다. 코드 면접이라 자연스럽게 높습니다.
    screen_ratio: Ratio
    # 화면 밖을 본 비율입니다.
    away_ratio: Ratio
    # 얼굴이 잡히지 않은 비율입니다.
    no_face_ratio: Ratio
    # 화면 밖으로 2초 넘게 시선을 돌린 횟수입니다.
    away_count: Annotated[int, Field(ge=0, le=10_000)]

    @model_validator(mode="after")
    def _ratios_add_up(self) -> Self:
        total = self.camera_ratio + self.screen_ratio + self.away_ratio + self.no_face_ratio
        if abs(total - 1) > GAZE_RATIO_TOLERANCE:
            raise ValueError("시선 비율의 합은 1이어야 합니다")
        return self


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
    # 이 답변 동안의 시선 요약입니다 (#180). 질문이 화면에 나온 뒤부터 답변을 보낼 때까지 잰 값이고,
    # 시선 분석을 켠 면접에서 그 구간을 충분히 잰 답변에만 있습니다. 리포트의 참고 정보에만 씁니다.
    gaze: GazeSummary | None = None


class SessionEnd(Contract):
    """POST /api/sessions/{id}/end 요청 본문입니다. 본문 없이 보내도 됩니다."""

    gaze: GazeSummary | None = None


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
