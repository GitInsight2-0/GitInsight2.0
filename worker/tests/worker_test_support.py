"""워커 테스트 공용 도구. pytest 설정의 pythonpath로 테스트 모듈에서 import합니다."""

from pathlib import Path

from gitinsight_contracts.api import AnalysisState
from gitinsight_worker.stage2 import FollowupDraft, PointDraft, TopicMaterial


class Recorder:
    """보고된 분석 상태를 순서대로 모읍니다."""

    def __init__(self) -> None:
        self.states: list[AnalysisState] = []

    async def report(self, state: AnalysisState) -> None:
        self.states.append(state)


def _body(prefix: str, n: int = 8) -> str:
    return "".join(f"        {prefix}{i} = {i}\n" for i in range(n))


# 주제 후보가 3개(Service, handle, helper) 나오는 작은 레포입니다.
REPO_FILES = {
    "app/service.py": (
        "class Service:\n"
        "    def run(self):\n" + _body("a") + "        return a0\n"
        "\n"
        "    def stop(self):\n" + _body("b") + "        return b0\n"
    ),
    "app/handler.py": "def handle(request):\n"
    + _body("c").replace("        ", "    ")
    + "    return c0\n",
    "app/util.py": "def helper():\n"
    + _body("d", 6).replace("        ", "    ")
    + "    return d0\n",
}


def write_repo(root: Path, files: dict[str, str] | None = None) -> None:
    for rel, text in (files or REPO_FILES).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def material(
    *,
    title: str = "요청 처리 흐름",
    base_question: str = "이 코드가 요청을 어떤 순서로 처리하는지 설명해 주시겠어요?",
    question_intent: str = "처리 단계와 각 단계의 역할을 이해하는지 보고 싶습니다.",
    hint: str = "함수 안의 변수 이름을 위에서부터 따라가 보세요.",
    points: int = 3,
    followups: int = 1,
    related: list[str] | None = None,
    followup_symbol: str | None = None,
) -> TopicMaterial:
    """검증을 통과하는 LLM 출력입니다. 인자로 한 군데씩 바꿔 실패 경우를 만듭니다."""
    return TopicMaterial(
        title=title,
        difficulty=1,
        summary="요청에서 값을 꺼내 차례로 계산한 뒤 첫 값을 돌려준다.",
        base_question=base_question,
        question_intent=question_intent,
        expected_points=[
            PointDraft(text=f"요점 {i}", probe_question=f"요점 {i}은 어떻게 처리되나요?")
            for i in range(1, points + 1)
        ],
        prepared_followups=[
            FollowupDraft(
                question="이 부분을 테스트하려면 어떤 입력을 준비하시겠어요?",
                symbol_id=followup_symbol if i == 0 else None,
            )
            for i in range(followups)
        ],
        hint=hint,
        related_symbol_ids=related or [],
    )
