"""분석 요청·상태 API (#12). GitHub는 httpx.MockTransport로 흉내 내고, 실행기는 기록만 하는 가짜입니다."""

import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy.exc import ProgrammingError

from gitinsight_api.analyses.service import NO_CONTEXT, LaunchContext
from gitinsight_api.analyses.store import (
    AnalysisKey,
    AnalysisRecord,
    AnalysisStore,
    InMemoryAnalysisStore,
)
from gitinsight_api.login_cookie import SESSION_COOKIE, LoginSession, seal_session
from gitinsight_api.main import create_app
from gitinsight_api.settings import Settings
from gitinsight_contracts.fixtures import load_analysis

TOKEN = "ghu_TestUserAccessToken0000000000000000"
REPO_ID = 777
FULL_NAME = "octo/private-repo"
SHA = "b" * 40
USER_ID = "4242"


def settings_for(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "lab_enabled": False,
        "metrics_path": tmp_path / "m.jsonl",
        "session_secret": "s" * 40,
        "brain_force_prepared": True,
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


class FakeGitHub:
    """GET /repositories/{id}와 GET /repos/{name}/commits/{branch}를 흉내 냅니다."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.default_branch = "main"
        self.repo_status = 200
        self.commit_status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"message": "Bad credentials"})
        path = request.url.raw_path.decode()
        if path == f"/repositories/{REPO_ID}":
            if self.repo_status != 200:
                return httpx.Response(self.repo_status, json={"message": "x"})
            return httpx.Response(
                200,
                json={"id": REPO_ID, "full_name": FULL_NAME, "default_branch": self.default_branch},
            )
        if path.startswith(f"/repos/{FULL_NAME}/commits/"):
            if self.commit_status != 200:
                return httpx.Response(self.commit_status, json={"message": "x"})
            return httpx.Response(200, text=SHA)
        return httpx.Response(404, json={"message": "Not Found"})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


class FakeLauncher:
    def __init__(self, error: Exception | None = None) -> None:
        self.launched: list[tuple[AnalysisRecord, LaunchContext]] = []
        self.error = error
        self.events: list[str] = []

    async def launch(self, record: AnalysisRecord, context: LaunchContext = NO_CONTEXT) -> None:
        self.launched.append((record, context))
        if self.error is not None:
            raise self.error

    async def recover(self) -> int:
        self.events.append("recover")
        return 0

    async def aclose(self) -> None:
        self.events.append("aclose")


class Harness:
    def __init__(self, tmp_path: Path, *, launcher: FakeLauncher | None = None) -> None:
        self.settings = settings_for(tmp_path)
        self.store = InMemoryAnalysisStore()
        self.github = FakeGitHub()
        self.launcher = launcher or FakeLauncher()
        self.app = self.build(lambda _store: self.launcher)

    def build(self, factory: Callable[[AnalysisStore], FakeLauncher] | None) -> FastAPI:
        return create_app(
            self.settings,
            analysis_store=self.store,
            github_http=self.github.client(),
            launcher_factory=factory,
        )

    def cookie(self, user_id: str = USER_ID, login: str = "octo") -> str:
        session = LoginSession(
            user_id=user_id,
            login=login,
            avatar_url="",
            github_token=SecretStr(TOKEN),
            expires_at=int(time.time()) + 3600,
        )
        return seal_session(self.settings, session)

    @asynccontextmanager
    async def client(self, cookie: str | None = None) -> AsyncIterator[httpx.AsyncClient]:
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            if cookie is not None:
                client.cookies.set(SESSION_COOKIE, cookie)
            yield client


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


async def test_request_resolves_repo_with_user_token_and_launches_once(harness: Harness) -> None:
    async with harness.client(harness.cookie()) as client:
        first = await client.post("/api/analyses", json={"repo_id": REPO_ID})
        again = await client.post("/api/analyses", json={"repo_id": REPO_ID})

    assert first.status_code == 202, first.text
    body = first.json()
    assert (body["status"], body["cached"]) == ("queued", False)
    assert again.json()["analysis_id"] == body["analysis_id"]
    # 같은 (레포, 커밋, 요청자)라 실행은 한 번입니다.
    [(record, context)] = harness.launcher.launched
    assert (record.key.repo_full_name, record.key.commit_sha, record.key.user_id) == (
        FULL_NAME,
        SHA,
        USER_ID,
    )
    assert context.github_token is not None
    assert context.github_token.get_secret_value() == TOKEN
    assert context.login == "octo"
    # 응답에는 토큰이 없습니다.
    assert TOKEN not in first.text


async def test_state_is_visible_only_to_requester(harness: Harness) -> None:
    async with harness.client(harness.cookie()) as client:
        created = (await client.post("/api/analyses", json={"repo_id": REPO_ID})).json()
        mine = await client.get(f"/api/analyses/{created['analysis_id']}")
        missing = await client.get("/api/analyses/nope")
    async with harness.client(harness.cookie(user_id="9999", login="other")) as other:
        theirs = await other.get(f"/api/analyses/{created['analysis_id']}")

    assert mine.status_code == 200
    assert mine.json()["status"] == "queued"
    assert (missing.status_code, theirs.status_code) == (404, 404)


async def test_done_analysis_is_returned_as_cached(harness: Harness) -> None:
    async with harness.client(harness.cookie()) as client:
        created = (await client.post("/api/analyses", json={"repo_id": REPO_ID})).json()
        result = load_analysis().model_copy(
            update={
                "analysis_id": created["analysis_id"],
                "repo_full_name": FULL_NAME,
                "commit_sha": SHA,
            }
        )
        await harness.store.save_result(created["analysis_id"], result)
        again = await client.post("/api/analyses", json={"repo_id": REPO_ID})
        state = await client.get(f"/api/analyses/{created['analysis_id']}")

    assert again.json() == {
        "analysis_id": created["analysis_id"],
        "status": "done",
        "cached": True,
    }
    assert (state.json()["status"], state.json()["progress"]) == ("done", 1.0)
    assert len(harness.launcher.launched) == 1


async def test_branch_name_with_slash_is_one_path_segment(harness: Harness) -> None:
    harness.github.default_branch = "release/v1"
    async with harness.client(harness.cookie()) as client:
        response = await client.post("/api/analyses", json={"repo_id": REPO_ID})

    assert response.status_code == 202
    commit_request = harness.github.requests[-1]
    assert commit_request.url.raw_path.decode().endswith("/commits/release%2Fv1")
    assert commit_request.headers["Accept"] == "application/vnd.github.sha"


@pytest.mark.parametrize(
    ("where", "github_status", "expected"),
    [
        ("repo", 404, 404),  # 없거나 App이 설치되지 않은 레포
        ("repo", 403, 404),
        ("repo", 401, 401),  # 토큰 만료: 다시 로그인
        ("repo", 500, 502),
        ("commit", 409, 422),  # 커밋이 없는 빈 레포
        ("commit", 404, 404),
    ],
)
async def test_github_errors(
    harness: Harness, where: str, github_status: int, expected: int
) -> None:
    if where == "repo":
        harness.github.repo_status = github_status
    else:
        harness.github.commit_status = github_status
    async with harness.client(harness.cookie()) as client:
        response = await client.post("/api/analyses", json={"repo_id": REPO_ID})

    assert response.status_code == expected, response.text
    assert harness.launcher.launched == []
    assert harness.store._records == {}


async def test_login_is_required(harness: Harness) -> None:
    async with harness.client() as client:
        post = await client.post("/api/analyses", json={"repo_id": REPO_ID})
        get = await client.get("/api/analyses/x")

    assert (post.status_code, get.status_code) == (401, 401)
    assert harness.github.requests == []


async def test_launch_failure_is_503_and_can_be_retried(tmp_path: Path) -> None:
    harness = Harness(tmp_path, launcher=FakeLauncher(error=ConnectionError("down")))
    async with harness.client(harness.cookie()) as client:
        failed = await client.post("/api/analyses", json={"repo_id": REPO_ID})
        harness.launcher.error = None
        retry = await client.post("/api/analyses", json={"repo_id": REPO_ID})

    assert failed.status_code == 503
    assert retry.status_code == 202
    assert len(harness.launcher.launched) == 2


async def test_missing_table_is_500_with_reason(harness: Harness) -> None:
    # 첫 배포에서 마이그레이션이 빠졌을 때 본문 없는 500이 나갔습니다 (#101).
    async def missing_table(_key: AnalysisKey) -> tuple[AnalysisRecord, bool]:
        raise ProgrammingError("INSERT INTO analyses", {}, Exception("relation does not exist"))

    harness.store.get_or_create = missing_table  # type: ignore[method-assign]
    async with harness.client(harness.cookie()) as client:
        response = await client.post("/api/analyses", json={"repo_id": REPO_ID})

    assert response.status_code == 500
    assert response.json() == {
        "detail": "데이터베이스 오류로 처리하지 못했습니다. 관리자에게 알려 주세요"
    }
    assert harness.launcher.launched == []


async def test_without_launcher_requests_are_503(harness: Harness) -> None:
    harness.app = harness.build(None)
    async with harness.client(harness.cookie()) as client:
        response = await client.post("/api/analyses", json={"repo_id": REPO_ID})

    assert response.status_code == 503
    assert harness.github.requests == []


async def test_launcher_recovers_on_startup_and_closes_on_shutdown(harness: Harness) -> None:
    async with harness.app.router.lifespan_context(harness.app):
        assert harness.launcher.events == ["recover"]

    assert harness.launcher.events == ["recover", "aclose"]
