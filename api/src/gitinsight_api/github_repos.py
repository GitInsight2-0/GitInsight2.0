"""분석할 레포를 요청자 본인의 GitHub 토큰으로 확인합니다 (#12, #18).

웹은 레포 id만 보냅니다(계약 AnalysisCreate).
서버는 요청자 토큰으로 레포 이름과 기본 브랜치의 최신 커밋을 받아 분석 키(레포, 커밋)를 만듭니다.
GitHub App 사용자 토큰은 그 사람이 읽을 수 있고 App이 설치된 레포만 보이므로, 이 조회가 접근 권한 확인을 겸합니다.
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


async def resolve_repo(client: httpx.AsyncClient, token: SecretStr, repo_id: int) -> RepoTarget:
    """레포 id를 레포 이름과 기본 브랜치의 최신 커밋으로 바꿉니다."""
    response = await _get(
        client, token, f"{API_URL}/repositories/{repo_id}", "application/vnd.github+json"
    )
    try:
        repo = _RepoResponse.model_validate_json(response.content)
    except ValidationError:
        logger.warning("github_repo_lookup_invalid")
        raise GitHubUnavailableError("레포 응답 형식 오류") from None
    if not _REPO_FULL_NAME.fullmatch(repo.full_name) or ".." in repo.full_name:
        logger.warning("github_repo_lookup_invalid")
        raise GitHubUnavailableError("레포 이름 형식 오류")
    # 브랜치 이름에 /가 있어도 경로 한 칸으로 넘깁니다.
    branch = quote(repo.default_branch, safe="")
    response = await _get(
        client,
        token,
        f"{API_URL}/repos/{repo.full_name}/commits/{branch}",
        # 커밋 SHA만 본문으로 받습니다.
        "application/vnd.github.sha",
    )
    sha = response.text.strip()
    if not _SHA.fullmatch(sha):
        logger.warning("github_commit_lookup_invalid")
        raise GitHubUnavailableError("커밋 응답 형식 오류")
    return RepoTarget(full_name=repo.full_name, commit_sha=sha)


async def _get(
    client: httpx.AsyncClient, token: SecretStr, url: str, accept: str
) -> httpx.Response:
    try:
        response = await client.get(
            url,
            headers={
                "Authorization": f"Bearer {token.get_secret_value()}",
                "Accept": accept,
                "X-GitHub-Api-Version": API_VERSION,
            },
            timeout=HTTP_TIMEOUT_S,
        )
    except httpx.HTTPError as exc:
        logger.warning("github_repo_request_failed error=%s", type(exc).__name__)
        raise GitHubUnavailableError("GitHub 요청 실패") from None
    status = response.status_code
    if status == 200:
        return response
    if status == 401:
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
