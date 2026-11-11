"""워커 테스트 공용 도구. pytest 설정의 pythonpath로 테스트 모듈에서 import합니다."""

from gitinsight_contracts.api import AnalysisState


class Recorder:
    """보고된 분석 상태를 순서대로 모읍니다."""

    def __init__(self) -> None:
        self.states: list[AnalysisState] = []

    async def report(self, state: AnalysisState) -> None:
        self.states.append(state)
