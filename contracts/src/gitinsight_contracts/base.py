"""계약 모델의 공통 기반 클래스와 문장 검사 도구."""

import re

from pydantic import BaseModel, ConfigDict

# 문장 끝 문장부호 뒤에 공백이나 문자열 끝이 오면 한 문장으로 셉니다.
# "3.5초"처럼 숫자 사이의 마침표는 문장 끝으로 보지 않습니다.
_SENTENCE_END = re.compile(r"[.?!。？！]+(?=\s|$)")


class Contract(BaseModel):
    """API와 모듈 사이를 오가는 모든 계약 모델의 기반입니다.

    정의하지 않은 필드를 거부해서 클라이언트가 보낸 값이 몰래 끼어들지 않게 합니다.
    응답 스키마에서는 기본값이 있는 필드도 required로 표시해 TS 타입이 정확해지게 합니다.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_serialization_defaults_required=True,
    )


class LLMOutput(BaseModel):
    """LLM 구조화 출력용 모델의 기반입니다.

    OpenAI strict 모드 규칙에 맞춰 모든 필드를 기본값 없이 선언합니다.
    선택 값은 `X | None`으로 표현합니다.
    """

    model_config = ConfigDict(extra="forbid")


def count_sentences(text: str) -> int:
    """문장 수를 셉니다. 마지막 문장에 문장부호가 없어도 한 문장으로 셉니다."""
    stripped = text.strip()
    if not stripped:
        return 0
    ends = len(_SENTENCE_END.findall(stripped))
    tail = _SENTENCE_END.split(stripped)[-1].strip()
    return ends + (1 if tail else 0)
