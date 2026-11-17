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


class CloneError(Exception):
    """메시지에 git 출력(레포 내용이 섞일 수 있음)을 넣지 않습니다."""


# 토큰 인증 헤더는 이 주소로 시작하는 요청에만 붙입니다.
GITHUB_HTTPS = "https://github.com/"


def git_env(allowed_protocols: str, token: SecretStr | None = None) -> dict[str, str]:
    """git 명령 환경 변수입니다.

    token이 있으면 github.com 요청에만 Authorization 헤더를 붙입니다 (#18, 요청자 본인 토큰).
    토큰은 명령줄 인자나 레포 설정 파일(.git/config)에 넣지 않고 환경 변수로만 넘깁니다.
    명령줄 인자는 같은 컴퓨터의 다른 사용자도 ps로 볼 수 있기 때문입니다.
    GIT_CONFIG_COUNT는 git 2.31 이상에서 읽습니다. 배포 이미지(#19)의 git 버전을 확인합니다.
    """
    env = {
        **os.environ,
        "GIT_ALLOW_PROTOCOL": allowed_protocols,
        "GIT_TERMINAL_PROMPT": "0",
    }
    if token is not None:
        basic = base64.b64encode(f"x-access-token:{token.get_secret_value()}".encode()).decode()
        env |= {
            # 시스템 git 설정(/etc/gitconfig)을 읽지 않습니다.
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": f"http.{GITHUB_HTTPS}.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
            # 자격 증명 도우미를 끕니다. 토큰이 거절되면 git이 도우미에 erase를 보내
            # 서버 컴퓨터에 저장된 다른 GitHub 자격 증명을 지우는 일을 막습니다.
            "GIT_CONFIG_KEY_1": "credential.helper",
            "GIT_CONFIG_VALUE_1": "",
        }
    return env


def git_cloner(
    url: str,
    commit_sha: str,
    *,
    allowed_protocols: str = "https",
    token: SecretStr | None = None,
) -> Cloner:
    """`url`의 `commit_sha`를 내려받는 Cloner를 만듭니다. 기본은 https만 허용합니다.

    token을 주면 github.com https 주소에만 쓸 수 있습니다. 다른 주소로 토큰이 나가지 않게 합니다.
    """
    if token is not None and not url.startswith(GITHUB_HTTPS):
        raise ValueError("토큰은 github.com https 주소에만 씁니다")
    if not _SHA.fullmatch(commit_sha):
        raise ValueError("commit_sha는 40자리 소문자 16진수여야 합니다")
    if url.startswith("-"):
        raise ValueError("url은 옵션처럼 보이면 안 됩니다")

    def clone(dest: Path) -> None:
        env = git_env(allowed_protocols, token)
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
        for step in steps:
            try:
                subprocess.run(
                    ["git", *step],
                    check=True,
                    capture_output=True,
                    env=env,
                    timeout=CLONE_TIMEOUT_S,
                )
            except subprocess.TimeoutExpired:
                raise CloneError("git 명령이 제한 시간을 넘었습니다") from None
            except subprocess.CalledProcessError as error:
                raise CloneError(
                    f"git 명령이 실패했습니다 (종료 코드 {error.returncode})"
                ) from None

    return clone
