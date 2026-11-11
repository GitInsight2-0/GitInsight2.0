"""기여 근거 분석: 심볼 줄 범위의 git blame으로 기여 라벨과 최근 커밋을 정합니다.

라벨은 질문 순서와 도입 문구에만 씁니다. 점수와 리포트 프롬프트에는 쓰지 않습니다.
git 이력은 각 줄을 마지막으로 고친 사람만 보여 주고, 작성자 정보는 누구나 꾸밀 수 있기 때문입니다.
결과에는 라벨과 커밋 SHA만 남기고 작성자 이름과 이메일은 남기지 않습니다.

한계: `-w`는 공백 변경만 무시합니다.
함수를 다른 파일로 옮기거나 포매터로 줄을 바꾼 커밋은 그 커밋 작성자의 줄로 잡힙니다.
이동을 따라가는 `-M`/`-C`는 blame을 크게 느리게 해서 쓰지 않습니다.
"""

import logging
import re
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from gitinsight_contracts.analysis import ContributionLabel

logger = logging.getLogger(__name__)

# 임시 기준값입니다. 팀 합의 뒤 확정합니다 (#10).
# 요청자가 남아 있는 줄의 절반 이상을 마지막으로 고쳤으면 "기여 근거 확인"으로 봅니다.
VERIFIED_MIN_SHARE = 0.5
# 10% 미만은 이름 변경이나 몇 줄 수정 같은 작은 손질로 보고 "공동 기여"로 치지 않습니다.
SHARED_MIN_SHARE = 0.1
# Topic.commits에 넣을 최근 커밋 수입니다. 샘플 데이터와 같은 기준입니다.
MAX_COMMITS = 3

# 파일 하나의 blame 제한 시간입니다.
BLAME_TIMEOUT_S = 60
# 레포 전체 blame 시간 상한입니다. 넘으면 남은 심볼은 "확인 불가"로 둡니다.
# 라벨은 질문 순서와 도입 문구에만 쓰여서, 일부가 없어도 면접은 진행됩니다.
BLAME_BUDGET_S = 30
_SHA = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class Requester:
    """분석을 요청한 사람의 git 신원입니다. 메모리에서만 쓰고 결과에 저장하지 않습니다.

    커밋 작성자 이메일로만 맞춥니다.
    git 작성자 이름은 고유하지 않고 GitHub 로그인과도 대부분 달라서 쓰지 않습니다.
    """

    emails: frozenset[str]

    @classmethod
    def of(
        cls,
        *,
        emails: Iterable[str] = (),
        login: str | None = None,
        user_id: int | None = None,
    ) -> "Requester":
        """GitHub 인증 이메일과 계정으로 만듭니다. 계정을 주면 noreply 주소 두 형태를 더합니다."""
        candidates = list(emails)
        if login:
            candidates.append(f"{login}@users.noreply.github.com")
            if user_id is not None:
                candidates.append(f"{user_id}+{login}@users.noreply.github.com")
        return cls(emails=frozenset(e.strip().casefold() for e in candidates if e.strip()))

    def matches(self, email: str) -> bool:
        return email.casefold() in self.emails


@dataclass(frozen=True)
class BlameLine:
    """blame 한 줄의 결과입니다. 코드 내용과 작성자 이름은 담지 않습니다."""

    commit_sha: str
    author_email: str
    committer_time: int


@dataclass(frozen=True)
class Contribution:
    label: ContributionLabel
    # 이 범위에 남아 있는 줄을 만든 커밋 중 최근 것부터입니다.
    commits: tuple[str, ...]


UNKNOWN = Contribution(label=ContributionLabel.UNVERIFIED, commits=())


class BlameError(Exception):
    """메시지에 git 출력(레포 내용이 섞일 수 있음)을 넣지 않습니다."""


def parse_line_porcelain(output: bytes) -> list[BlameLine]:
    """`git blame --line-porcelain` 출력을 줄 순서대로 읽습니다.

    코드 줄은 탭으로 시작하므로 머리 줄(author 등)로 잘못 읽히지 않습니다.
    """
    lines: list[BlameLine] = []
    sha = email = ""
    time = 0
    for raw in output.split(b"\n"):
        if raw.startswith(b"\t"):
            lines.append(BlameLine(sha, email, time))
            continue
        text = raw.decode("utf-8", errors="replace")
        key, _, value = text.partition(" ")
        if _SHA.fullmatch(key):
            sha = key
        elif key == "author-mail":
            email = value.removeprefix("<").removesuffix(">")
        elif key == "committer-time":
            time = int(value) if value.isdigit() else 0
    return lines


def blame_file(
    root: Path, path: str, commit_sha: str, *, timeout: float = BLAME_TIMEOUT_S
) -> list[BlameLine]:
    """파일 전체를 한 번에 blame합니다. 공백만 바꾼 수정은 무시합니다(-w)."""
    if not _SHA.fullmatch(commit_sha):
        raise ValueError("commit_sha는 40자리 소문자 16진수여야 합니다")
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "blame", "--line-porcelain", "-w", commit_sha, "--", path],
            check=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise BlameError("git blame이 제한 시간을 넘었습니다") from None
    except subprocess.CalledProcessError as error:
        raise BlameError(f"git blame이 실패했습니다 (종료 코드 {error.returncode})") from None
    return parse_line_porcelain(out.stdout)


def label_for(share: float) -> ContributionLabel:
    if share >= VERIFIED_MIN_SHARE:
        return ContributionLabel.VERIFIED
    if share >= SHARED_MIN_SHARE:
        return ContributionLabel.SHARED
    return ContributionLabel.UNVERIFIED


def summarize(
    blame: Sequence[BlameLine], start_line: int, end_line: int, requester: Requester | None
) -> Contribution:
    """줄 범위(1부터, 양끝 포함)의 기여 라벨과 최근 커밋을 정합니다."""
    in_range = blame[start_line - 1 : end_line]
    if not in_range:
        return UNKNOWN
    times: dict[str, int] = {}
    for line in in_range:
        times[line.commit_sha] = max(times.get(line.commit_sha, 0), line.committer_time)
    recent = sorted(times, key=lambda sha: (-times[sha], sha))[:MAX_COMMITS]
    if requester is None:
        label = ContributionLabel.UNVERIFIED
    else:
        mine = sum(requester.matches(line.author_email) for line in in_range)
        label = label_for(mine / len(in_range))
    return Contribution(label=label, commits=tuple(recent))
