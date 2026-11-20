"""샘플 레포를 실제 LLM으로 2단계까지 분석해 사람이 평가할 기록을 남깁니다 (수동 실행, 실제 API 키 필요, 비용 발생).

사용:
  uv run python scripts/eval_stage2.py
  uv run python scripts/eval_stage2.py --model gemini/gemini-3.6-flash
  uv run python scripts/eval_stage2.py --reasoning-effort ""   # 모델 기본 추론량으로 비교
  uv run python scripts/eval_stage2.py --requester-login <GitHub 로그인> --save-json .data/analysis.json
      # 그 계정 기준 기여 라벨을 붙이고, 결과를 데모 스냅샷으로 넣을 수 있게 저장합니다
      # (uv run python scripts/seed_demo_analysis.py --file .data/analysis.json --user-id <id>)
  uv run python scripts/eval_stage2.py --repo <owner/name> --commit <SHA>
      # 데모 레포가 아닌 레포로 질문 품질을 봅니다 (동의받은 레포만). 샘플과의 비교 단락은 빠지고,
      # --out이 없으면 docs/evals/stage2-<날짜>-<owner>-<name>.md에 씁니다
  uv run python scripts/eval_stage2.py --no-review
      # 검토(#199) 없이 돌립니다. 검토 전후를 비교할 때 씁니다
  uv run python scripts/eval_stage2.py --review-model gemini/<더 큰 모델> --review-effort high
      # 검토만 다른 모델·추론량으로 돌립니다

API 키는 환경 변수로 받습니다. .env에 넣었다면 `uv run --env-file .env python ...`으로 실행합니다.
진행 중 로그에는 오류 종류와 개수만 나오고, 코드와 응답 본문은 나오지 않습니다.

샘플 데이터(fixtures/analysis)의 레포를 같은 고정 커밋으로 내려받아 분석하고,
만든 주제와 호출 수, 비용, 지연, 손으로 만든 샘플과의 비교를 docs/evals/stage2-<날짜>.md에 씁니다.
--requester-login을 주면 그 계정의 GitHub noreply 이메일로, --requester-email을 주면 그 이메일로 커밋한 줄만
본인 기여로 봅니다.
기대 포인트가 실제 코드에 있는 내용인지, 질문이 자연스러운지는 사람이 기록을 보고 판단합니다.
CI에서는 돌리지 않습니다. CI는 FakeLLM으로 같은 흐름을 검사합니다(worker/tests/test_stage2.py).
"""

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from gitinsight_api.llm import (
    InMemoryCostRecorder,
    LiteLLMClient,
    LLMClient,
    LLMResult,
    Message,
)
from gitinsight_api.settings import Settings
from gitinsight_contracts.analysis import AnalysisResult, Topic
from gitinsight_contracts.api import AnalysisState
from gitinsight_contracts.common import LineSpan
from gitinsight_contracts.fixtures import load_analysis
from gitinsight_worker.contribution import Requester
from gitinsight_worker.git import git_cloner
from gitinsight_worker.pipeline import run_analysis
from gitinsight_worker.review import (
    REVIEW_PURPOSE,
    REVIEW_REASONING_EFFORT,
    PurposeRouter,
    ReviewVerdict,
    TopicReview,
)

ROOT = Path(__file__).resolve().parents[1]


class PrintReporter:
    async def report(self, state: AnalysisState) -> None:
        print(f"  {state.status.value} {state.progress:.2f}", file=sys.stderr)


class ReviewRecorder:
    """검토 LLM의 응답을 모아 기록에 씁니다(#199). 사람이 검토의 지적이 맞는지 코드와 대조합니다."""

    def __init__(self, client: LLMClient) -> None:
        self._client = client
        self.reviews: list[TopicReview] = []

    async def complete_structured[T: BaseModel](
        self,
        schema: type[T],
        messages: list[Message],
        *,
        purpose: str,
        timeout_s: float,
        model: str | None = None,
    ) -> LLMResult[T]:
        result = await self._client.complete_structured(
            schema, messages, purpose=purpose, timeout_s=timeout_s, model=model
        )
        if isinstance(result.value, TopicReview):
            self.reviews.append(result.value)
        return result


