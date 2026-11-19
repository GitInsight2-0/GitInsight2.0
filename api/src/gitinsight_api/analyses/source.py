"""분석 기록 저장소(AnalysisStore)를 면접 두뇌와 리포트의 분석 소스(AnalysisSource)로 씁니다 (#52).

- 완료된 결과는 바뀌지 않으므로 서버 메모리에 캐시합니다. 턴마다 DB를 읽지 않습니다.
- 소유자 확인은 캐시하지 않고 매번 기록을 읽습니다. 세션을 만들 때 한 번만 부릅니다.
- DB 연결 오류는 AnalysisUnavailableError(503)로 바꿉니다. 로그에는 예외 종류만 남깁니다. 접속 주소가 메시지에 섞일 수 있습니다.
- 다시 보내도 같은 DB 오류(테이블 없음 등)는 그대로 올려서 서버의 DB 오류 처리기(500)가 받습니다 (#101).
"""

import logging
from collections import OrderedDict

from gitinsight_api.analyses.store import AnalysisStore
from gitinsight_api.db import RETRYABLE_DB_ERRORS
from gitinsight_contracts.analysis import AnalysisResult

logger = logging.getLogger(__name__)

# 캐시에 두는 분석 결과 수입니다. 동시에 진행하는 면접 수보다 넉넉하면 됩니다.
CACHE_SIZE = 32


class AnalysisUnavailableError(Exception):
    """분석 결과를 읽지 못했습니다. 메시지는 예외 종류 이름입니다."""


class StoredAnalysisSource:
    def __init__(self, store: AnalysisStore, *, cache_size: int = CACHE_SIZE) -> None:
        self._store = store
        self._cache_size = cache_size
        self._cache: OrderedDict[str, AnalysisResult] = OrderedDict()

    async def owner_of(self, analysis_id: str) -> str | None:
        """분석을 요청한 사용자 id입니다. 기록이 없으면 None입니다."""
        try:
            record = await self._store.get_record(analysis_id)
        except RETRYABLE_DB_ERRORS as error:
            raise _unavailable(error) from None
        return record.key.user_id if record is not None else None

    async def get(self, analysis_id: str) -> AnalysisResult | None:
        """완료된 결과를 돌려줍니다. 소유자 확인은 하지 않습니다 (owner_of로 먼저 확인합니다)."""
        cached = self._cache.get(analysis_id)
        if cached is not None:
            self._cache.move_to_end(analysis_id)
            return cached
        try:
            result = await self._store.get(analysis_id)
        except RETRYABLE_DB_ERRORS as error:
            raise _unavailable(error) from None
        if result is not None:
            self._cache[analysis_id] = result
            if len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return result


def _unavailable(error: Exception) -> AnalysisUnavailableError:
    kind = type(error).__name__
    logger.warning("analysis_source_failed error=%s", kind)
    return AnalysisUnavailableError(kind)
