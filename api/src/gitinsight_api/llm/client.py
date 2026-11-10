"""LLMClient 인터페이스와 LiteLLM 구현.

- 구조화 출력: Pydantic 모델을 OpenAI strict json_schema 형식으로 넘기고, 응답을 같은 모델로 다시 검증합니다.
- 스키마 위반은 1회 재시도합니다. 제한 시간은 재시도를 포함한 전체 호출에 적용합니다.
- 로그에는 목적, 모델, 토큰, 비용, 지연만 남기고 프롬프트와 응답 본문은 남기지 않습니다.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, TypedDict

import litellm
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

# LiteLLM이 오류마다 표준 출력에 안내 문구를 찍지 않게 합니다.
litellm.suppress_debug_info = True


class Message(TypedDict):
    role: str
    content: str


class LLMError(Exception):
    """LLM 호출 실패의 공통 부모입니다. 호출한 쪽은 이 예외를 잡아 대비책으로 넘어갑니다."""


class LLMTimeoutError(LLMError):
    pass


class LLMRefusalError(LLMError):
    pass


class LLMSchemaError(LLMError):
    pass


class LLMUnavailableError(LLMError):
    pass


@dataclass(frozen=True)
class LLMUsage:
    purpose: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float | None
    latency_ms: float
    attempts: int


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    value: T
    usage: LLMUsage


class CostRecorder(Protocol):
    def record(self, usage: LLMUsage) -> None: ...


@dataclass
class InMemoryCostRecorder:
    records: list[LLMUsage] = field(default_factory=list)

    def record(self, usage: LLMUsage) -> None:
        self.records.append(usage)

    def total_usd(self) -> float:
        return sum(r.cost_usd or 0.0 for r in self.records)


class LLMClient(Protocol):
    async def complete_structured[T: BaseModel](
        self,
        schema: type[T],
        messages: list[Message],
        *,
        purpose: str,
        timeout_s: float,
        model: str | None = None,
    ) -> LLMResult[T]: ...


def strict_response_format(schema: type[BaseModel]) -> dict[str, Any]:
    """OpenAI strict 구조화 출력 형식입니다. LiteLLM이 Gemini 등 다른 제공사 형식으로 바꿔 줍니다."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema.__name__,
            "schema": schema.model_json_schema(),
            "strict": True,
        },
    }


def _schema_feedback(error: ValidationError) -> str:
    # 오류 위치와 종류만 알려 주고 입력값(응답 본문)은 다시 넣지 않습니다.
    problems = [
        f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['type']}"
        for e in error.errors(include_input=False, include_url=False)[:10]
    ]
    return (
        "앞 응답이 요구한 JSON 스키마와 맞지 않았습니다. 문제: "
        + "; ".join(problems)
        + ". 스키마에 맞는 JSON 객체 하나만 다시 출력하세요."
    )


CompletionFn = Callable[..., Awaitable[Any]]


class LiteLLMClient:
    def __init__(
        self,
        default_model: str,
        *,
        cost_recorder: CostRecorder | None = None,
        completion_fn: CompletionFn | None = None,
        max_attempts: int = 2,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        # 추론 모델은 temperature를 1 외에는 받지 않으므로 기본으로 보내지 않습니다.
        # reasoning_effort를 비워 두면 모델 기본값을 씁니다. 판단 지연을 줄이려면 낮게 설정합니다.
        self._default_model = default_model
        self._cost_recorder = cost_recorder
        self._completion_fn: CompletionFn = completion_fn or litellm.acompletion
        self._max_attempts = max_attempts
        self._optional_params: dict[str, Any] = {}
        if temperature is not None:
            self._optional_params["temperature"] = temperature
        if reasoning_effort is not None:
            self._optional_params["reasoning_effort"] = reasoning_effort

    async def complete_structured[T: BaseModel](
        self,
        schema: type[T],
        messages: list[Message],
        *,
        purpose: str,
        timeout_s: float,
        model: str | None = None,
    ) -> LLMResult[T]:
        model_name = model or self._default_model
        started = time.perf_counter()
        try:
            async with asyncio.timeout(timeout_s):
                value, prompt_tokens, completion_tokens, cost, attempts = await self._attempts(
                    schema, list(messages), model_name, timeout_s
                )
        except TimeoutError:
            logger.info(
                "llm_timeout purpose=%s model=%s timeout_s=%s", purpose, model_name, timeout_s
            )
            raise LLMTimeoutError(f"{purpose}: {timeout_s}초 안에 응답하지 않았습니다") from None
        usage = LLMUsage(
            purpose=purpose,
            model=model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
            latency_ms=(time.perf_counter() - started) * 1000,
            attempts=attempts,
        )
        logger.info(
            "llm_call purpose=%s model=%s attempts=%d latency_ms=%.0f tokens=%d/%d cost_usd=%s",
            usage.purpose,
            usage.model,
            usage.attempts,
            usage.latency_ms,
            usage.prompt_tokens,
            usage.completion_tokens,
            usage.cost_usd,
        )
        if self._cost_recorder is not None:
            self._cost_recorder.record(usage)
        return LLMResult(value=value, usage=usage)

    async def _attempts[T: BaseModel](
        self, schema: type[T], messages: list[Message], model: str, timeout_s: float
    ) -> tuple[T, int, int, float | None, int]:
        prompt_tokens = completion_tokens = 0
        cost: float | None = None
        last_error: ValidationError | None = None
        for attempt in range(1, self._max_attempts + 1):
            response = await self._call(schema, messages, model, timeout_s)
            usage = getattr(response, "usage", None)
            prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
            completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
            call_cost = _response_cost(response)
            if call_cost is not None:
                cost = (cost or 0.0) + call_cost
            choice = response.choices[0]
            message = choice.message
            if getattr(message, "refusal", None):
                raise LLMRefusalError("모델이 응답을 거부했습니다")
            content = message.content or ""
            try:
                value = schema.model_validate_json(content)
            except ValidationError as error:
                last_error = error
                messages = [
                    *messages,
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": _schema_feedback(error)},
                ]
                continue
            return value, prompt_tokens, completion_tokens, cost, attempt
        raise LLMSchemaError(
            f"{self._max_attempts}회 모두 스키마 검증에 실패했습니다: "
            f"{last_error.error_count() if last_error else 0}개 오류"
        )

    async def _call(
        self, schema: type[BaseModel], messages: list[Message], model: str, timeout_s: float
    ) -> Any:
        try:
            return await self._completion_fn(
                model=model,
                messages=messages,
                response_format=strict_response_format(schema),
                timeout=timeout_s,
                **self._optional_params,
            )
        except litellm.exceptions.Timeout:
            raise TimeoutError from None
        except litellm.exceptions.ContentPolicyViolationError as error:
            raise LLMRefusalError(type(error).__name__) from None
        except Exception as error:
            # LiteLLM 제공사 오류는 공통 부모가 없어서 넓게 잡고 대비책으로 넘깁니다.
            # 오류 메시지에 요청 본문이 들어갈 수 있어 예외 종류만 남깁니다.
            logger.warning("llm_unavailable model=%s error=%s", model, type(error).__name__)
            raise LLMUnavailableError(type(error).__name__) from None


def _response_cost(response: Any) -> float | None:
    hidden = getattr(response, "_hidden_params", None) or {}
    cost = hidden.get("response_cost") if isinstance(hidden, dict) else None
    if cost is not None:
        return float(cost)
    try:
        return float(litellm.completion_cost(completion_response=response))
    except Exception:
        # 가격표에 없는 모델이면 비용을 모릅니다. 사용량 토큰은 남습니다.
        return None
