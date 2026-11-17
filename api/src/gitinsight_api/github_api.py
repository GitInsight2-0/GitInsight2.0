"""로그인 사용자 토큰으로 부르는 GitHub REST API (#18 2단계).

- 레포 목록: 사용자가 App을 설치한 곳(GET /user/installations)의 레포 중 사용자가 읽을 수 있는 것
- 파일 내용: /api/code가 씁니다. 받은 코드는 응답으로만 넘기고 저장하지 않습니다
분석 요청에 쓰는 레포 조회와 최신 커밋은 github_repos.py(#65)에 있습니다.

토큰, 응답 본문, 코드는 로그와 예외 메시지에 넣지 않습니다. 로그에는 HTTP 상태와 예외 종류만 남깁니다.
GitHub 문서: https://docs.github.com/rest/apps/installations#list-app-installations-accessible-to-the-user-access-token
"""

import logging
import re
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

from gitinsight_api.github_oauth import API_VERSION, HTTP_TIMEOUT_S
from gitinsight_contracts.api import Repo

logger = logging.getLogger(__name__)

API = "https://api.github.com"
PER_PAGE = 100
# 설치 한 곳에서 읽는 최대 쪽 수입니다 (100개 x 5쪽). 데모 사용자에게는 넉넉합니다.
MAX_PAGES = 5
# /api/code가 돌려주는 파일 크기 상한입니다. 화면에 보여 줄 소스 파일은 이보다 훨씬 작습니다.
MAX_FILE_BYTES = 1_000_000

_SHA = re.compile(r"[0-9a-f]{40}")
_REPO_FULL_NAME = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class GitHubUnauthorizedError(Exception):
    """토큰이 만료되었거나 취소되었습니다. 다시 로그인해야 합니다."""


class GitHubUnavailableError(Exception):
    """GitHub 호출이 실패했습니다. 메시지는 짧은 원인 이름만 담습니다."""


class GitHubFileTooLargeError(Exception):
    pass


class _RepoBody(BaseModel):
    id: int
    full_name: str
    private: bool
    default_branch: str = "main"

    def contract(self) -> Repo:
        return Repo(
            repo_id=self.id,
            full_name=self.full_name,
            private=self.private,
            default_branch=self.default_branch,
        )


class _Installation(BaseModel):
    id: int


def _path_segments(path: str) -> str:
    """레포 안 파일 경로를 URL 경로로 바꿉니다. `.`, `..`, 빈 조각은 받지 않습니다."""
    parts = path.split("/")
    if not parts or any(p in ("", ".", "..") for p in parts):
        raise ValueError("파일 경로 형식이 아닙니다")
    return "/".join(quote(p, safe="") for p in parts)


class GitHubAPI:
    def __init__(self, client: httpx.AsyncClient, token: SecretStr) -> None:
        self._client = client
        self._token = token

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token.get_secret_value()}",
            "Accept": accept,
            "X-GitHub-Api-Version": API_VERSION,
        }

    async def _json(self, path: str, params: dict[str, Any]) -> Any:
        try:
            response = await self._client.get(
                f"{API}{path}",
                params=params,
                headers=self._headers(),
                timeout=HTTP_TIMEOUT_S,
            )
        except httpx.HTTPError as exc:
            logger.warning("github_api_request_failed error=%s", type(exc).__name__)
            raise GitHubUnavailableError(type(exc).__name__) from None
        if response.status_code == 401:
            raise GitHubUnauthorizedError
        if response.status_code != 200:
            logger.warning("github_api_status status=%s", response.status_code)
            raise GitHubUnavailableError(f"HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError:
            logger.warning("github_api_bad_json")
            raise GitHubUnavailableError("bad json") from None

    async def _paged(self, path: str, key: str) -> list[Any]:
        items: list[Any] = []
        for page in range(1, MAX_PAGES + 1):
            body = await self._json(path, {"per_page": PER_PAGE, "page": page})
            chunk = body.get(key) if isinstance(body, dict) else None
            if not isinstance(chunk, list):
                logger.warning("github_api_bad_shape")
                raise GitHubUnavailableError("bad shape")
            items.extend(chunk)
            if len(chunk) < PER_PAGE:
                return items
        # 상한까지 읽고도 더 있으면 목록 일부만 돌려줍니다. 잘린 것을 로그로 알 수 있게 남깁니다.
        logger.warning("github_api_page_cap pages=%d", MAX_PAGES)
        return items

    async def list_repos(self) -> list[Repo]:
        """App이 설치되어 있고 사용자가 읽을 수 있는 레포입니다. 설치가 없으면 빈 목록입니다."""
        repos: dict[int, Repo] = {}
        try:
            installations = [
                _Installation.model_validate(i)
                for i in await self._paged("/user/installations", "installations")
            ]
            for installation in installations:
                for raw in await self._paged(
                    f"/user/installations/{installation.id}/repositories", "repositories"
                ):
                    repo = _RepoBody.model_validate(raw).contract()
                    repos[repo.repo_id] = repo
        except ValidationError:
            logger.warning("github_api_bad_shape")
            raise GitHubUnavailableError("bad shape") from None
        return sorted(repos.values(), key=lambda r: r.full_name.casefold())

    async def file_text(self, repo_full_name: str, commit_sha: str, path: str) -> str | None:
        """고정 커밋의 파일 내용입니다. 파일이 없으면 None입니다."""
        if not _REPO_FULL_NAME.fullmatch(repo_full_name) or ".." in repo_full_name:
            raise ValueError("repo_full_name은 owner/name 형식이어야 합니다")
        if not _SHA.fullmatch(commit_sha):
            raise ValueError("commit_sha는 40자리 소문자 16진수여야 합니다")
        url = f"{API}/repos/{repo_full_name}/contents/{_path_segments(path)}"
        # 큰 파일을 메모리에 다 받지 않도록 나눠 읽다가 상한을 넘으면 멈춥니다.
        try:
            async with self._client.stream(
                "GET",
                url,
                params={"ref": commit_sha},
                headers=self._headers("application/vnd.github.raw+json"),
                timeout=HTTP_TIMEOUT_S,
            ) as response:
                if response.status_code == 401:
                    raise GitHubUnauthorizedError
                if response.status_code == 404:
                    return None
                if response.status_code != 200:
                    logger.warning("github_api_status status=%s", response.status_code)
                    raise GitHubUnavailableError(f"HTTP {response.status_code}")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_FILE_BYTES:
                        raise GitHubFileTooLargeError
        except httpx.HTTPError as exc:
            logger.warning("github_api_request_failed error=%s", type(exc).__name__)
            raise GitHubUnavailableError(type(exc).__name__) from None
        return body.decode("utf-8", errors="replace")
