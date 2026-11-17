"""지연 측정 이벤트 저장.

DATABASE_URL이 있으면 Postgres(metric_events 테이블), 없으면 로컬 JSONL 파일에 쌓습니다.
DB 장애를 JSONL로 대신 받지 않습니다. 같은 서버가 두 곳에 나눠 쌓으면 분석할 때 합치기 어렵기 때문입니다.
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Protocol

from sqlalchemy import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from gitinsight_api.tables import metric_events
from gitinsight_contracts.metrics import MetricEvent

logger = logging.getLogger(__name__)


class MetricsStoreUnavailableError(Exception):
    """저장소에 쓰지 못했습니다. 메시지에는 예외 종류 이름만 넣습니다."""


class MetricsStore(Protocol):
    async def append(self, event: MetricEvent) -> None: ...


class JsonlMetricsStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()

    async def append(self, event: MetricEvent) -> None:
        record = event.model_dump(mode="json")
        record["received_at_ms"] = int(time.time() * 1000)
        line = json.dumps(record, ensure_ascii=False) + "\n"
        async with self._lock:
            await asyncio.to_thread(self._write, line)

    def _write(self, line: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(line)


class PostgresMetricsStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def append(self, event: MetricEvent) -> None:
        values = event.model_dump(mode="python")
        values["name"] = event.name.value
        try:
            async with self._engine.begin() as conn:
                await conn.execute(insert(metric_events).values(**values))
        except (SQLAlchemyError, OSError) as error:
            # 오류 메시지에는 접속 주소가 들어갈 수 있어 예외 종류만 남깁니다.
            logger.warning("metrics_store_failed error=%s", type(error).__name__)
            raise MetricsStoreUnavailableError(type(error).__name__) from None
