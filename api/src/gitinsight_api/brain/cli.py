"""터미널 텍스트 면접.

사용:
  uv run python -m gitinsight_api.brain.cli                 # LLM 없이 미리 만든 질문만
  uv run python -m gitinsight_api.brain.cli --real-llm      # 실제 판단 LLM (OPENAI_API_KEY 필요)
  uv run python -m gitinsight_api.brain.cli --real-llm --jd jd.txt --duration 10
답변 입력 중 /quit 를 입력하면 끝납니다.
"""

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import uuid4

from gitinsight_api.brain.factory import (
    build_fixture_brain,
    build_judge_llm,
    build_report_llm,
    build_report_service,
)
from gitinsight_api.llm import InMemoryCostRecorder
from gitinsight_api.settings import Settings
from gitinsight_contracts.analysis import CONTRIBUTION_LABEL_TEXT
from gitinsight_contracts.fixtures import load_analysis
from gitinsight_contracts.interview import (
    BrainReply,
    InterviewMode,
    SessionCreate,
    TurnRequest,
    TurnSource,
)
from gitinsight_contracts.llm import COMPETENCY_TEXT
from gitinsight_contracts.report import Report


def render(reply: BrainReply) -> str:
    lines = [f"\n[{reply.action.value}] {reply.utterance}"]
    if reply.highlight is not None:
        h = reply.highlight
        lines.append(f"  코드: {h.path}:{h.start_line}-{h.end_line} ({h.name})")
    lines.append(f"  주제: {reply.topic_id}, 깊이 {reply.depth}, 남은 시간 {reply.remaining_sec}초")
    return "\n".join(lines)


def render_report(report: Report) -> str:
    lines = [f"\n=== 리포트 ({report.kind.value}) ==="]
    for score in report.competencies:
        lines.append(f"- {COMPETENCY_TEXT[score.name]}: {score.level}/4")
        lines.extend(f'    근거: "{e.quote}"' for e in score.evidence)
    for topic in report.topics:
        lines.append(
            f"- 주제 {topic.topic_id}: 다룸 {len(topic.covered)}개, 빠짐 {len(topic.missing)}개"
        )
        lines.extend(f"    {link.url}" for link in topic.code_links)
    for title, items in (
        ("강점", report.strengths),
        ("보완점", report.improvements),
        ("다음 주제", report.next_topics),
    ):
        if items:
            lines.append(f"- {title}: " + " / ".join(items))
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> int:
    settings = Settings()
    recorder = InMemoryCostRecorder()
    llm = build_judge_llm(settings, recorder) if args.real_llm else None
    brain = build_fixture_brain(
        settings, llm=llm, fixture=args.fixture, force_prepared=args.force_prepared
    )
    analysis = load_analysis(args.fixture or settings.fixture_analysis)
    print(f"레포: {analysis.repo_full_name} @ {analysis.commit_sha[:12]}")
    for topic in analysis.topics:
        print(f"  - {topic.title} [{CONTRIBUTION_LABEL_TEXT[topic.contribution_label]}]")
    mode = "실제 LLM" if args.real_llm and not args.force_prepared else "미리 만든 질문만"
    print(f"판단: {mode}")

    jd_text: str | None = args.jd_text
    session_id, reply = await brain.start(
        SessionCreate(
            analysis_id=analysis.analysis_id,
            jd_text=jd_text,
            mode=InterviewMode.TEXT,
            duration_min=args.duration,
        )
    )
    print(render(reply))
    while not reply.done:
        try:
            answer = await asyncio.to_thread(input, "\n답변> ")
        except EOFError:
            break
        if answer.strip() == "/quit":
            break
        reply = await brain.handle_turn(
            session_id,
            TurnRequest(turn_id=uuid4(), transcript=answer, source=TurnSource.TEXT),
        )
        print(render(reply))

    turns = await brain.repository.list_turns(session_id)
    fallbacks = [t.fallback_reason.value for t in turns if t.fallback_reason is not None]
    print(f"\n턴 {len(turns)}개, 대체 진행 {len(fallbacks)}회 {fallbacks}")

    report_llm = build_report_llm(settings, recorder) if args.real_llm else None
    report = await build_report_service(
        settings, brain, analysis, llm=report_llm, force_prepared=args.force_prepared
    ).end_session(session_id)
    print(render_report(report))
    if args.real_llm:
        print(f"LLM 호출 {len(recorder.records)}회, 비용 약 ${recorder.total_usd():.4f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    parser = argparse.ArgumentParser(description="GitInsight 텍스트 면접")
    parser.add_argument("--fixture", default=None, help="fixtures/analysis/<이름>.json")
    parser.add_argument("--real-llm", action="store_true", help="실제 판단 LLM을 씁니다")
    parser.add_argument("--force-prepared", action="store_true", help="LLM 없이 미리 만든 질문만")
    parser.add_argument("--jd", default=None, help="채용공고 텍스트 파일")
    parser.add_argument("--duration", type=int, default=10, help="면접 시간(분)")
    args = parser.parse_args(argv)
    args.jd_text = Path(args.jd).read_text(encoding="utf-8") if args.jd else None
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
