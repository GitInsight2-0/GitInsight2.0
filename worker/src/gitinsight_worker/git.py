"""분석할 레포를 고정 커밋으로 내려받습니다.

레포는 신뢰할 수 없는 입력입니다. 외부 명령을 실행하는 전송 방식과 심볼릭 링크를 막습니다.
"""

import base64
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from pydantic import SecretStr

# 레포를 dest 안에 채워 넣는 함수입니다. 테스트에서는 가짜로 바꿉니다.
Cloner = Callable[[Path], None]

CLONE_TIMEOUT_S = 300
# 계약의 CommitSha와 같은 형식입니다.
_SHA = re.compile(r"[0-9a-f]{40}")
# 토큰을 보내도 되는 곳입니다. 다른 주소로는 토큰을 실은 요청을 보내지 않습니다.
GITHUB_HTTPS = "https://github.com/"


class CloneError(Exception):
    """메시지에 git 출력(레포 내용이 섞일 수 있음)을 넣지 않습니다."""


def git_cloner(
    url: str,
    commit_sha: str,
    *,
    allowed_protocols: str = "https",
    token: SecretStr | None = None,
) -> Cloner:
    """`url`의 `commit_sha`를 내려받는 Cloner를 만듭니다. 기본은 https만 허용합니다.

    token을 주면 GitHub에 그 토큰으로 인증해 비공개 레포를 받습니다.
    토큰은 clone 단계의 환경 변수로만 넘깁니다. 명령줄 인자, 주소, .git/config에는 남지 않습니다.
    """
    if not _SHA.fullmatch(commit_sha):
        raise ValueError("commit_sha는 40자리 소문자 16진수여야 합니다")
    if url.startswith("-"):
        raise ValueError("url은 옵션처럼 보이면 안 됩니다")
    if token is not None and not url.startswith(GITHUB_HTTPS):
        raise ValueError("토큰은 GitHub https 주소에만 씁니다")

    def clone(dest: Path) -> None:
        env = {
            **os.environ,
            "GIT_ALLOW_PROTOCOL": allowed_protocols,
            "GIT_TERMINAL_PROMPT": "0",
            # 시스템 자격 증명 도우미(예: Windows 자격 증명 관리자)를 쓰지도, 저장하지도 않습니다.
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": "",
        }
        clone_env = env if token is None else {**env, **_auth_env(token)}
        # 변경 이력은 기여 분석(blame)에 필요하므로 전체를 받습니다.
        steps = [
            ["clone", "--quiet", "--no-checkout", "--", url, str(dest)],
            [
                "-C",
                str(dest),
                "-c",
                "core.symlinks=false",
                "checkout",
                "--quiet",
                "--detach",
                commit_sha,
            ],
        ]
        for step, step_env in zip(steps, (clone_env, env), strict=True):
            try:
                subprocess.run(
                    ["git", *step],
                    check=True,
                    capture_output=True,
                    env=step_env,
                    timeout=CLONE_TIMEOUT_S,
                )
            except subprocess.TimeoutExpired:
                raise CloneError("git 명령이 제한 시간을 넘었습니다") from None
            except subprocess.CalledProcessError as error:
                raise CloneError(
                    f"git 명령이 실패했습니다 (종료 코드 {error.returncode})"
                ) from None

    return clone


def _auth_env(token: SecretStr) -> dict[str, str]:
    """GitHub 요청에만 붙는 인증 헤더를 git 설정 환경 변수로 만듭니다."""
    basic = base64.b64encode(f"x-access-token:{token.get_secret_value()}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_1": f"http.{GITHUB_HTTPS}.extraheader",
        "GIT_CONFIG_VALUE_1": f"Authorization: Basic {basic}",
        # 레포 이름은 GitHub API에서 받은 현재 이름이라 리다이렉트가 필요 없습니다.
        # 따라가지 않아, 인증 헤더가 실린 요청이 다른 곳으로 넘어가지 않게 합니다.
        "GIT_CONFIG_KEY_2": "http.followRedirects",
        "GIT_CONFIG_VALUE_2": "false",
    }
