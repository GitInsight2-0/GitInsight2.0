"""분석 요청 처리: 캐시 재사용, 같은 분석 한 번만 실행, 상태 조회.

실제 분석은 워커가 합니다. 서비스는 새 기록을 만들었을 때만 Launcher로 작업을 넘깁니다.
작업을 넘기는 방식(같은 프로세스, 큐, 별도 워커 서비스)은 배포 구성(#19)에 맞춰 Launcher 구현으로 정합니다.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

from pydantic import SecretStr

from gitinsight_api.analyses.store import AnalysisKey, AnalysisRecord, AnalysisStore
from gitinsight_contracts.api import AnalysisCreated, AnalysisState, AnalysisStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LaunchContext:
    """분석을 시작할 때만 쓰는 요청자 정보입니다. 저장소에 저장하지 않고 작업이 끝나면 버립니다.

    github_token은 요청자 본인의 GitHub 토큰입니다. 비공개 레포를 내려받을 때 씁니다.
    login은 기여 라벨을 맞출 GitHub noreply 이메일을 만들 때 씁니다.
    """

    github_token: SecretStr | None = None
    login: str | None = None


NO_CONTEXT = LaunchContext()


class Launcher(Protocol):
    async def launch(self, record: AnalysisRecord, context: LaunchContext = NO_CONTEXT) -> None:
        """분석 작업을 시작하거나 대기열에 넣습니다. 끝까지 기다리지 않고 바로 돌아옵니다."""
        ...


class ManagedLauncher(Launcher, Protocol):
    """서버 시작·종료에 맞춰 관리하는 Launcher입니다 (같은 프로세스에서 분석을 돌리는 구현)."""

    async def recover(self) -> int:
        """지난 프로세스에서 진행 중이던 기록을 닫습니다. 서버를 시작할 때 부릅니다."""
        ...

    async def aclose(self) -> None:
        """돌고 있는 작업을 멈춥니다. 서버를 끌 때 부릅니다."""
        ...


class AnalysisLaunchError(Exception):
    """분석 작업을 넘기지 못했습니다. 기록은 failed로 닫혀 다시 요청할 수 있습니다. 메시지는 예외 종류입니다."""


class AnalysisService:
    def __init__(self, store: AnalysisStore, launcher: Launcher) -> None:
        self._store = store
        self._launcher = launcher

    async def request(
        self,
        repo_full_name: str,
        commit_sha: str,
        user_id: str,
        context: LaunchContext = NO_CONTEXT,
    ) -> AnalysisCreated:
        """분석을 요청합니다. 같은 키의 완료된 결과가 있으면 cached=True로 바로 돌려줍니다.

        같은 키로 진행 중인 분석이 있으면 새로 시작하지 않고 그 analysis_id를 돌려줍니다.
        context는 새로 분석을 시작할 때만 Launcher에 넘깁니다.
        """
        key = AnalysisKey(repo_full_name=repo_full_name, commit_sha=commit_sha, user_id=user_id)
        record, created = await self._store.get_or_create(key)
        if created:
            try:
                await self._launcher.launch(record, context)
            except Exception as error:
                # 작업을 넘기지 못한 기록이 진행 중으로 남으면 같은 키의 재요청이 막힙니다.
                logger.warning("analysis_launch_failed error=%s", type(error).__name__)
                await self._store.update_state(
                    record.analysis_id,
                    record.state.model_copy(
                        update={"status": AnalysisStatus.FAILED, "error": type(error).__name__}
                    ),
                )
                raise AnalysisLaunchError(type(error).__name__) from error
        status = record.state.status
        return AnalysisCreated(
            analysis_id=record.analysis_id,
            status=status,
            cached=status is AnalysisStatus.DONE,
        )

    async def state(self, analysis_id: str, user_id: str) -> AnalysisState | None:
        """요청자 본인의 분석 상태를 돌려줍니다. 없거나 다른 사람의 분석이면 None입니다."""
        record = await self._store.get_record(analysis_id)
        if record is None or record.key.user_id != user_id:
            return None
        return record.state
