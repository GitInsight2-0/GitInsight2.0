"""샘플 데이터(fixtures/) 형식과 불러오기 도구.

사용: uv run python -m gitinsight_contracts.fixtures show [분석 파일 이름]
"""

import sys
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from gitinsight_contracts.analysis import CONTRIBUTION_LABEL_TEXT, AnalysisResult
from gitinsight_contracts.base import Contract
from gitinsight_contracts.interview import Action
from gitinsight_contracts.llm import Judgment

# contracts/src/gitinsight_contracts/fixtures.py 기준으로 저장소 루트의 fixtures/를 찾습니다.
FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures"
DEFAULT_ANALYSIS = "gitinsight_agent"


class GoldenTurn(Contract):
    """골든 대화의 한 턴입니다. FakeLLM이 돌려줄 판단과 기대하는 서버 행동을 함께 적습니다."""

    transcript: str
    # ok: judgment를 그대로 돌려줌, timeout: 판단 시간 초과, invalid: 스키마는 맞지만 검증 실패할 판단
    llm_behavior: Literal["ok", "timeout", "invalid"] = "ok"
    judgment: Judgment | None = None
    expect_action: Action
    expect_topic_id: str | None = None


class GoldenConversation(Contract):
    name: str
    description: str
    analysis: str = DEFAULT_ANALYSIS
    duration_min: Annotated[int, Field(ge=3, le=15)] = 10
    # 각 턴 사이에 흐른 것으로 칠 시간(초)입니다.
    seconds_per_turn: Annotated[int, Field(ge=0)] = 20
    turns: list[GoldenTurn]


def load_analysis(name: str = DEFAULT_ANALYSIS) -> AnalysisResult:
    path = FIXTURES_DIR / "analysis" / f"{name}.json"
    return AnalysisResult.model_validate_json(path.read_text(encoding="utf-8"))


def load_conversations() -> list[GoldenConversation]:
    paths = sorted((FIXTURES_DIR / "conversations").glob("*.json"))
    return [GoldenConversation.model_validate_json(p.read_text(encoding="utf-8")) for p in paths]


def show(name: str = DEFAULT_ANALYSIS) -> str:
    result = load_analysis(name)
    lines = [
        f"{result.repo_full_name} @ {result.commit_sha[:12]} (심볼 {result.symbol_count}개)",
    ]
    for topic in result.topics:
        symbol = topic.symbol
        label = CONTRIBUTION_LABEL_TEXT[topic.contribution_label]
        lines.append(
            f"- {topic.topic_id} [{label}, 난이도 {topic.difficulty}] "
            f"{symbol.path}:{symbol.start_line}-{symbol.end_line} {symbol.name}"
        )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if not argv or argv[0] != "show":
        print(
            "사용법: python -m gitinsight_contracts.fixtures show [분석 파일 이름]", file=sys.stderr
        )
        return 2
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    print(show(*argv[1:2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
