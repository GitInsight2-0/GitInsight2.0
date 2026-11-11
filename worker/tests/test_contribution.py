import dataclasses
import os
import subprocess
from pathlib import Path

import pytest

from gitinsight_contracts.analysis import ContributionLabel
from gitinsight_worker.contribution import (
    MAX_COMMITS,
    UNKNOWN,
    BlameError,
    BlameLine,
    Contribution,
    Requester,
    blame_file,
    label_for,
    parse_line_porcelain,
    summarize,
)
from gitinsight_worker.stage1 import analyze_tree, run_stage1

from worker_test_support import Recorder

ME = Requester.of(emails=["me@example.com"])


def line(sha_char: str, email: str = "o@example.com", time: int = 0) -> BlameLine:
    return BlameLine(sha_char * 40, email, time)


@pytest.mark.parametrize(
    ("share", "label"),
    [
        (1.0, ContributionLabel.VERIFIED),
        (0.5, ContributionLabel.VERIFIED),
        (0.49, ContributionLabel.SHARED),
        (0.1, ContributionLabel.SHARED),
        (0.09, ContributionLabel.UNVERIFIED),
        (0.0, ContributionLabel.UNVERIFIED),
    ],
)
def test_label_thresholds(share: float, label: ContributionLabel) -> None:
    assert label_for(share) is label


def test_requester_matches_email_ignoring_case() -> None:
    assert ME.matches("ME@Example.com")
    assert not ME.matches("o@example.com")
    assert Requester.of(emails=["", "  "]).emails == frozenset()


def test_requester_adds_github_noreply_addresses() -> None:
    requester = Requester.of(emails=["me@example.com"], login="Me-Dev", user_id=42)

    assert requester.matches("me-dev@users.noreply.github.com")
    assert requester.matches("42+Me-Dev@users.noreply.github.com")
    assert not requester.matches("41+me-dev@users.noreply.github.com")
    # 숫자 id가 없으면 옛 noreply 형태만 더합니다.
    assert Requester.of(login="me").emails == {"me@users.noreply.github.com"}


def test_summarize_counts_only_lines_in_range() -> None:
    blame = [line("a", "me@example.com")] * 2 + [line("b")] * 8

    assert summarize(blame, 1, 2, ME).label is ContributionLabel.VERIFIED
    assert summarize(blame, 1, 10, ME).label is ContributionLabel.SHARED
    assert summarize(blame, 3, 10, ME).label is ContributionLabel.UNVERIFIED


def test_summarize_keeps_most_recent_commits_first() -> None:
    blame = [line(c, time=t) for c, t in zip("abcde", [5, 1, 4, 2, 3], strict=True)]

    result = summarize(blame, 1, 5, ME)

    assert result.commits == ("a" * 40, "c" * 40, "e" * 40)
    assert len(result.commits) == MAX_COMMITS


def test_summarize_without_requester_or_lines() -> None:
    blame = [line("a", "me@example.com", time=1)]

    assert summarize(blame, 1, 1, None) == Contribution(ContributionLabel.UNVERIFIED, ("a" * 40,))
    assert summarize(blame, 5, 6, ME) == UNKNOWN


def test_contribution_keeps_no_author_identity() -> None:
    # 분석 결과에는 라벨과 커밋 SHA만 남깁니다.
    assert {f.name for f in dataclasses.fields(Contribution)} == {"label", "commits"}


def test_parse_line_porcelain_ignores_code_that_looks_like_headers() -> None:
    sha = "c" * 40
    output = (
        f"{sha} 1 1 2\nauthor Me\nauthor-mail <me@example.com>\ncommitter-time 7\n"
        "\tcommitter-time 1\n"
        f"{sha} 2 2\nauthor Me\nauthor-mail <me@example.com>\ncommitter-time 7\n"
        "\tauthor-mail <fake@example.com>\n"
    ).encode()

    assert parse_line_porcelain(output) == [BlameLine(sha, "me@example.com", 7)] * 2


def test_blame_file_rejects_bad_sha_and_hides_git_output(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="commit_sha"):
        blame_file(tmp_path, "a.py", "HEAD")
    with pytest.raises(BlameError, match="종료 코드") as info:
        blame_file(tmp_path, "secret-name.py", "a" * 40)
    assert "secret-name" not in str(info.value)


