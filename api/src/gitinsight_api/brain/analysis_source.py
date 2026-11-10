"""면접 재료(분석 결과)를 읽는 인터페이스. A의 분석 결과 테이블 구현은 Task 11에서 붙입니다."""

from typing import Protocol

from gitinsight_contracts.analysis import AnalysisResult


class AnalysisSource(Protocol):
    async def get(self, analysis_id: str) -> AnalysisResult | None: ...


class FixtureAnalysisSource:
    def __init__(self, *analyses: AnalysisResult) -> None:
        self._analyses = {a.analysis_id: a for a in analyses}

    async def get(self, analysis_id: str) -> AnalysisResult | None:
        return self._analyses.get(analysis_id)
