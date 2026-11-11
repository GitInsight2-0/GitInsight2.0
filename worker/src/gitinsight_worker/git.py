"""분석할 레포를 고정 커밋으로 내려받습니다.

레포는 신뢰할 수 없는 입력입니다. 외부 명령을 실행하는 전송 방식과 심볼릭 링크를 막습니다.
"""

import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

# 레포를 dest 안에 채워 넣는 함수입니다. 테스트에서는 가짜로 바꿉니다.
Cloner = Callable[[Path], None]

CLONE_TIMEOUT_S = 300
# 계약의 CommitSha와 같은 형식입니다.
_SHA = re.compile(r"[0-9a-f]{40}")


class CloneError(Exception):
    """메시지에 git 출력(레포 내용이 섞일 수 있음)을 넣지 않습니다."""


def git_cloner(url: str, commit_sha: str, *, allowed_protocols: str = "https") -> Cloner:
    """`url`의 `commit_sha`를 내려받는 Cloner를 만듭니다. 기본은 https만 허용합니다."""
    if not _SHA.fullmatch(commit_sha):
        raise ValueError("commit_sha는 40자리 소문자 16진수여야 합니다")
    if url.startswith("-"):
        raise ValueError("url은 옵션처럼 보이면 안 됩니다")

    def clone(dest: Path) -> None:
        env = {
            **os.environ,
            "GIT_ALLOW_PROTOCOL": allowed_protocols,
            "GIT_TERMINAL_PROMPT": "0",
        }
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