class ReviewLog(logging.Handler):
    """워커의 검토 로그(개수만 있음)를 모읍니다. 서버 근거 대조에서 걸린 요점 수를 여기서 얻습니다."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if message.startswith(("topic_review", "topic_rejected")):
            self.messages.append(message)


def review_lines(recorder: ReviewRecorder | None, log: ReviewLog) -> list[str]:
    """검토 결과입니다. 검토를 끄고 돌렸으면 그렇다고만 씁니다."""
    if recorder is None:
        return ["", "## 검토 (#199)", "", "- 검토 없이 돌렸습니다 (--no-review)"]
    items = [i for r in recorder.reviews for i in r.items]
    fixes = [i for i in items if i.verdict is ReviewVerdict.FIX]
    unverifiable = [i for i in items if i.verdict is ReviewVerdict.UNVERIFIABLE]
    drops = [r.drop_reason for r in recorder.reviews if r.drop]
    unsupported = sum(
        int(part.split("=")[1])
        for m in log.messages
        for part in m.split()
        if part.startswith("unsupported=")
    )
    lines = [
        "",
        "## 검토 (#199)",
        "",
        f"- 검토 호출: {len(recorder.reviews)}회, 고칠 것 {len(fixes)}건, 확인할 수 없음 {len(unverifiable)}건, 주제 빼기 {len(drops)}건",
        f"- 서버 근거 대조에서 걸린 요점: {unsupported}건",
        f"- 다시 만들어도 남아 버린 후보: {sum(m.startswith('topic_rejected') for m in log.messages)}개",
        "",
        "사람이 확인할 것: 고칠 것의 지적이 코드와 맞는가(멀쩡한 요점을 틀렸다고 하지 않았는가),",
        "확인할 수 없음이 정말 다른 파일의 코드가 필요한 내용인가.",
        "",
        "### 고칠 것",
        *(f"- `{i.field}`: {i.reason}" for i in fixes),
        "",
        "### 확인할 수 없음",
        *(f"- `{i.field}`: {i.reason}" for i in unverifiable),
        "",
        "### 주제 빼기",
        *(f"- {reason}" for reason in drops),
    ]
    return lines


def labels(result: AnalysisResult) -> dict[str, int]:
    counts: dict[str, int] = {}
    for topic in result.topics:
        counts[topic.contribution_label.value] = counts.get(topic.contribution_label.value, 0) + 1
    return counts


def render(result: AnalysisResult, recorder: InMemoryCostRecorder, model: str) -> list[str]:
    records = recorder.records
    lines = [
        f"# 2단계 분석 평가 ({datetime.now(UTC):%Y-%m-%d})",
        "",
        f"- 레포: `{result.repo_full_name}` @ `{result.commit_sha}`",
        f"- 모델: `{model}`",
        f"- 주제 수: {len(result.topics)}",
        f"- 기여 라벨: {', '.join(f'{k} {v}개' for k, v in labels(result).items())}",
        f"- LLM 호출: {len(records)}회 (검증 실패 재요청 포함), 이 중 스키마 재시도 {sum(r.attempts - 1 for r in records)}회",
        f"- 비용: ${recorder.total_usd():.4f}",
        f"- 호출당 최대 지연: {max((r.latency_ms for r in records), default=0):.0f} ms",
        "",
        "사람이 확인할 것: 기대 포인트가 코드에 실제로 있는가, 질문이 존댓말 한 문장이고 자연스러운가,",
        "힌트가 답을 알려 주지 않는가, 관련 심볼이 꼬리질문과 맞는가,",
        "틀린 말이 정말 틀렸는가(absent_call은 주제 코드가 그 함수를 직접 부르지 않는가), 정답만 들어도 정정으로 들리는가.",
        f"- 틀린 말: {sum(t.misstatement is not None for t in result.topics)}/{len(result.topics)}개 주제",
    ]
    for topic in result.topics:
        lines += [
            "",
            f"## {topic.title} (`{topic.symbol.symbol_id}`, 난이도 {topic.difficulty}, "
            f"기여 {topic.contribution_label.value})",
            "",
            f"- 요약: {topic.summary}",
            f"- 질문: {topic.base_question}",
            f"- 취지: {topic.question_intent}",
            f"- 힌트: {topic.hint}",
            f"- 관련 심볼: {', '.join(s.symbol_id for s in topic.related_symbols) or '없음'}",
            "- 기대 포인트:",
            *(
                f"  - {p.text} → {p.probe_question} [{focus_text(topic, p.focus)}]"
                for p in topic.expected_points
            ),
            "- 꼬리질문:",
            *(f"  - {f.question} ({f.symbol_id or '-'})" for f in topic.prepared_followups),
            *misstatement_lines(topic),
        ]
    return lines


def focus_text(topic: Topic, focus: LineSpan | None) -> str:
    """요점의 강조 범위입니다 (#156). 사람이 코드와 대조해 요점과 맞는지 봅니다."""
    if focus is None:
        return "강조: 주제 전체"
    return f"강조: {focus.start_line}–{focus.end_line}줄 (주제 {topic.symbol.start_line}–{topic.symbol.end_line}줄)"


def misstatement_lines(topic: Topic) -> list[str]:
    """면접관이 일부러 하는 틀린 말입니다 (#142). 사람이 코드와 대조해 정말 틀린 말인지 봅니다."""
    m = topic.misstatement
    if m is None:
        return ["- 틀린 말: 없음 (검증 실패 또는 만들지 않음)"]
    target = m.absent_symbol.symbol_id if m.absent_symbol is not None else m.point_id
    return [
        f"- 틀린 말 ({m.kind.value}, `{target}`):",
        f"  - 말: {m.statement}",
        f"  - 정답: {m.correction}",
    ]


def compare(result: AnalysisResult, sample: AnalysisResult) -> list[str]:
    """손으로 만든 샘플과 주제 심볼과 줄 범위를 비교합니다."""
    ours = {t.symbol.symbol_id: t.symbol for t in result.topics}
    theirs = {t.symbol.symbol_id: t.symbol for t in sample.topics}
    shared = sorted(ours.keys() & theirs.keys())
    lines = [
        "",
        "## 손으로 만든 샘플과 비교",
        "",
        f"- 심볼 수: 워커 {result.symbol_count}, 샘플 {sample.symbol_count}",
        f"- 같은 심볼을 고른 주제: {len(shared)}개 (워커 {len(ours)}개, 샘플 {len(theirs)}개)",
    ]
    for symbol_id in shared:
        a, b = ours[symbol_id], theirs[symbol_id]
        note = (
            ""
            if (a.start_line, a.end_line) == (b.start_line, b.end_line)
            else (f" (샘플 {b.start_line}-{b.end_line}, 다름)")
        )
        lines.append(f"  - `{symbol_id}` 줄 {a.start_line}-{a.end_line}{note}")
    only_ours = sorted(ours.keys() - theirs.keys())
    only_theirs = sorted(theirs.keys() - ours.keys())
    lines += [
        f"- 워커만 고른 심볼: {', '.join(f'`{s}`' for s in only_ours) or '없음'}",
        f"- 샘플만 고른 심볼: {', '.join(f'`{s}`' for s in only_theirs) or '없음'}",
    ]
    return lines


async def run(args: argparse.Namespace) -> int:
    settings = Settings()
    model = args.model or settings.llm_analysis_model
    recorder = InMemoryCostRecorder()
    # 인자를 주지 않으면 설정(LLM_ANALYSIS_REASONING_EFFORT, 기본 low)을, 빈 문자열이면 모델 기본값을 씁니다.
    effort = (
        settings.llm_analysis_reasoning_effort
        if args.reasoning_effort is None
        else args.reasoning_effort.strip() or None
    )
    topic_client = LiteLLMClient(model, cost_recorder=recorder, reasoning_effort=effort)
    label = f"{model} (reasoning_effort={effort or '모델 기본값'})"
    reviews: ReviewRecorder | None = None
    client: LLMClient = topic_client
    if not args.no_review:
        review_model = args.review_model or model
        review_effort = args.review_effort or REVIEW_REASONING_EFFORT
        reviews = ReviewRecorder(
            LiteLLMClient(review_model, cost_recorder=recorder, reasoning_effort=review_effort)
        )
        client = PurposeRouter(topic_client, {REVIEW_PURPOSE: reviews})
        label += f", 검토 {review_model} (reasoning_effort={review_effort})"
    review_log = ReviewLog()
    logging.getLogger("gitinsight_worker").addHandler(review_log)
    sample = load_analysis()
    # 다른 레포를 주면 그 레포를 분석합니다. 손으로 만든 샘플과의 비교는 샘플 레포일 때만 씁니다.
    repo = args.repo or sample.repo_full_name
    commit = args.commit or (sample.commit_sha if repo == sample.repo_full_name else None)
    if commit is None:
        print("--repo로 다른 레포를 줄 때는 --commit(40자리 SHA)도 줘야 합니다", file=sys.stderr)
        return 2
    url = f"https://github.com/{repo}.git"
    requester = (
        Requester.of(
            emails=args.requester_email, login=args.requester_login, user_id=args.requester_id
        )
        if args.requester_login or args.requester_email
        else None
    )
    result = await run_analysis(
        git_cloner(url, commit),
        commit,
        PrintReporter(),
        client,
        analysis_id=args.analysis_id,
        repo_full_name=repo,
        requester=requester,
        review=not args.no_review,
    )
    # 다른 레포의 기록이 같은 날 데모 레포 기록을 덮어쓰지 않게 파일 이름에 레포 이름을 넣습니다.
    suffix = "" if repo == sample.repo_full_name else "-" + repo.replace("/", "-")
    out = args.out or ROOT / "docs" / "evals" / f"stage2-{datetime.now(UTC):%Y-%m-%d}{suffix}.md"
    lines = render(result, recorder, label)
    lines += review_lines(reviews, review_log)
    if repo == sample.repo_full_name and commit == sample.commit_sha:
        lines += compare(result, sample)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(result.topics)}개 주제, 비용 ${recorder.total_usd():.4f} → {out}")
    if args.save_json:
        args.save_json.parent.mkdir(parents=True, exist_ok=True)
        args.save_json.write_text(
            result.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        print(f"결과 JSON → {args.save_json}")
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    parser = argparse.ArgumentParser(description="2단계 분석 실제 LLM 평가")
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--reasoning-effort",
        default=None,
        help="low, medium, high. 주지 않으면 설정값(기본 low), 빈 문자열이면 모델 기본값",
    )
    parser.add_argument(
        "--requester-login",
        default=None,
        help="기여 라벨을 이 GitHub 계정 기준으로 붙입니다(noreply 이메일로 맞춤). 없으면 모두 확인 불가",
    )
    parser.add_argument(
        "--requester-id", type=int, default=None, help="GitHub 사용자 숫자 id (noreply 새 형식)"
    )
    parser.add_argument(
        "--requester-email",
        action="append",
        default=[],
        help="본인 기여로 볼 커밋 작성자 이메일 (여러 번 줄 수 있음). "
        "로그인 후에는 GitHub 인증 이메일이 이 역할을 합니다. 결과와 기록에는 남지 않습니다",
    )
    parser.add_argument(
        "--save-json", type=Path, default=None, help="결과 AnalysisResult를 저장할 경로"
    )
    parser.add_argument(
        "--analysis-id", default="eval-stage2", help="결과에 넣을 analysis_id (스냅샷용)"
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="분석할 GitHub 공개 레포 owner/name (기본: 샘플 데이터의 데모 레포). 동의받은 레포만 씁니다",
    )
    parser.add_argument(
        "--commit", default=None, help="--repo의 고정 커밋 SHA (40자리). 다른 레포면 필수"
    )
    parser.add_argument(
        "--no-review", action="store_true", help="2단계 검토(#199) 없이 돌립니다 (검토 전후 비교용)"
    )
    parser.add_argument(
        "--review-model", default=None, help="검토에 쓸 모델 (기본: 출제와 같은 모델)"
    )
    parser.add_argument(
        "--review-effort",
        default=None,
        help=f"검토의 reasoning_effort (기본: {REVIEW_REASONING_EFFORT})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="평가 기록 경로 (기본: docs/evals/stage2-<날짜>.md, 같은 날 기록은 덮어씁니다)",
    )
    # 주제 생성 실패 원인(시간 초과, 검증 실패)을 보려고 정보 로그를 켭니다. 본문은 로그에 남지 않습니다.
    logging.basicConfig(level=logging.WARNING, format="  %(levelname)s %(message)s")
    for name in ("gitinsight_worker", "gitinsight_api.llm"):
        logging.getLogger(name).setLevel(logging.INFO)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
