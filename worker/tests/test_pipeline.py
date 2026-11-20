from pathlib import Path

import pytest

from gitinsight_api.brain.analysis_source import FixtureAnalysisSource
from gitinsight_api.brain.repository import InMemorySessionRepository
from gitinsight_api.brain.service import InterviewBrain
from gitinsight_api.llm import LLMTimeoutError
from gitinsight_api.llm.fake import FakeLLM, FakeStep
from gitinsight_contracts.analysis import AnalysisResult, ContributionLabel
from gitinsight_contracts.api import AnalysisStatus
from gitinsight_contracts.interview import InterviewMode, SessionCreate
from gitinsight_worker.contribution import Requester
from gitinsight_worker.git import git_cloner
from gitinsight_worker.pipeline import run_analysis
from gitinsight_worker.stage1 import NoSupportedCodeError, analyze_tree
from gitinsight_worker.stage2 import Stage2Error

from worker_test_support import Recorder, answer, make_origin, material


@pytest.fixture
def origin(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "origin"
    return path, make_origin(path)


async def analyze(
    origin: tuple[Path, str],
    work: Path,
    llm: FakeLLM,
    recorder: Recorder,
    requester: Requester | None = None,
):
    path, sha = origin
    work.mkdir()
    return await run_analysis(
        git_cloner(str(path), sha, allowed_protocols="file"),
        sha,
        recorder,
        llm,
        analysis_id="analysis-1",
        repo_full_name="owner/repo",
        requester=requester,
        work_dir=work,
    )


async def test_run_analysis_builds_result_and_removes_checkout(
    origin: tuple[Path, str], tmp_path: Path
) -> None:
    llm = FakeLLM(responder=answer(material()))
    recorder = Recorder()

    result = await analyze(origin, tmp_path / "work", llm, recorder)

    # 계약 검증을 다시 통과하고, 모든 심볼이 1단계 목록에 있습니다.
    AnalysisResult.model_validate(result.model_dump())
    stage1_ids = {s.symbol_id for s in analyze_tree(origin[0], origin[1]).symbols}
    for topic in result.topics:
        assert {topic.symbol.symbol_id, *(s.symbol_id for s in topic.related_symbols)} <= stage1_ids
    assert len(result.topics) == 3
    assert result.commit_sha == origin[1]
    statuses = [(s.status, s.progress) for s in recorder.states]
    assert statuses[:3] == [
        (AnalysisStatus.STAGE1, 0.0),
        (AnalysisStatus.STAGE1, 0.2),
        (AnalysisStatus.STAGE2, 0.5),
    ]
    assert all(s is AnalysisStatus.STAGE2 for s, _ in statuses[3:])
    assert all(p <= 0.95 for _, p in statuses)
    # done은 결과를 저장한 쪽이 보고합니다.
    assert AnalysisStatus.DONE not in {s for s, _ in statuses}
    assert list((tmp_path / "work").iterdir()) == []


async def test_stage2_failure_reports_failed_and_removes_checkout(
    origin: tuple[Path, str], tmp_path: Path
) -> None:
    llm = FakeLLM(responder=lambda *_: FakeStep(error=LLMTimeoutError("느림")))
    recorder = Recorder()

    with pytest.raises(Stage2Error):
        await analyze(origin, tmp_path / "work", llm, recorder)

    assert recorder.states[-1].status is AnalysisStatus.FAILED
    assert recorder.states[-1].error == "Stage2Error"
    # 실패 직전 진행률(2단계 시작)을 유지해 어느 단계에서 실패했는지 알 수 있습니다.
    assert recorder.states[-1].progress == 0.5
    assert list((tmp_path / "work").iterdir()) == []


async def test_interview_brain_can_start_with_the_result(
    origin: tuple[Path, str], tmp_path: Path
) -> None:
    llm = FakeLLM(responder=answer(material()))
    result = await analyze(origin, tmp_path / "work", llm, Recorder())
    brain = InterviewBrain(
        analyses=FixtureAnalysisSource(result),
        repository=InMemorySessionRepository(),
        llm=FakeLLM(),
    )

    _, first = await brain.start(
        SessionCreate(analysis_id=result.analysis_id, mode=InterviewMode.TEXT)
    )

    topic = result.topic(first.topic_id or "")
    assert topic.base_question in first.utterance
    assert first.highlight == topic.symbol


async def test_contribution_from_stage1_is_attached_to_topics(
    origin: tuple[Path, str], tmp_path: Path
) -> None:
    llm = FakeLLM(responder=answer(material()))
    mine = Requester.of(emails=["t@example.com"])

    result = await analyze(origin, tmp_path / "work", llm, Recorder(), requester=mine)

    # 라벨과 커밋은 LLM이 아니라 1단계 blame 결과에서 옵니다.
    assert {t.contribution_label for t in result.topics} == {ContributionLabel.VERIFIED}
    assert {tuple(t.commits) for t in result.topics} == {(origin[1],)}


async def test_without_requester_label_is_unverified_but_commits_remain(
    origin: tuple[Path, str], tmp_path: Path
) -> None:
    llm = FakeLLM(responder=answer(material()))

    result = await analyze(origin, tmp_path / "work", llm, Recorder())

    assert {t.contribution_label for t in result.topics} == {ContributionLabel.UNVERIFIED}
    assert {tuple(t.commits) for t in result.topics} == {(origin[1],)}


# ---- 지원하지 않는 언어 (#70) ----

NO_SUPPORTED_CODE_REPOS = {
    # 지원하지 않는 언어만 있는 레포
    "go": {
        "main.go": "package main\n\nfunc main() {\n\tprintln(1)\n}\n",
        "go.mod": "module demo\n",
    },
    # TypeScript 파일은 있지만 타입 선언뿐인 레포
    "typescript-without-definitions": {
        "src/types.ts": "export interface Item {\n  id: string;\n}\n\nexport type Id = string;\n",
        "package.json": '{"name": "demo"}\n',
    },
    # Python 파일은 있지만 함수·클래스가 없는 레포
    "python-without-definitions": {
        "settings.py": "DEBUG = True\nNAME = 'demo'\n",
        "README.md": "# demo\n",
    },
}


@pytest.mark.parametrize(
    "files", NO_SUPPORTED_CODE_REPOS.values(), ids=NO_SUPPORTED_CODE_REPOS.keys()
)
async def test_repo_without_supported_code_fails_before_llm(
    files: dict[str, str], tmp_path: Path
) -> None:
    path = tmp_path / "origin"
    origin = (path, make_origin(path, files))
    llm = FakeLLM(responder=answer(material()))
    recorder = Recorder()

    with pytest.raises(NoSupportedCodeError):
        await analyze(origin, tmp_path / "work", llm, recorder)

    # LLM을 한 번도 부르지 않고, 화면이 고를 수 있게 예외 이름을 실패 사유로 보고합니다.
    assert llm.calls == []
    last = recorder.states[-1]
    assert (last.status, last.error) == (AnalysisStatus.FAILED, "NoSupportedCodeError")
    assert AnalysisStatus.STAGE2 not in {s.status for s in recorder.states}
    # 내려받은 레포는 지웁니다.
    assert list((tmp_path / "work").iterdir()) == []


# ---- TypeScript·JavaScript 레포 (#72) ----


def _script_body(prefix: str, n: int = 8) -> str:
    return "".join(f"  const {prefix}{i} = {i};\n" for i in range(n))


SCRIPT_REPO = {
    "src/service.ts": (
        "export class Service {\n"
        "  run(): number {\n" + _script_body("a") + "    return a0;\n  }\n"
        "}\n"
    ),
    "src/handler.tsx": (
        "export const handle = (request: Request) => {\n" + _script_body("c") + "  return c0;\n};\n"
    ),
    "lib/util.js": "function helper() {\n" + _script_body("d") + "  return d0;\n}\n",
}


async def test_typescript_and_javascript_repo_produces_topics(tmp_path: Path) -> None:
    path = tmp_path / "origin"
    origin = (path, make_origin(path, SCRIPT_REPO))
    llm = FakeLLM(responder=answer(material()))

    result = await analyze(origin, tmp_path / "work", llm, Recorder())

    AnalysisResult.model_validate(result.model_dump())
    lines = {rel: text.splitlines() for rel, text in SCRIPT_REPO.items()}
    located = {
        t.symbol.symbol_id: lines[t.symbol.path][t.symbol.start_line - 1 : t.symbol.end_line]
        for t in result.topics
    }
    # 줄 범위가 실제 코드의 정의 첫 줄과 닫는 줄을 가리킵니다.
    assert {symbol_id: (code[0], code[-1]) for symbol_id, code in located.items()} == {
        "src/service.ts::Service": ("export class Service {", "}"),
        "src/handler.tsx::handle": ("export const handle = (request: Request) => {", "};"),
        "lib/util.js::helper": ("function helper() {", "}"),
    }
