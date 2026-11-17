"""/api/repos, /api/code와 GitHub REST 호출(#18 2단계) 테스트. GitHub는 httpx.MockTransport로 흉내 냅니다."""

import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from gitinsight_api.analyses.store import AnalysisKey, InMemoryAnalysisStore
from gitinsight_api.github_api import (
    MAX_FILE_BYTES,
    GitHubAPI,
    GitHubUnauthorizedError,
    GitHubUnavailableError,
    repo_access,
)
from gitinsight_api.login_cookie import SESSION_COOKIE, LoginSession, seal_session
from gitinsight_api.main import create_app
from gitinsight_api.settings import Settings
from gitinsight_contracts.api import Repo
from gitinsight_contracts.fixtures import load_analysis

TOKEN = "ghu_TestUserAccessToken1111111111111111"
USER_ID = "4242"
SAMPLE = load_analysis()
TOPIC_PATH = SAMPLE.topics[0].symbol.path
CODE = "def hello():\n    return 1\n"

Handler = Callable[[httpx.Request], httpx.Response]


def settings(metrics_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "lab_enabled": False,
        "metrics_path": metrics_path,
        "github_client_id": "Iv23liTest",
        "github_client_secret": "test-client-secret",
        "session_secret": "s" * 40,
        "web_origin": "https://gi.example",
        "brain_force_prepared": True,
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def repo_json(repo_id: int, full_name: str, private: bool = False) -> dict[str, Any]:
    return {"id": repo_id, "full_name": full_name, "private": private, "default_branch": "main"}


class FakeGitHub:
    """api.github.com을 흉내 냅니다. 경로별 응답을 routes에 넣습니다."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[str, Handler] = {}

    def on(self, path: str, handler: Handler) -> None:
        self.routes[path] = handler

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host != "api.github.com":
            return httpx.Response(599)
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"message": "Bad credentials"})
        route = self.routes.get(request.url.path)
        return route(request) if route else httpx.Response(404, json={"message": "Not Found"})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def page_of(items: list[Any], key: str) -> Handler:
    """per_page와 page를 지켜 나눠 돌려줍니다."""

    def handle(request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.url.query.decode())
        per_page = int(query["per_page"][0])
        page = int(query["page"][0])
        chunk = items[(page - 1) * per_page : page * per_page]
        return httpx.Response(200, json={"total_count": len(items), key: chunk})

    return handle


async def add_analysis(store: InMemoryAnalysisStore, owner: str) -> str:
    record, _ = await store.get_or_create(
        AnalysisKey(SAMPLE.repo_full_name, SAMPLE.commit_sha, owner)
    )
    await store.save_result(
        record.analysis_id, SAMPLE.model_copy(update={"analysis_id": record.analysis_id})
    )
    return record.analysis_id


def login_cookie(config: Settings, user_id: str = USER_ID, token: str = TOKEN) -> str:
    return seal_session(
        config,
        LoginSession(
            user_id=user_id,
            login="octo",
            avatar_url="",
            github_token=SecretStr(token),
            expires_at=int(time.time()) + 3600,
        ),
    )


@asynccontextmanager
async def client_for(
    app: FastAPI, config: Settings, *, logged_in: bool = True, user_id: str = USER_ID
) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://gi.example") as client:
        if logged_in:
            client.cookies.set(SESSION_COOKIE, login_cookie(config, user_id))
        yield client


def build(
    metrics_path: Path, github: FakeGitHub, store: InMemoryAnalysisStore | None = None, **kw: Any
) -> tuple[FastAPI, Settings]:
    config = settings(metrics_path, **kw)
    app = create_app(config, analysis_store=store, github_http=github.client())
    return app, config


# ---- /api/repos ----


async def test_repos_lists_installed_repos_sorted_and_deduplicated(metrics_path: Path) -> None:
    github = FakeGitHub()
    github.on("/user/installations", page_of([{"id": 1}, {"id": 2}], "installations"))
    github.on(
        "/user/installations/1/repositories",
        page_of([repo_json(10, "octo/zeta"), repo_json(11, "octo/Alpha", True)], "repositories"),
    )
    github.on(
        "/user/installations/2/repositories",
        page_of([repo_json(10, "octo/zeta"), repo_json(12, "team/beta")], "repositories"),
    )
    app, config = build(metrics_path, github)
    async with client_for(app, config) as client:
        response = await client.get("/api/repos")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    repos = [Repo.model_validate(r) for r in response.json()]
    assert [r.full_name for r in repos] == ["octo/Alpha", "octo/zeta", "team/beta"]
    assert repos[0].private is True
    assert all(r.headers["x-github-api-version"] for r in github.requests)


async def test_repos_reads_every_page(metrics_path: Path) -> None:
    github = FakeGitHub()
    many = [repo_json(i, f"octo/r{i:03d}") for i in range(250)]
    github.on("/user/installations", page_of([{"id": 1}], "installations"))
    github.on("/user/installations/1/repositories", page_of(many, "repositories"))
    app, config = build(metrics_path, github)
    async with client_for(app, config) as client:
        response = await client.get("/api/repos")
    assert len(response.json()) == 250


async def test_repos_is_empty_without_installation(metrics_path: Path) -> None:
    github = FakeGitHub()
    github.on("/user/installations", page_of([], "installations"))
    app, config = build(metrics_path, github)
    async with client_for(app, config) as client:
        response = await client.get("/api/repos")
    assert (response.status_code, response.json()) == (200, [])


async def test_repos_requires_login(metrics_path: Path) -> None:
    github = FakeGitHub()
    app, config = build(metrics_path, github)
    async with client_for(app, config, logged_in=False) as client:
        response = await client.get("/api/repos")
    assert response.status_code == 401
    assert github.requests == []


async def test_revoked_token_clears_login_cookie(metrics_path: Path) -> None:
    github = FakeGitHub()
    app, config = build(metrics_path, github)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://gi.example") as client:
        client.cookies.set(SESSION_COOKIE, login_cookie(config, token="ghu_revoked"))
        response = await client.get("/api/repos")
    assert response.status_code == 401
    assert response.json()["detail"] == "로그인이 필요합니다"
    cleared = [h for h in response.headers.get_list("set-cookie") if h.startswith(SESSION_COOKIE)]
    assert cleared and "max-age=0" in cleared[0].lower()


@pytest.mark.parametrize(
    "make",
    [
        lambda: httpx.Response(500),
        lambda: httpx.Response(200, text="not json"),
        lambda: httpx.Response(200, json={"installations": "nope"}),
        lambda: httpx.Response(200, json={"installations": [{"no_id": 1}]}),
    ],
)
async def test_repos_github_failure_is_502_without_token_in_logs(
    metrics_path: Path, caplog: pytest.LogCaptureFixture, make: Callable[[], httpx.Response]
) -> None:
    caplog.set_level(logging.DEBUG)
    github = FakeGitHub()
    github.on("/user/installations", lambda _r: make())
    app, config = build(metrics_path, github)
    async with client_for(app, config) as client:
        response = await client.get("/api/repos")
    assert response.status_code == 502
    assert TOKEN not in caplog.text


async def test_repos_is_404_in_lab_mode(metrics_path: Path) -> None:
    github = FakeGitHub()
    app, config = build(metrics_path, github, lab_enabled=True)
    async with client_for(app, config) as client:
        response = await client.get("/api/repos")
    # 웹은 404면 데모 레포 카드를 보여 줍니다.
    assert response.status_code == 404
    assert github.requests == []


# ---- /api/code ----


def serve_code(github: FakeGitHub, body: bytes = CODE.encode()) -> None:
    path = f"/repos/{SAMPLE.repo_full_name}/contents/{TOPIC_PATH}"

    def handle(request: httpx.Request) -> httpx.Response:
        assert parse_qs(request.url.query.decode())["ref"] == [SAMPLE.commit_sha]
        assert request.headers["accept"] == "application/vnd.github.raw+json"
        return httpx.Response(200, content=body)

    github.on(path, handle)


async def test_code_returns_file_from_own_analysis(metrics_path: Path) -> None:
    github = FakeGitHub()
    serve_code(github)
    store = InMemoryAnalysisStore()
    analysis_id = await add_analysis(store, USER_ID)
    app, config = build(metrics_path, github, store)
    async with client_for(app, config) as client:
        response = await client.get(
            "/api/code", params={"analysis_id": analysis_id, "path": TOPIC_PATH}
        )

    assert response.status_code == 200
    assert response.text == CODE
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in response.headers["content-security-policy"]


async def test_code_related_symbol_file_is_allowed(metrics_path: Path) -> None:
    related = next(s for t in SAMPLE.topics for s in t.related_symbols)
    github = FakeGitHub()
    github.on(
        f"/repos/{SAMPLE.repo_full_name}/contents/{related.path}",
        lambda _r: httpx.Response(200, content=b"x = 1\n"),
    )
    store = InMemoryAnalysisStore()
    analysis_id = await add_analysis(store, USER_ID)
    app, config = build(metrics_path, github, store)
    async with client_for(app, config) as client:
        response = await client.get(
            "/api/code", params={"analysis_id": analysis_id, "path": related.path}
        )
    assert response.status_code == 200


@pytest.mark.parametrize(
    "path", ["README.md", ".env", f"{TOPIC_PATH}/../.env", "../" + TOPIC_PATH, "/" + TOPIC_PATH]
)
async def test_code_only_serves_files_in_the_analysis(metrics_path: Path, path: str) -> None:
    github = FakeGitHub()
    store = InMemoryAnalysisStore()
    analysis_id = await add_analysis(store, USER_ID)
    app, config = build(metrics_path, github, store)
    async with client_for(app, config) as client:
        response = await client.get("/api/code", params={"analysis_id": analysis_id, "path": path})
    assert response.status_code == 404
    assert github.requests == []


async def test_code_of_other_users_analysis_is_404(metrics_path: Path) -> None:
    github = FakeGitHub()
    serve_code(github)
    store = InMemoryAnalysisStore()
    analysis_id = await add_analysis(store, "9999")
    app, config = build(metrics_path, github, store)
    async with client_for(app, config) as client:
        other = await client.get(
            "/api/code", params={"analysis_id": analysis_id, "path": TOPIC_PATH}
        )
        missing = await client.get(
            "/api/code", params={"analysis_id": "no-such-analysis", "path": TOPIC_PATH}
        )
    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json()
    assert github.requests == []


async def test_code_of_unfinished_analysis_is_404(metrics_path: Path) -> None:
    store = InMemoryAnalysisStore()
    record, _ = await store.get_or_create(
        AnalysisKey(SAMPLE.repo_full_name, SAMPLE.commit_sha, USER_ID)
    )
    app, config = build(metrics_path, FakeGitHub(), store)
    async with client_for(app, config) as client:
        response = await client.get(
            "/api/code", params={"analysis_id": record.analysis_id, "path": TOPIC_PATH}
        )
    assert response.status_code == 404


async def test_code_without_analysis_store_is_503(metrics_path: Path) -> None:
    app, config = build(metrics_path, FakeGitHub())
    async with client_for(app, config) as client:
        response = await client.get("/api/code", params={"analysis_id": "a", "path": TOPIC_PATH})
    assert response.status_code == 503


async def test_code_requires_login(metrics_path: Path) -> None:
    store = InMemoryAnalysisStore()
    analysis_id = await add_analysis(store, USER_ID)
    app, config = build(metrics_path, FakeGitHub(), store)
    async with client_for(app, config, logged_in=False) as client:
        response = await client.get(
            "/api/code", params={"analysis_id": analysis_id, "path": TOPIC_PATH}
        )
    assert response.status_code == 401


async def test_code_too_large_is_422(metrics_path: Path) -> None:
    github = FakeGitHub()
    serve_code(github, b"x" * (MAX_FILE_BYTES + 1))
    store = InMemoryAnalysisStore()
    analysis_id = await add_analysis(store, USER_ID)
    app, config = build(metrics_path, github, store)
    async with client_for(app, config) as client:
        response = await client.get(
            "/api/code", params={"analysis_id": analysis_id, "path": TOPIC_PATH}
        )
    assert response.status_code == 422


@pytest.mark.parametrize(("status", "expected"), [(404, 404), (500, 502), (401, 401)])
async def test_code_github_errors(metrics_path: Path, status: int, expected: int) -> None:
    github = FakeGitHub()
    github.on(
        f"/repos/{SAMPLE.repo_full_name}/contents/{TOPIC_PATH}",
        lambda _r: httpx.Response(status),
    )
    store = InMemoryAnalysisStore()
    analysis_id = await add_analysis(store, USER_ID)
    app, config = build(metrics_path, github, store)
    async with client_for(app, config) as client:
        response = await client.get(
            "/api/code", params={"analysis_id": analysis_id, "path": TOPIC_PATH}
        )
    assert response.status_code == expected
    cleared = [h for h in response.headers.get_list("set-cookie") if h.startswith(SESSION_COOKIE)]
    assert bool(cleared) == (status == 401)


async def test_code_is_404_in_lab_mode(metrics_path: Path) -> None:
    github = FakeGitHub()
    store = InMemoryAnalysisStore()
    analysis_id = await add_analysis(store, USER_ID)
    app, config = build(metrics_path, github, store, lab_enabled=True)
    async with client_for(app, config) as client:
        response = await client.get(
            "/api/code", params={"analysis_id": analysis_id, "path": TOPIC_PATH}
        )
    assert response.status_code == 404
    assert github.requests == []


async def test_code_github_connection_error_is_502(metrics_path: Path) -> None:
    def refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    github = FakeGitHub()
    github.on(f"/repos/{SAMPLE.repo_full_name}/contents/{TOPIC_PATH}", refuse)
    store = InMemoryAnalysisStore()
    analysis_id = await add_analysis(store, USER_ID)
    app, config = build(metrics_path, github, store)
    async with client_for(app, config) as client:
        response = await client.get(
            "/api/code", params={"analysis_id": analysis_id, "path": TOPIC_PATH}
        )
    assert response.status_code == 502


async def test_repos_page_cap_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    github = FakeGitHub()
    many = [repo_json(i, f"octo/r{i:04d}") for i in range(600)]
    github.on("/user/installations", page_of([{"id": 1}], "installations"))
    github.on("/user/installations/1/repositories", page_of(many, "repositories"))
    repos = await GitHubAPI(github.client(), SecretStr(TOKEN)).list_repos()
    assert len(repos) == 500
    assert "github_api_page_cap" in caplog.text


# ---- GitHubAPI: 분석 요청용 (A가 /api/analyses에서 씀) ----


def api_with(github: FakeGitHub) -> GitHubAPI:
    return GitHubAPI(github.client(), SecretStr(TOKEN))


async def test_get_repo_and_head_sha() -> None:
    github = FakeGitHub()
    github.on("/repositories/10", lambda _r: httpx.Response(200, json=repo_json(10, "octo/zeta")))
    github.on(
        "/repos/octo/zeta/commits/main",
        lambda r: (
            httpx.Response(200, text="a" * 40)
            if r.headers["accept"] == "application/vnd.github.sha"
            else httpx.Response(415)
        ),
    )
    api = api_with(github)
    repo = await api.get_repo(10)
    assert repo == Repo(repo_id=10, full_name="octo/zeta", private=False, default_branch="main")
    assert repo is not None
    assert await api.head_sha(repo) == "a" * 40
    assert await api.get_repo(11) is None


async def test_head_sha_rejects_non_sha() -> None:
    github = FakeGitHub()
    github.on("/repos/octo/zeta/commits/main", lambda _r: httpx.Response(200, text="<html>"))
    with pytest.raises(GitHubUnavailableError):
        await api_with(github).head_sha(
            Repo(repo_id=10, full_name="octo/zeta", private=False, default_branch="main")
        )


async def test_repo_access_uses_only_verified_emails() -> None:
    github = FakeGitHub()
    github.on(
        "/user/emails",
        lambda _r: httpx.Response(
            200,
            json=[
                {"email": "me@example.com", "verified": True, "primary": True},
                {"email": "fake@example.com", "verified": False, "primary": False},
            ],
        ),
    )
    access = await repo_access(
        api_with(github), token=SecretStr(TOKEN), user_id=USER_ID, login="octo"
    )
    assert access.emails == ("me@example.com",)
    assert (access.github_id, access.login) == (4242, "octo")
    assert TOKEN not in repr(access)


@pytest.mark.parametrize("status", [403, 404])
async def test_repo_access_without_email_permission_has_no_emails(status: int) -> None:
    # App에 "Email addresses" 읽기 권한이 없으면 GitHub가 403이나 404를 줍니다. noreply 주소로만 맞춥니다.
    github = FakeGitHub()
    github.on("/user/emails", lambda _r: httpx.Response(status))
    access = await repo_access(
        api_with(github), token=SecretStr(TOKEN), user_id=USER_ID, login="octo"
    )
    assert access.emails == ()


async def test_unauthorized_is_reported() -> None:
    api = GitHubAPI(FakeGitHub().client(), SecretStr("ghu_wrong"))
    with pytest.raises(GitHubUnauthorizedError):
        await api.list_repos()


@pytest.mark.parametrize("path", ["", "a//b", "./a", "a/../b", "a/"])
async def test_file_text_rejects_bad_paths(path: str) -> None:
    with pytest.raises(ValueError):
        await api_with(FakeGitHub()).file_text(SAMPLE.repo_full_name, SAMPLE.commit_sha, path)
