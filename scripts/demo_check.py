"""데모 점검: 현재 설정으로 샘플 레포 면접을 자동으로 끝까지 진행하고 리포트까지 만들어 봅니다.

사용:
  uv run python scripts/demo_check.py                 # LLM 없이 (미리 만든 질문, 판단 표 리포트)
  uv run python scripts/demo_check.py --block-llm     # 판단 LLM이 오류만 내는 상황을 흉내 냄
  uv run python scripts/demo_check.py --real-llm      # 실제 LLM (API 키 필요, 비용 발생)
  $env:LLM_PROVIDER="gemini"; uv run python scripts/demo_check.py --real-llm   # 제공사 전환 확인

통과 조건: 마무리(WRAP_UP)까지 가고 리포트가 만들어지며, 턴 처리 서버 시간이 기준 이하입니다.
답변은 골든 대화의 자막을 돌려 씁니다. 실제 지원자 답변의 품질을 재는 도구가 아닙니다.
"""

import argparse
import asyncio
import sys
import time
from itertools import cycle
from uuid import uuid4

from gitinsight_api.brain.factory import (
    build_fixture_brain,
    build_judge_llm,
    build_report_llm,
    build_report_service,
)
from gitinsight_api.llm import InMemoryCostRecorder, LLMClient, LLMUnavailableError
from gitinsight_api.llm.fake import FakeLLM, FakeStep
from gitinsight_api.settings import Settings
from gitinsight_contracts.fixtures import load_analysis, load_conversations
from gitinsight_contracts.interview import InterviewMode, SessionCreate, TurnRequest, TurnSource

MAX_TURNS = 60


def blocked_llm() -> LLMClient:
    return FakeLLM(responder=lambda *_: FakeStep(error=LLMUnavailableError("blocked")))


async def run(args: argparse.Namespace) -> int:
    settings = Settings()
    recorder = InMemoryCostRecorder()
    judge_llm: LLMClient | None
    report_llm: LLMClient | None
    if args.block_llm:
        judge_llm = report_llm = blocked_llm()
        mode = "판단 LLM 차단 흉내"
    elif args.real_llm and not settings.brain_force_prepared:
        judge_llm = build_judge_llm(settings, recorder)
        report_llm = build_report_llm(settings, recorder)
        mode = f"실제 LLM ({settings.llm_provider}: {settings.llm_judge_model})"
    else:
        judge_llm = report_llm = None
        mode = "미리 만든 질문만"
    print(f"모드: {mode}")

    analysis = load_analysis(settings.fixture_analysis)
    brain = build_fixture_brain(settings, llm=judge_llm)
    answers = cycle([t.transcript for c in load_conversations() for t in c.turns])

    started = time.perf_counter()
    session_id, reply = await brain.start(
        SessionCreate(analysis_id=analysis.analysis_id, mode=InterviewMode.TEXT, duration_min=10)
    )
    for _ in range(MAX_TURNS):
        if reply.done:
            break
        reply = await brain.handle_turn(
            session_id,
            TurnRequest(turn_id=uuid4(), transcript=next(answers), source=TurnSource.TEXT),
        )
    report = await build_report_service(settings, brain, analysis, llm=report_llm).end_session(
        session_id
    )
    total_s = time.perf_counter() - started

    turns = await brain.repository.list_turns(session_id)
    server_ms = [t.server_ms for t in turns if t.server_ms is not None]
    fallbacks: dict[str, int] = {}
    for t in turns:
        key = t.fallback_reason.value if t.fallback_reason else "none"
        fallbacks[key] = fallbacks.get(key, 0) + 1
    topics = len({t.topic_id for t in turns})
    worst = max(server_ms) if server_ms else 0.0

    checks = {
        "마무리(WRAP_UP)까지 진행": reply.done,
        "리포트 생성": report is not None,
        f"턴 서버 시간 최대 {args.max_turn_ms:.0f}ms 이하": worst <= args.max_turn_ms,
    }
    print(f"턴 {len(turns)}개, 주제 {topics}개, 대체 사유 {fallbacks}")
    print(f"턴 서버 시간 최대 {worst:.0f}ms, 전체 {total_s:.1f}초")
    print(f"리포트 종류 {report.kind.value}, 역량 점수 {len(report.competencies)}개")
    if recorder.records:
        print(f"LLM 호출 {len(recorder.records)}회, 비용 약 ${recorder.total_usd():.4f}")
    for name, ok in checks.items():
        print(f"[{'통과' if ok else '실패'}] {name}")
    return 0 if all(checks.values()) else 1


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    parser = argparse.ArgumentParser(description="데모 점검")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--real-llm", action="store_true")
    group.add_argument("--block-llm", action="store_true")
    parser.add_argument(
        "--max-turn-ms",
        type=float,
        default=3000,
        help="턴 하나의 서버 처리 시간 상한(ms). 판단 제한 시간 2.5초에 여유를 더한 값입니다.",
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
