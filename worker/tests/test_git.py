"""git_cloner의 토큰 처리. git은 실제로 부르지 않고 subprocess.run 호출을 기록합니다."""

import base64
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from gitinsight_worker.git import git_cloner

SHA = "a" * 40
TOKEN = "ghu_TestUserAccessToken0000000000000000"
URL = "https://github.com/owner/private-repo.git"


def record_runs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], dict[str, str]]]:
    runs: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        runs.append((args, kwargs["env"]))
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return runs


def git_config(env: dict[str, str]) -> dict[str, str]:
    """GIT_CONFIG_COUNT/KEY/VALUE 환경 변수를 git 설정 사전으로 읽습니다."""
    count = int(env.get("GIT_CONFIG_COUNT", "0"))
    return {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(count)}


def test_token_goes_only_to_clone_step_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runs = record_runs(monkeypatch)

    git_cloner(URL, SHA, token=SecretStr(TOKEN))(tmp_path / "repo")

    (clone_args, clone_env), (checkout_args, checkout_env) = runs
    basic = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
    assert git_config(clone_env) == {
        "credential.helper": "",
        "http.https://github.com/.extraheader": f"Authorization: Basic {basic}",
        "http.followRedirects": "false",
    }
    # 체크아웃은 네트워크를 쓰지 않으므로 토큰을 넘기지 않습니다.
    assert git_config(checkout_env) == {"credential.helper": ""}
    # 토큰은 명령줄 인자와 주소에 들어가지 않습니다 (.git/config의 remote 주소에도 남지 않습니다).
    for args in (clone_args, checkout_args):
        assert not any(TOKEN in a or basic in a for a in args)
    assert URL in clone_args


def test_without_token_no_auth_header(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runs = record_runs(monkeypatch)

    git_cloner(URL, SHA)(tmp_path / "repo")

    assert [git_config(env) for _, env in runs] == [{"credential.helper": ""}] * 2


@pytest.mark.parametrize(
    "url",
    ["https://gitlab.com/owner/repo.git", "http://github.com/owner/repo.git", "/tmp/origin"],
)
def test_token_is_refused_outside_github_https(url: str) -> None:
    with pytest.raises(ValueError, match="GitHub"):
        git_cloner(url, SHA, token=SecretStr(TOKEN))