def git(*args: str, cwd: Path, author: tuple[str, str] = ("Other", "o@example.com")) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": author[0],
        "GIT_AUTHOR_EMAIL": author[1],
        "GIT_COMMITTER_NAME": author[0],
        "GIT_COMMITTER_EMAIL": author[1],
    }
    out = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env
    )
    return out.stdout.strip()


def commit(repo: Path, text: str, author: tuple[str, str]) -> str:
    (repo / "mod.py").write_text(text, encoding="utf-8")
    git("add", "-A", cwd=repo, author=author)
    git("commit", "--quiet", "-m", "change", cwd=repo, author=author)
    return git("rev-parse", "HEAD", cwd=repo)


MINE = """\
def mine():
    a = 1
    b = 2
    return a + b
"""

THEIRS = """\


def theirs():
    return 0
"""


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, list[str]]:
    """mine()은 Me가, theirs()는 Other가 쓰고, Other가 mine()의 들여쓰기만 바꾼 레포입니다."""
    path = tmp_path / "repo"
    path.mkdir()
    git("init", "--quiet", cwd=path)
    first = commit(path, MINE, ("Me", "me@example.com"))
    second = commit(path, MINE + THEIRS, ("Other", "o@example.com"))
    whitespace_only = MINE.replace("    a = 1", "    a = 1    ")
    third = commit(path, whitespace_only + THEIRS, ("Other", "o@example.com"))
    return path, [first, second, third]


def test_analyze_tree_labels_symbols_from_blame(repo: tuple[Path, list[str]]) -> None:
    path, (first, second, third) = repo

    result = analyze_tree(path, third, ME)

    # 공백만 바꾼 커밋은 blame에서 무시하므로 mine()은 모두 Me의 줄입니다.
    assert result.contributions["mod.py::mine"] == Contribution(
        ContributionLabel.VERIFIED, (first,)
    )
    assert result.contributions["mod.py::theirs"] == Contribution(
        ContributionLabel.UNVERIFIED, (second,)
    )


def test_analyze_tree_uses_the_pinned_commit_not_the_worktree(
    repo: tuple[Path, list[str]],
) -> None:
    path, (first, _, _) = repo

    result = analyze_tree(path, first, ME)

    # 작업 폴더에는 theirs()가 있지만 첫 커밋에는 없으므로, 그 범위의 blame 결과도 없습니다.
    assert result.contributions["mod.py::theirs"] == UNKNOWN
    assert result.contributions["mod.py::mine"].commits == (first,)


def test_same_name_with_other_email_is_not_the_requester(repo: tuple[Path, list[str]]) -> None:
    path, (_, _, third) = repo
    # mine()의 작성자 이름은 "Me"지만, 이메일이 다르면 다른 사람으로 봅니다.
    requester = Requester.of(emails=["someone-else@example.com"])

    result = analyze_tree(path, third, requester)

    assert result.contributions["mod.py::mine"].label is ContributionLabel.UNVERIFIED


def test_blame_budget_exhausted_leaves_symbols_unknown(repo: tuple[Path, list[str]]) -> None:
    path, (_, _, third) = repo

    result = analyze_tree(path, third, ME, blame_budget_s=0)

    # 시간 상한을 넘겨도 분석은 끝나고, 심볼은 그대로 나오며 기여 근거만 비어 있습니다.
    assert {s.name for s in result.symbols} == {"mine", "theirs"}
    assert set(result.contributions.values()) == {UNKNOWN}


def test_tree_without_git_gets_unknown_contribution(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def a():\n    pass\n", encoding="utf-8")

    result = analyze_tree(tmp_path, "b" * 40, ME)

    assert result.contributions == {"a.py::a": UNKNOWN}


async def test_run_stage1_passes_requester(repo: tuple[Path, list[str]], tmp_path: Path) -> None:
    path, (_, _, third) = repo

    def clone(dest: Path) -> None:
        subprocess.run(
            ["git", "clone", "--quiet", "--", str(path), str(dest)],
            check=True,
            capture_output=True,
        )

    work = tmp_path / "work"
    work.mkdir()
    result = await run_stage1(clone, third, Recorder(), requester=ME, work_dir=work)

    assert result.contributions["mod.py::mine"].label is ContributionLabel.VERIFIED
    assert list(work.iterdir()) == []
