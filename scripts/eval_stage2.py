"""샘플 레포를 실제 LLM으로 2단계까지 분석해 사람이 평가할 기록을 남깁니다 (수동 실행, 실제 API 키 필요, 비용 발생).

사용:
  uv run python scripts/eval_stage2.py
  uv run python scripts/eval_stage2.py --model openai/gpt-6.1-sol
  uv run python scripts/eval_stage2.py --reasoning-effort low

API 키는 환경 변수로 받습니다. .env에 넣었다면 `uv run --env-file .env python ...`으로 실행합니다.
진행 중 로그에는 오류 종류와 개수만 나오고, 코드와 응답 본문은 나오지 않습니다.

샘플 데이터(fixtures/analysis)의 레포를 같은 고정 커밋으로 내려받아 분석하고,
만든 주제와 호출 수, 비용, 지연을 docs/evals/stage2-<날짜>.md에 씁니다.
기대 포인트가 실제 코드에 있는 내용인지, 질문이 자연스러운지는 사람이 기록을 보고 판단합니다.
CI에서는 돌리지 않습니다. CI는 FakeLLM으로 같은 흐름을 검사합니다(worker/tests/test_stage2.py).
"""

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from gitinsight_api.llm import InMemoryCostRecorder, LiteLLMClient
from gitinsight_api.settings import Settings
from gitinsight_contracts.analysis import AnalysisResult
from gitinsight_contracts.api import AnalysisState
from gitinsight_contracts.fixtures import load_analysis
from gitinsight_worker.git import git_cloner
from gitinsight_worker.pipeline import run_analysis

ROOT = Path(__file__).resolve().parents[1]


class PrintReporter:
    async def report(self, state: AnalysisState) -> None:
        print(f"  {state.status.value} {state.progress:.2f}", file=sys.stderr)


def render(result: AnalysisResult, recorder: InMemoryCostRecorder, model: str) -> list[str]:
    records = recorder.records
    lines = [
        f"# 2단계 분석 평가 ({datetime.now(UTC):%Y-%m-%d})",
        "",
        f"- 레포: `{result.repo_full_name}` @ `{result.commit_sha}`",
        f"- 모델: `{model}`",
        f"- 주제 수: {len(result.topics)}",
        f"- LLM 호출: {len(records)}회 (검증 실패 재요청 포함), 이 중 스키마 재시도 {sum(r.attempts - 1 for r in records)}회",
        f"- 비용: ${recorder.total_usd():.4f}",
        f"- 호출당 최대 지연: {max((r.latency_ms for r in records), default=0):.0f} ms",
        "",
        "사람이 확인할 것: 기대 포인트가 코드에 실제로 있는가, 질문이 존댓말 한 문장이고 자연스러운가,",
        "힌트가 답을 알려 주지 않는가, 관련 심볼이 꼬리질문과 맞는가.",
    ]
    for topic in result.topics:
        lines += [
            "",
            f"## {topic.title} (`{topic.symbol.symbol_id}`, 난이도 {topic.difficulty})",
            "",
            f"- 요약: {topic.summary}",
            f"- 질문: {topic.base_question}",
            f"- 취지: {topic.question_intent}",
            f"- 힌트: {topic.hint}",
            f"- 관련 심볼: {', '.join(s.symbol_id for s in topic.related_symbols) or '없음'}",
            "- 기대 포인트:",
            *(f"  - {p.text} → {p.probe_question}" for p in topic.expected_points),
            "- 꼬리질문:",
            *(f"  - {f.question} ({f.symbol_id or '-'})" for f in topic.prepared_followups),
        ]
    return lines


async def run(args: argparse.Namespace) -> int:
    settings = Settings()
    model = args.model or settings.llm_analysis_model
    recorder = InMemoryCostRecorder()
    client = LiteLLMClient(model, cost_recorder=recorder, reasoning_effort=args.reasoning_effort)
    label = f"{model} (reasoning_effort={args.reasoning_effort or '기본값'})"
    sample = load_analysis()
    url = f"https://github.com/{sample.repo_full_name}.git"
    result = await run_analysis(
        git_cloner(url, sample.commit_sha),
        sample.commit_sha,
        PrintReporter(),
        client,
        analysis_id="eval-stage2",
        repo_full_name=sample.repo_full_name,
    )
    out = ROOT / "docs" / "evals" / f"stage2-{datetime.now(UTC):%Y-%m-%d}.md"
    out.write_text(
        "\n".join(render(result, recorder, label)) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"{len(result.topics)}개 주제, 비용 ${recorder.total_usd():.4f} → {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="2단계 분석 실제 LLM 평가")
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--reasoning-effort", default=None, help="low, medium, high (비우면 모델 기본값)"
    )
    # 주제 생성 실패 원인(시간 초과, 검증 실패)을 보려고 정보 로그를 켭니다. 본문은 로그에 남지 않습니다.
    logging.basicConfig(level=logging.WARNING, format="  %(levelname)s %(message)s")
    for name in ("gitinsight_worker", "gitinsight_api.llm"):
        logging.getLogger(name).setLevel(logging.INFO)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
