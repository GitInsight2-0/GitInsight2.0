"""테스트와 CI용 가짜 LLM. 네트워크 없이 대본 응답, 지연, 예외를 재현합니다."""

import asyncio
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel

from gitinsight_api.llm.client import LLMError, LLMResult, LLMTimeoutError, LLMUsage, Message


@dataclass(frozen=True)
class FakeStep:
    value: BaseModel | None = None
    error: LLMError | None = None
    delay_s: float = 0.0


@dataclass(frozen=True)
class FakeCall:
    purpose: str
    schema: type[BaseModel]
    messages: list[Message]
    timeout_s: float


Responder = Callable[[str, type[BaseModel], list[Message]], FakeStep]


@dataclass
class FakeLLM:
    """purpose별 대본 큐에서 응답을 꺼냅니다. 큐가 비면 responder를 부르고, 그것도 없으면 오류입니다."""

    responder: Responder | None = None
    calls: list[FakeCall] = field(default_factory=list)
    _queues: dict[str, deque[FakeStep]] = field(default_factory=lambda: defaultdict(deque))

    def push(self, purpose: str, *steps: FakeStep) -> None:
        self._queues[purpose].extend(steps)

    def calls_for(self, purpose: str) -> list[FakeCall]:
        return [c for c in self.calls if c.purpose == purpose]

    async def complete_structured[T: BaseModel](
        self,
        schema: type[T],
        messages: list[Message],
        *,
        purpose: str,
        timeout_s: float,
        model: str | None = None,
    ) -> LLMResult[T]:
        self.calls.append(FakeCall(purpose, schema, list(messages), timeout_s))
        queue = self._queues[purpose]
        if queue:
            step = queue.popleft()
        elif self.responder is not None:
            step = self.responder(purpose, schema, messages)
        else:
            raise AssertionError(f"FakeLLM: '{purpose}' 대본이 비어 있습니다")
        if step.delay_s > 0:
            if step.delay_s >= timeout_s:
                await asyncio.sleep(timeout_s)
                raise LLMTimeoutError(f"{purpose}: {timeout_s}초 초과 (가짜)")
            await asyncio.sleep(step.delay_s)
        if step.error is not None:
            raise step.error
        if not isinstance(step.value, schema):
            raise AssertionError(f"FakeLLM: '{purpose}'에 {schema.__name__}가 아닌 응답")
        usage = LLMUsage(
            purpose=purpose,
            model=model or "fake",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
            latency_ms=step.delay_s * 1000,
            attempts=1,
        )
        return LLMResult(value=step.value, usage=usage)
