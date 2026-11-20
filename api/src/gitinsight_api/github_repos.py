"""분석할 레포를 요청자 본인의 GitHub 토큰으로 확인합니다 (#12, #18).

웹은 레포 id만 보냅니다(계약 AnalysisCreate).
서버는 요청자 토큰으로 레포 이름과 기본 브랜치의 최신 커밋을 받아 분석 키(레포, 커밋)를 만듭니다.
GitHub App 사용자 토큰은 그 사람이 읽을 수 있고 App이 설치된 레포만 보이므로, 이 조회가 접근 권한 확인을 겸합니다.

면접관이 지원자의 공개 레포를 분석할 때(#173)는 웹이 레포 주소를 보냅니다(계약 PublicAnalysisCreate).
서버는 주소에서 owner/name만 읽고(parse_public_repo), GitHub API로 공개 레포인지 확인합니다(resolve_public_repo).
App이 설치되지 않은 레포라 사용자 토큰으로 보이지 않으면 토큰 없이 한 번 더 묻습니다. 토큰 없이 보이는 레포는 공개 레포뿐입니다.
토큰과 응답 본문은 로그와 예외 메시지에 넣지 않습니다.
"""

import logging
import re
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

from gitinsight_api.github_oauth import API_VERSION, HTTP_TIMEOUT_S

logger = logging.getLogger(__name__)

API_URL = "https://api.github.com"
# 계약 AnalysisResult.repo_full_name, CommitSha와 같은 형식입니다.
_REPO_FULL_NAME = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_SHA = re.compile(r"[0-9a-f]{40}")


class RepoNotFoundError(Exception):
    """레포가 없거나 요청자가 읽을 수 없습니다 (App 미설치 포함)."""


class RepoNotPublicError(Exception):
    """공개 레포가 아닙니다. 면접관 분석(#173)은 공개 레포만 받습니다."""


class EmptyRepoError(Exception):
    """기본 브랜치에 커밋이 없습니다."""


class GitHubTokenError(Exception):
    """요청자 토큰이 만료되었거나 취소되었습니다. 다시 로그인하면 됩니다."""


class GitHubUnavailableError(Exception):
    """GitHub 호출이 실패했거나 응답 형식이 예상과 다릅니다."""


@dataclass(frozen=True)
class RepoTarget:
    full_name: str
    commit_sha: str


class _RepoResponse(BaseModel):
    full_name: str
    default_branch: str


class _PublicRepoResponse(_RepoResponse):
    private: bool


async def resolve_repo(client: httpx.AsyncClient, token: SecretStr, repo_id: int) -> RepoTarget:
    """레포 id를 레포 이름과 기본 브랜치의 최신 커밋으로 바꿉니다."""
    response = await _get(
        client, token, f"{API_URL}/repositories/{repo_id}", "application/vnd.github+json"
    )
    repo = _parse_repo(response, _RepoResponse)
    return RepoTarget(
        full_name=repo.full_name,
        commit_sha=await _latest_commit(client, token, repo.full_name, repo.default_branch),
    )


# GitHub 레포 주소입니다. 경로 뒤(/tree/main, ?tab=..., #readme)는 읽지 않습니다.
_GITHUB_URL = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/([^/\s?#]+)/([^/\s?#]+)(?:[/?#].*)?", re.IGNORECASE
)


def parse_public_repo(repo_url: str) -> str | None:
    """레포 주소에서 owner/name을 읽습니다. GitHub 주소나 owner/name이 아니면 None입니다."""
    text = repo_url.strip()
    match = _GITHUB_URL.fullmatch(text)
    if match is not None:
        owner, name = match.group(1), match.group(2)
    elif text.count("/") == 1:
        owner, name = text.split("/")
    else:
        return None
    name = name.removesuffix(".git")
    full_name = f"{owner}/{name}"
    if not _REPO_FULL_NAME.fullmatch(full_name) or ".." in full_name:
        return None
    return full_name


async def resolve_public_repo(
    client: httpx.AsyncClient, token: SecretStr, full_name: str
) -> RepoTarget:
    """공개 레포의 정식 이름과 기본 브랜치의 최신 커밋입니다. 비공개면 RepoNotPublicError입니다 (#173).

    App이 설치되지 않은 레포는 사용자 토큰으로 보이지 않을 수 있어, 그때는 토큰 없이 한 번 더 묻습니다.
    토큰 없이 보이는 레포는 공개 레포뿐이라 비공개 레포가 새어 나가지 않습니다.
    """
    url = f"{API_URL}/repos/{full_name}"
    used: SecretStr | None = token
    try:
        response = await _get(client, token, url, "application/vnd.github+json")
    except RepoNotFoundError:
        used = None
        response = await _get(client, None, url, "application/vnd.github+json")
    repo = _parse_repo(response, _PublicRepoResponse)
    if repo.private:
        raise RepoNotPublicError("비공개 레포")
    return RepoTarget(
        full_name=repo.full_name,
        commit_sha=await _latest_commit(client, used, repo.full_name, repo.default_branch),
    )


def _parse_repo[R: _RepoResponse](response: httpx.Response, model: type[R]) -> R:
    try:
        repo = model.model_validate_json(response.content)
    except ValidationError:
        logger.warning("github_repo_lookup_invalid")
        raise GitHubUnavailableError("레포 응답 형식 오류") from None
    if not _REPO_FULL_NAME.fullmatch(repo.full_name) or ".." in repo.full_name:
        logger.warning("github_repo_lookup_invalid")
        raise GitHubUnavailableError("레포 이름 형식 오류")
    return repo


async def _latest_commit(
    client: httpx.AsyncClient, token: SecretStr | None, full_name: str, default_branch: str
) -> str:
    # 브랜치 이름에 /가 있어도 경로 한 칸으로 넘깁니다.
    branch = quote(default_branch, safe="")
    response = await _get(
        client,
        token,
        f"{API_URL}/repos/{full_name}/commits/{branch}",
        # 커밋 SHA만 본문으로 받습니다.
        "application/vnd.github.sha",
    )
    sha = response.text.strip()
    if not _SHA.fullmatch(sha):
        logger.warning("github_commit_lookup_invalid")
        raise GitHubUnavailableError("커밋 응답 형식 오류")
    return sha


async def _get(
    client: httpx.AsyncClient, token: SecretStr | None, url: str, accept: str
) -> httpx.Response:
    """token이 None이면 인증 없이 부릅니다. 공개 레포만 보입니다."""
    headers = {"Accept": accept, "X-GitHub-Api-Version": API_VERSION}
    if token is not None:
        headers["Authorization"] = f"Bearer {token.get_secret_value()}"
    try:
        response = await client.get(url, headers=headers, timeout=HTTP_TIMEOUT_S)
    except httpx.HTTPError as exc:
        logger.warning("github_repo_request_failed error=%s", type(exc).__name__)
        raise GitHubUnavailableError("GitHub 요청 실패") from None
    status = response.status_code
    if status == 200:
        return response
    if status == 401 and token is not None:
        raise GitHubTokenError("토큰 만료 또는 취소")
    rate_limited = status == 429 or (
        status == 403 and response.headers.get("x-ratelimit-remaining") == "0"
    )
    # 권한이 없는 비공개 레포는 GitHub가 404로 답합니다. 호출 한도가 아닌 403도 읽을 수 없는 레포로 봅니다.
    if status in (403, 404) and not rate_limited:
        raise RepoNotFoundError("레포를 읽을 수 없음")
    if status == 409:
        raise EmptyRepoError("커밋 없음")
    logger.warning("github_repo_request_failed status=%s", status)
    raise GitHubUnavailableError("GitHub 응답 오류")
