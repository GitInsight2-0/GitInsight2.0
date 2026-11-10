"""지연 측정 이벤트 저장. Postgres(Task 9) 전까지 로컬 JSONL 파일에 쌓습니다."""

import asyncio
import json
import time
from pathlib import Path
from typing import Protocol

from gitinsight_contracts.metrics import MetricEvent


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
