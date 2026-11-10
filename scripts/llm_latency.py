"""판단 LLM 지연 측정 (수동 실행, 실제 API 키 필요, 비용 발생).

사용:
  uv run python scripts/llm_latency.py --n 20
  uv run python scripts/llm_latency.py --n 20 --model openai/gpt-6.1-terra --reasoning-effort low

실제 판단 프롬프트와 Judgment 스키마로 호출합니다.
새 스키마의 첫 요청은 추가 지연이 있어 첫 호출(예열)은 통계에서 뺍니다.
결과는 docs/evals/latency.md 끝에 추가합니다. 프롬프트와 응답 본문은 기록하지 않습니다.
"""

import argparse
import asyncio
import math
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

from gitinsight_api.brain.judge import JUDGE_PURPOSE, build_judge_messages
from gitinsight_api.llm import InMemoryCostRecorder, LiteLLMClient, LLMError
from gitinsight_api.settings import Settings
from gitinsight_contracts.fixtures import load_analysis, load_conversations
from gitinsight_contracts.llm import Judgment

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "evals" / "latency.md"


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


async def run(args: argparse.Namespace) -> int:
    settings = Settings()
    model = args.model or settings.llm_judge_model
    effort = args.reasoning_effort or settings.llm_judge_reasoning_effort
    recorder = InMemoryCostRecorder()
    client = LiteLLMClient(model, cost_recorder=recorder, reasoning_effort=effort)

    analysis = load_analysis()
    topic = analysis.topics[0]
    transcript = load_conversations()[0].turns[0].transcript
    messages = build_judge_messages(topic, topic.base_question, transcript, [])

    latencies: list[float] = []
    failures: dict[str, int] = {}
    for i in range(args.n + 1):
        try:
            result = await client.complete_structured(
                Judgment, messages, purpose=JUDGE_PURPOSE, timeout_s=args.timeout
            )
        except LLMError as error:
            failures[type(error).__name__] = failures.get(type(error).__name__, 0) + 1
            print(f"{i}: 실패 {type(error).__name__}")
            continue
        label = "예열(제외)" if i == 0 else f"{result.usage.latency_ms:.0f}ms"
        print(f"{i}: {label}")
        if i > 0:
            latencies.append(result.usage.latency_ms)

    if not latencies:
        print("성공한 호출이 없습니다.", file=sys.stderr)
        return 1
    costs = [r.cost_usd for r in recorder.records[1:] if r.cost_usd is not None]
    cost_text = f"{statistics.mean(costs):.5f}" if costs else "알 수 없음"
    failure_text = ", ".join(f"{k} {v}" for k, v in failures.items()) or "-"
    row = (
        f"| {datetime.now(UTC):%Y-%m-%d %H:%M} | `{model}` | {effort or '기본값'} | {len(latencies)} "
        f"| {statistics.median(latencies):.0f} | {percentile(latencies, 95):.0f} "
        f"| {max(latencies):.0f} | {failure_text} | {cost_text} |"
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8", newline="\n") as f:
        f.write(row + "\n")
    print(row)
    print(f"기록: {OUT}")
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    parser = argparse.ArgumentParser(description="판단 LLM 지연 측정")
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--model", default=None)
    parser.add_argument("--reasoning-effort", default=None)
    parser.add_argument("--timeout", type=float, default=30.0)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
