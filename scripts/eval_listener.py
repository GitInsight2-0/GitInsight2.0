"""골든 대화로 AI 청자 테스트(#111)를 실제 LLM으로 돌려 기록합니다 (수동 실행, 실제 API 키 필요, 비용 발생).

사용:
  uv run python scripts/eval_listener.py
  uv run python scripts/eval_listener.py --runs 3 --out docs/evals/listener-memo.md

확인하려는 것은 "좋은 답변일수록 청자 정답률이 오르는가"입니다.
골든 대화마다 그 대화에서 물어본 주제의 문제를 청자가 답변 없이 한 번(대조군), 답변만 읽고 한 번 풉니다.
골든 대화의 기대 판단에서 다룬(covered) 요점의 문제는 맞히고, 다루지 않은 요점의 문제는 못 맞히는지도 함께 봅니다.
문제는 주제마다 한 번만 만들어 모든 대화가 같은 문제를 풉니다.
CI에서는 돌리지 않습니다. CI는 FakeLLM으로 검사합니다(api/tests/test_listener.py).
"""

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from gitinsight_api.brain.factory import build_report_llm
from gitinsight_api.llm import InMemoryCostRecorder
from gitinsight_api.report.listener import QuizQuestion, generate_quiz, solve_quiz
from gitinsight_api.settings import Settings
from gitinsight_contracts.fixtures import GoldenConversation, load_analysis, load_conversations
from gitinsight_contracts.llm import CoverageStatus

ROOT = Path(__file__).resolve().parents[1]
SEED = "eval-listener"


@dataclass
class Row:
    conversation: str
    run: int
    question: QuizQuestion
    # 골든 대화의 기대 판단에서 이 요점을 다뤘는지입니다.
    covered: bool
    blind: int
    informed: int

    @property
    def blind_ok(self) -> bool:
        return self.blind == self.question.correct_index

    @property
    def informed_ok(self) -> bool:
        return self.informed == self.question.correct_index


def asked_topics(conversation: GoldenConversation) -> list[str]:
    """대화에서 물어본 주제입니다. 마지막에 넘어가기만 한 주제도 들어갑니다 (리포트와 같은 기준)."""
    topics: list[str] = []
    for turn in conversation.turns:
        if turn.expect_topic_id and turn.expect_topic_id not in topics:
            topics.append(turn.expect_topic_id)
    return topics


def golden_covered(conversation: GoldenConversation) -> set[str]:
    return {
        c.point_id
        for turn in conversation.turns
        if turn.judgment is not None and turn.llm_behavior == "ok"
        for c in turn.judgment.coverage
        if c.status is CoverageStatus.COVERED
    }


def mark(picked: int, question: QuizQuestion) -> str:
    if picked == question.correct_index:
        return "O"
    return "?" if picked == question.unknown_index else "X"


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


async def run(args: argparse.Namespace) -> int:
    settings = Settings()
    recorder = InMemoryCostRecorder()
    llm = build_report_llm(settings, recorder)
    quiz_model = settings.llm_report_model
    listener_model = args.listener_model or settings.llm_judge_model
    conversations = load_conversations()

    quizzes: dict[tuple[str, str], list[QuizQuestion]] = {}
    for conversation in conversations:
        analysis = load_analysis(conversation.analysis)
        for topic_id in asked_topics(conversation):
            key = (conversation.analysis, topic_id)
            if key not in quizzes:
                quizzes[key] = await generate_quiz(
                    llm, analysis, [topic_id], seed=SEED, timeout_s=args.timeout, model=quiz_model
                )

    rows: list[Row] = []
    for conversation in conversations:
        quiz = [q for t in asked_topics(conversation) for q in quizzes[(conversation.analysis, t)]]
        covered = golden_covered(conversation)
        transcripts = [t.transcript for t in conversation.turns if t.transcript.strip()]
        for number in range(1, args.runs + 1):
            blind, informed = await asyncio.gather(
                solve_quiz(llm, quiz, None, timeout_s=args.timeout, model=listener_model),
                solve_quiz(llm, quiz, transcripts, timeout_s=args.timeout, model=listener_model),
            )
            rows += [
                Row(conversation.name, number, q, q.point_id in covered, b, i)
                for q, b, i in zip(quiz, blind, informed, strict=True)
            ]

    lines = [
        f"# AI 청자 테스트 평가 {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        "",
        f"- 문제 모델: `{quiz_model}`, 청자 모델: `{listener_model}`, 대화마다 {args.runs}번 반복",
        f"- LLM 호출 {len(recorder.records)}회, 비용 약 ${recorder.total_usd():.4f}",
        "- 다룬 요점은 골든 대화의 기대 판단에서 covered인 요점입니다.",
        '- 표의 O는 정답, X는 오답, ?는 "알 수 없음"을 고른 것입니다.',
        "",
        "## 대화별 결과",
        "",
        "| 대화 | 반복 | 문제 수 | 답변 없이 정답 | 답변 읽고 정답 | 다룬 요점 정답 | 다루지 않은 요점 정답 |",
        "|---|---|---|---|---|---|---|",
    ]
    for conversation in conversations:
        for number in range(1, args.runs + 1):
            mine = [r for r in rows if r.conversation == conversation.name and r.run == number]
            on = [r for r in mine if r.covered]
            off = [r for r in mine if not r.covered]
            lines.append(
                f"| {conversation.name} | {number} | {len(mine)} "
                f"| {sum(r.blind_ok for r in mine)} | {sum(r.informed_ok for r in mine)} "
                f"| {sum(r.informed_ok for r in on)}/{len(on)} "
                f"| {sum(r.informed_ok for r in off)}/{len(off)} |"
            )
    lines += [
        "",
        "## 문제별 결과",
        "",
        "| 대화 | 반복 | 요점 | 다룬 요점 | 답변 없이 | 답변 읽고 |",
        "|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {r.conversation} | {r.run} | {r.question.point_id} | {'예' if r.covered else '아니오'} "
        f"| {mark(r.blind, r.question)} | {mark(r.informed, r.question)} |"
        for r in rows
    ]
    lines += ["", "## 문제", ""]
    for (_, topic_id), quiz in quizzes.items():
        lines.append(f"### {topic_id}")
        lines.append("")
        for q in quiz:
            lines.append(f"- `{q.point_id}` {cell(q.question)}")
            for index, choice in enumerate(q.choices):
                answer = " (정답)" if index == q.correct_index else ""
                lines.append(f"  - {index + 1}. {cell(choice)}{answer}")
        lines.append("")

    out = (
        Path(args.out)
        if args.out
        else ROOT / "docs" / "evals" / f"listener-{datetime.now(UTC):%Y-%m-%d}.md"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines))
    print(f"\n기록: {out}")
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    parser = argparse.ArgumentParser(description="골든 대화 AI 청자 테스트 실제 LLM 평가")
    parser.add_argument("--runs", type=int, default=2, help="대화마다 청자 호출을 반복하는 횟수")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--listener-model", default=None)
    parser.add_argument(
        "--out", default=None, help="기록 경로 (기본 docs/evals/listener-<날짜>.md)"
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
