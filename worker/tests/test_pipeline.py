import os
import subprocess
from pathlib import Path

import pytest

from gitinsight_api.brain.analysis_source import FixtureAnalysisSource
from gitinsight_api.brain.repository import InMemorySessionRepository
from gitinsight_api.brain.service import InterviewBrain
from gitinsight_api.llm import LLMTimeoutError
from gitinsight_api.llm.fake import FakeLLM, FakeStep
from gitinsight_contracts.analysis import AnalysisResult
from gitinsight_contracts.api import AnalysisStatus
from gitinsight_contracts.interview import InterviewMode, SessionCreate
from gitinsight_worker.git import git_cloner
from gitinsight_worker.pipeline import run_analysis
from gitinsight_worker.stage1 import analyze_tree
from gitinsight_worker.stage2 import Stage2Error

from worker_test_support import Recorder, material, write_repo


def make_origin(path: Path) -> str:
    path.mkdir()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    write_repo(path)
    for args in (["init", "--quiet"], ["add", "-A"], ["commit", "--quiet", "-m", "init"]):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, env=env)
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


@pytest.fixture
def origin(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "origin"
    return path, make_origin(path)


async def analyze(origin: tuple[Path, str], work: Path, llm: FakeLLM, recorder: Recorder):
    path, sha = origin
    work.mkdir()
    return await run_analysis(
        git_cloner(str(path), sha, allowed_protocols="file"),
        sha,
        recorder,
        llm,
        analysis_id="analysis-1",
        repo_full_name="owner/repo",
        work_dir=work,
    )


async def test_run_analysis_builds_result_and_removes_checkout(
    origin: tuple[Path, str], tmp_path: Path
) -> None:
    llm = FakeLLM(responder=lambda *_: FakeStep(material()))
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
    assert list((tmp_path / "work").iterdir()) == []


async def test_interview_brain_can_start_with_the_result(
    origin: tuple[Path, str], tmp_path: Path
) -> None:
    llm = FakeLLM(responder=lambda *_: FakeStep(material()))
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
