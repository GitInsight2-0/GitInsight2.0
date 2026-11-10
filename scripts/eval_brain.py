"""골든 대화를 실제 판단 LLM으로 돌려 품질을 기록합니다 (수동 실행, 실제 API 키 필요, 비용 발생).

사용:
  uv run python scripts/eval_brain.py
  uv run python scripts/eval_brain.py --timeout 10 --model openai/gpt-6.1-terra

골든 대화의 기대 판단(intent, covered 포인트)과 기대 행동을 실제 결과와 비교해
docs/evals/brain-<날짜>.md에 씁니다. 주입 문구가 든 턴은 어떤 포인트도 covered가 되면 안 됩니다.
CI에서는 돌리지 않습니다. CI는 FakeLLM으로 같은 대화를 검사합니다(api/tests/test_brain.py).
"""

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from gitinsight_api.brain.analysis_source import FixtureAnalysisSource
from gitinsight_api.brain.repository import InMemorySessionRepository
from gitinsight_api.brain.service import InterviewBrain
from gitinsight_api.brain.state import TurnRecord
from gitinsight_api.llm import InMemoryCostRecorder, LiteLLMClient
from gitinsight_api.settings import Settings
from gitinsight_contracts.fixtures import (
    GoldenConversation,
    GoldenTurn,
    load_analysis,
    load_conversations,
)
from gitinsight_contracts.interview import InterviewMode, SessionCreate, TurnRequest, TurnSource
from gitinsight_contracts.llm import CoverageStatus

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Row:
    conversation: str
    index: int
    expected_action: str
    actual_action: str
    expected_intent: str
    actual_intent: str
    expected_covered: str
    actual_covered: str
    fallback: str
    judge_ms: str
    ok: bool


def expected_covered(turn: GoldenTurn) -> set[str]:
    # 검증 실패를 기대한 턴(주입 문구 등)은 실제로 다룬 포인트가 없어야 합니다.
    if turn.judgment is None or turn.llm_behavior == "invalid":
        return set()
    return {c.point_id for c in turn.judgment.coverage if c.status is CoverageStatus.COVERED}


def actual_covered(record: TurnRecord) -> set[str]:
    if record.judgment is None:
        return set()
    return {c.point_id for c in record.judgment.coverage if c.status is CoverageStatus.COVERED}


async def run_conversation(
    conversation: GoldenConversation, client: LiteLLMClient, timeout_s: float
) -> list[Row]:
    analysis = load_analysis(conversation.analysis)
    now = [datetime(2026, 11, 16, tzinfo=UTC)]
    brain = InterviewBrain(
        analyses=FixtureAnalysisSource(analysis),
        repository=InMemorySessionRepository(),
        llm=client,
        judge_timeout_s=timeout_s,
        clock=lambda: now[0],
    )
    session_id, _ = await brain.start(
        SessionCreate(
            analysis_id=analysis.analysis_id,
            mode=InterviewMode.TEXT,
            duration_min=conversation.duration_min,
        )
    )
    rows: list[Row] = []
    for index, turn in enumerate(conversation.turns, start=1):
        now[0] += timedelta(seconds=conversation.seconds_per_turn)
        reply = await brain.handle_turn(
            session_id,
            TurnRequest(turn_id=uuid4(), transcript=turn.transcript, source=TurnSource.TEXT),
        )
        record = (await brain.repository.list_turns(session_id))[-1]
        expected_intent = turn.judgment.intent.value if turn.judgment else "-"
        actual_intent = record.judgment.intent.value if record.judgment else "-"
        exp_cov = expected_covered(turn)
        act_cov = actual_covered(record)
        if turn.llm_behavior == "timeout":
            # 실제 모델에서는 시간 초과를 재현하지 않으므로 행동만 기록합니다.
            ok = True
        elif turn.llm_behavior == "invalid":
            ok = not act_cov
        else:
            ok = actual_intent == expected_intent and act_cov == exp_cov
        rows.append(
            Row(
                conversation=conversation.name,
                index=index,
                expected_action=turn.expect_action.value,
                actual_action=reply.action.value,
                expected_intent=expected_intent,
                actual_intent=actual_intent,
                expected_covered=", ".join(sorted(exp_cov)) or "-",
                actual_covered=", ".join(sorted(act_cov)) or "-",
                fallback=record.fallback_reason.value if record.fallback_reason else "-",
                judge_ms=f"{record.judge_ms:.0f}" if record.judge_ms is not None else "-",
                ok=ok,
            )
        )
    return rows


async def run(args: argparse.Namespace) -> int:
    settings = Settings()
    model = args.model or settings.llm_judge_model
    recorder = InMemoryCostRecorder()
    client = LiteLLMClient(
        model, cost_recorder=recorder, reasoning_effort=settings.llm_judge_reasoning_effort
    )
    rows: list[Row] = []
    for conversation in load_conversations():
        rows.extend(await run_conversation(conversation, client, args.timeout))

    passed = sum(r.ok for r in rows)
    lines = [
        f"# 면접 두뇌 평가 {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        "",
        f"- 모델: `{model}`, 판단 제한 시간 {args.timeout}초",
        f"- 판단 일치: {passed}/{len(rows)} 턴",
        f"- LLM 호출 {len(recorder.records)}회, 비용 약 ${recorder.total_usd():.4f}",
        "- 기대 행동과 실제 행동이 달라지면 그 뒤 턴은 다른 질문에 대한 답변이 되므로 함께 보아야 합니다.",
        "",
        "| 대화 | 턴 | 기대 행동 | 실제 행동 | 기대 intent | 실제 intent | 기대 covered | 실제 covered | 대체 사유 | 판단 ms | 일치 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {r.conversation} | {r.index} | {r.expected_action} | {r.actual_action} | {r.expected_intent} "
        f"| {r.actual_intent} | {r.expected_covered} | {r.actual_covered} | {r.fallback} | {r.judge_ms} "
        f"| {'O' if r.ok else 'X'} |"
        for r in rows
    ]
    out = ROOT / "docs" / "evals" / f"brain-{datetime.now(UTC):%Y-%m-%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines))
    print(f"\n기록: {out}")
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    parser = argparse.ArgumentParser(description="골든 대화 실제 LLM 평가")
    parser.add_argument("--model", default=None)
    parser.add_argument("--timeout", type=float, default=10.0)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
