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


class LineSpan(Contract):
    """심볼 안의 좁은 줄 범위입니다 (#156). 분석 결과에서 얻고, LLM이 만들지 않습니다.

    if·for·try 블록처럼 이름이 없는 범위라 symbol_id, name, kind가 없습니다.
    파일과 커밋은 이 범위를 담은 심볼(SymbolRef)의 것을 씁니다.
    """

    start_line: Annotated[int, Field(ge=1)]
    end_line: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _check_range(self) -> Self:
        if self.end_line < self.start_line:
            raise ValueError("end_line은 start_line보다 작을 수 없습니다")
        return self

    def within(self, symbol: SymbolRef) -> bool:
        """이 범위가 심볼의 줄 범위 안에 있는지입니다."""
        return symbol.start_line <= self.start_line and self.end_line <= symbol.end_line
