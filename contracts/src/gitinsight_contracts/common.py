"""여러 모듈이 함께 쓰는 기본 계약."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from gitinsight_contracts.base import Contract

CommitSha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


class SymbolKind(StrEnum):
    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"


class SymbolRef(Contract):
    """분석 결과에서 얻은 코드 위치입니다. LLM이 만들지 않습니다."""

    symbol_id: Annotated[str, Field(min_length=1, max_length=200)]
    name: Annotated[str, Field(min_length=1, max_length=200)]
    kind: SymbolKind
    path: Annotated[str, Field(min_length=1, max_length=500)]
    start_line: Annotated[int, Field(ge=1)]
    end_line: Annotated[int, Field(ge=1)]
    commit_sha: CommitSha

    @model_validator(mode="after")
    def _check_range(self) -> Self:
        if self.end_line < self.start_line:
            raise ValueError("end_line은 start_line보다 작을 수 없습니다")
        return self
