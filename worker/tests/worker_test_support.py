"""워커 테스트 공용 도구. pytest 설정의 pythonpath로 테스트 모듈에서 import합니다."""

import os
import subprocess
from pathlib import Path

from gitinsight_contracts.api import AnalysisState
from gitinsight_worker.stage2 import FollowupDraft, MisstatementDraft, PointDraft, TopicMaterial


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
    misstatement: MisstatementDraft | None = None,
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
        misstatement=misstatement or inverted_draft(),
    )


def inverted_draft(point_index: int | None = 1) -> MisstatementDraft:
    """요점 하나를 반대로 말하는 틀린 말입니다. absent_call 주제에서는 함수 이름이 없어 검증에 실패합니다."""
    return MisstatementDraft(
        statement="이 코드는 계산 없이 요청을 그대로 돌려주죠?",
        correction="방금 말씀드린 것과 달리 이 코드는 값을 차례로 계산한 뒤 첫 값을 돌려줍니다.",
        point_index=point_index,
    )


def absent_draft(name: str) -> MisstatementDraft:
    """주제 코드가 name 함수를 직접 부른다고 말하는 틀린 말입니다."""
    return MisstatementDraft(
        statement=f"여기서 직접 {name}를 호출해서 값을 준비하죠?",
        correction=f"방금 말씀드린 것과 달리 이 코드는 {name}를 직접 호출하지 않습니다.",
        point_index=None,
    )


def make_origin(path: Path, files: dict[str, str] | None = None) -> str:
    """REPO_FILES로 커밋 하나짜리 로컬 git 레포를 만들고 커밋 SHA를 돌려줍니다. 작성자는 t@example.com입니다."""
    path.mkdir()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    write_repo(path, files)
    for args in (["init", "--quiet"], ["add", "-A"], ["commit", "--quiet", "-m", "init"]):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, env=env)
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    )
    return out.stdout.strip()
