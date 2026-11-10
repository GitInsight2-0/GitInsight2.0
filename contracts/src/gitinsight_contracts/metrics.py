"""지연 측정 이벤트 계약 (POST /api/metrics).

자막 원문이나 코드 본문이 들어오지 않도록 허용 필드만 받고, 문자열은 짧은 식별자 형식으로 제한합니다.
"""

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import Field

from gitinsight_contracts.base import Contract

ShortCode = Annotated[str, Field(pattern=r"^[a-z0-9_.-]{1,64}$")]


class MetricName(StrEnum):
    VAD_START = "vad_start"
    VAD_END = "vad_end"
    FIRST_AUDIO = "first_audio"
    BARGE_IN = "barge_in"
    DELEGATION_START = "delegation_start"
    DELEGATION_END = "delegation_end"
    FILLER_START = "filler_start"
    ERROR = "error"


class MetricEvent(Contract):
    name: MetricName
    session_id: UUID | None = None
    turn_id: UUID | None = None
    # 클라이언트 epoch 밀리초입니다.
    at_ms: Annotated[int, Field(ge=0)]
    value_ms: Annotated[float, Field(ge=0)] | None = None
    provider: ShortCode | None = None
    scenario: ShortCode | None = None
    error_code: ShortCode | None = None
