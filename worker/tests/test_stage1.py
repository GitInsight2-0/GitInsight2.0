import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from gitinsight_contracts.api import AnalysisState, AnalysisStatus
from gitinsight_worker.git import CloneError, Cloner, git_cloner
from gitinsight_worker.stage1 import analyze_tree, iter_repo_files, run_stage1

from worker_test_support import Recorder

SHA = "b" * 40

FILES = {
    "app/main.py": "def main():\n    return 1\n",
    "app/util.py": "class Util:\n    def run(self):\n        pass\n",
    "README.md": "# 제목\n",
    "node_modules/pkg/index.py": "def vendored():\n    pass\n",
    ".venv/lib/x.py": "def venv_fn():\n    pass\n",
}


def write_tree(root: Path, files: dict[str, str] = FILES) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


class FakeClone:
    """레포 대신 파일을 채우고, 어디에 내려받았는지 기록합니다."""

    def __init__(self, files: dict[str, str] = FILES, error: Exception | None = None) -> None:
        self.files = files
        self.error = error
        self.dest: Path | None = None

    def __call__(self, dest: Path) -> None:
        self.dest = dest
        write_tree(dest, self.files)
        if self.error is not None:
            raise self.error


def test_analyze_tree_skips_vendored_directories(tmp_path: Path) -> None:
    write_tree(tmp_path)

    result = analyze_tree(tmp_path, SHA)

    assert {s.symbol_id for s in result.symbols} == {
        "app/main.py::main",
        "app/util.py::Util",
        "app/util.py::Util.run",
    }
    assert result.symbol_count == 3
    assert [(s.language, s.file_count) for s in result.languages] == [
        ("Python", 2),
        ("Markdown", 1),
    ]
    assert {s.commit_sha for s in result.symbols} == {SHA}


def test_symlink_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("def leaked():\n    pass\n", encoding="utf-8")
    repo = tmp_path / "repo"
    write_tree(repo, {"a.py": "def a():\n    pass\n"})
    try:
        (repo / "link.py").symlink_to(outside / "secret.py")
    except OSError:
        pytest.skip("이 환경에서는 심볼릭 링크를 만들 수 없습니다")

    assert iter_repo_files(repo) == ["a.py"]
    assert {s.name for s in analyze_tree(repo, SHA).symbols} == {"a"}


async def test_run_stage1_reports_progress_and_removes_checkout(tmp_path: Path) -> None:
    clone = FakeClone()
    recorder = Recorder()

    result = await run_stage1(clone, SHA, recorder, work_dir=tmp_path)

    assert result.symbol_count == 3
    assert [(s.status, s.progress) for s in recorder.states] == [
        (AnalysisStatus.STAGE1, 0.0),
        (AnalysisStatus.STAGE1, 0.2),
        (AnalysisStatus.STAGE1, 0.5),
    ]
    assert {s.commit_sha for s in recorder.states} == {SHA}
    assert clone.dest is not None and not clone.dest.exists()
    assert list(tmp_path.iterdir()) == []


async def test_clone_failure_reports_failed_without_message_and_removes_checkout(
    tmp_path: Path,
) -> None:
    clone = FakeClone(error=CloneError("git 명령이 실패했습니다"))
    recorder = Recorder()

    with pytest.raises(CloneError, match="git 명령이 실패했습니다"):
        await run_stage1(clone, SHA, recorder, work_dir=tmp_path)

    last = recorder.states[-1]
    assert last.status is AnalysisStatus.FAILED
    # 오류에는 예외 종류만 남기고 메시지(레포 내용이 섞일 수 있음)는 넣지 않습니다.
    assert last.error == "CloneError"
    assert clone.dest is not None and not clone.dest.exists()


async def test_cancellation_is_not_reported_as_failure_and_cleans_up(tmp_path: Path) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    dests: list[Path] = []

    class SlowRecorder(Recorder):
        async def report(self, state: AnalysisState) -> None:
            await super().report(state)
            if state.progress == 0.2:
                started.set()
                await release.wait()

    def clone(dest: Path) -> None:
        dests.append(dest)
        write_tree(dest)

    recorder = SlowRecorder()
    task = asyncio.create_task(run_stage1(clone, SHA, recorder, work_dir=tmp_path))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert AnalysisStatus.FAILED not in {s.status for s in recorder.states}
    assert dests and not dests[0].exists()


def git(*args: str, cwd: Path) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    out = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env
    )
    return out.stdout.strip()


def make_origin(path: Path) -> tuple[str, str]:
    """커밋 두 개짜리 로컬 레포를 만들고 (첫 커밋 SHA, 둘째 커밋 SHA)를 돌려줍니다."""
    path.mkdir()
    git("init", "--quiet", cwd=path)
    write_tree(path, {"a.py": "def first():\n    pass\n"})
    git("add", "-A", cwd=path)
    git("commit", "--quiet", "-m", "first", cwd=path)
    first = git("rev-parse", "HEAD", cwd=path)
    write_tree(path, {"a.py": "def first():\n    pass\n\n\ndef second():\n    pass\n"})
    git("commit", "--quiet", "-am", "second", cwd=path)
    return first, git("rev-parse", "HEAD", cwd=path)


async def test_git_cloner_checks_out_the_pinned_commit(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    first, _ = make_origin(origin)
    cloner = git_cloner(str(origin), first, allowed_protocols="file")

    result = await run_stage1(cloner, first, Recorder(), work_dir=tmp_path)

    # 최신 커밋이 아니라 고정한 첫 커밋 기준으로 분석합니다.
    assert [s.name for s in result.symbols] == ["first"]
    assert {s.commit_sha for s in result.symbols} == {first}
    assert [p.name for p in tmp_path.iterdir()] == ["origin"]


async def test_git_cloner_failure_cleans_up_and_hides_git_output(tmp_path: Path) -> None:
    cloner: Cloner = git_cloner(str(tmp_path / "missing"), SHA, allowed_protocols="file")
    recorder = Recorder()

    with pytest.raises(CloneError) as info:
        await run_stage1(cloner, SHA, recorder, work_dir=tmp_path)

    assert "missing" not in str(info.value)
    assert recorder.states[-1].status is AnalysisStatus.FAILED
    assert list(tmp_path.iterdir()) == []


def test_default_protocol_rejects_local_and_command_transports(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    first, _ = make_origin(origin)

    for url in (str(origin), "ext::sh -c 'echo pwned > " + str(tmp_path / "pwned") + "'"):
        with pytest.raises(CloneError):
            git_cloner(url, first)(tmp_path / "dest")
    assert not (tmp_path / "pwned").exists()


@pytest.mark.parametrize(
    ("url", "sha"),
    [("--upload-pack=evil", SHA), ("https://example.com/r.git", "abc"), ("x", "B" * 40)],
)
def test_git_cloner_rejects_bad_arguments(url: str, sha: str) -> None:
    with pytest.raises(ValueError, match=r"commit_sha|url"):
        git_cloner(url, sha)
