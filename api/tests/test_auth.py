"""GitHub 로그인(#18 1단계) 테스트. GitHub는 httpx.MockTransport로 흉내 내고 실제로 부르지 않습니다."""

import base64
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr, ValidationError

from gitinsight_api.brain.factory import build_fixture_brain, build_report_service
from gitinsight_api.github_oauth import (
    REVOKE_URL_TEMPLATE,
    TOKEN_URL,
    USER_URL,
    code_challenge,
)
from gitinsight_api.llm.fake import FakeLLM
from gitinsight_api.login_cookie import (
    OAUTH_COOKIE,
    SESSION_COOKIE,
    LoginSession,
    OAuthAttempt,
    open_oauth,
    open_session,
    seal_oauth,
    seal_session,
)
from gitinsight_api.main import create_app
from gitinsight_api.settings import Settings
from gitinsight_contracts.api import Me
from gitinsight_contracts.fixtures import load_analysis

ANALYSIS_ID = "demo-gitinsight-agent"
WEB = "https://gi.example"
CALLBACK = f"{WEB}/api/auth/github/callback"
CLIENT_ID = "Iv23liTestClientId"
CLIENT_SECRET = "test-client-secret-value"
SESSION_SECRET = "s" * 40
CODE = "test-oauth-code-1234"
TOKEN = "ghu_TestUserAccessToken0000000000000000"
REVOKE_URL = REVOKE_URL_TEMPLATE.format(client_id=CLIENT_ID)
GITHUB_USER: dict[str, Any] = {
    "id": 4242,
    "login": "octo",
    "avatar_url": "https://avatars.githubusercontent.com/u/4242?v=4",
}


def auth_settings(metrics_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "lab_enabled": False,
        "metrics_path": metrics_path,
        "github_client_id": CLIENT_ID,
        "github_client_secret": CLIENT_SECRET,
        "session_secret": SESSION_SECRET,
        "web_origin": WEB,
        "cookie_secure": True,
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


class FakeGitHub:
    """토큰 교환과 GET /user를 흉내 냅니다. 받은 요청을 기록합니다."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.token_response: Callable[[], httpx.Response] = lambda: httpx.Response(
            200,
            json={
                "access_token": TOKEN,
                "expires_in": 28800,
                "refresh_token": "ghr_TestRefreshToken",
                "refresh_token_expires_in": 15897600,
                "scope": "",
                "token_type": "bearer",
            },
        )
        self.user_response: Callable[[], httpx.Response] = lambda: httpx.Response(
            200, json=GITHUB_USER
        )
        self.revoke_response: Callable[[], httpx.Response] = lambda: httpx.Response(204)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if request.method == "POST" and url == TOKEN_URL:
            return self.token_response()
        if request.method == "GET" and url == USER_URL:
            if request.headers.get("Authorization") != f"Bearer {TOKEN}":
                return httpx.Response(401, json={"message": "Bad credentials"})
            return self.user_response()
        if request.method == "DELETE" and url == REVOKE_URL:
            return self.revoke_response()
        return httpx.Response(404)

    def revoke_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "DELETE"]

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def build_app(settings: Settings, github: FakeGitHub) -> FastAPI:
    fake = FakeLLM()
    brain = build_fixture_brain(settings, llm=fake, force_prepared=True)
    reports = build_report_service(settings, brain, load_analysis(), llm=fake, force_prepared=True)
    return create_app(settings, brain=brain, report_service=reports, github_http=github.client())


@asynccontextmanager
async def browser(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    # Secure 쿠키를 주고받도록 https 주소로 붙습니다. 리다이렉트는 따라가지 않고 직접 확인합니다.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://gi.example") as client:
        yield client


def set_cookie_headers(response: httpx.Response, name: str) -> list[str]:
    return [h for h in response.headers.get_list("set-cookie") if h.startswith(f"{name}=")]


def cookie_attrs(header: str) -> dict[str, str]:
    parsed = SimpleCookie()
    parsed.load(header)
    (morsel,) = parsed.values()
    return {key: str(morsel[key]) for key in morsel if morsel[key]} | {"value": morsel.value}


async def start_login(client: httpx.AsyncClient) -> dict[str, list[str]]:
    response = await client.get("/api/auth/github/login")
    assert response.status_code == 302
    return parse_qs(urlsplit(response.headers["location"]).query)


async def login(client: httpx.AsyncClient) -> httpx.Response:
    query = await start_login(client)
    return await client.get(
        "/api/auth/github/callback", params={"code": CODE, "state": query["state"][0]}
    )


def test_code_challenge_matches_rfc7636_example() -> None:
    # RFC 7636 부록 B의 예시 값입니다.
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert code_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


async def test_login_redirects_to_github_with_state_and_pkce(metrics_path: Path) -> None:
    settings = auth_settings(metrics_path)
    async with browser(build_app(settings, FakeGitHub())) as client:
        response = await client.get("/api/auth/github/login")

    assert response.status_code == 302
    assert response.headers["cache-control"] == "no-store"
    location = urlsplit(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        "https://github.com/login/oauth/authorize"
    )
    query = parse_qs(location.query)
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == [CALLBACK]
    assert query["code_challenge_method"] == ["S256"]
    assert "scope" not in query

    (header,) = set_cookie_headers(response, OAUTH_COOKIE)
    attrs = cookie_attrs(header)
    assert attrs["path"] == "/api/auth/github"
    assert "httponly" in attrs
    assert "secure" in attrs
    assert attrs["samesite"].lower() == "lax"
    assert attrs["max-age"] == "600"
    attempt = open_oauth(settings, attrs["value"])
    assert attempt is not None
    assert query["state"] == [attempt.state]
    assert query["code_challenge"] == [code_challenge(attempt.code_verifier)]
    # 쿠키에 state와 verifier가 평문으로 들어가지 않습니다.
    assert attempt.state not in attrs["value"]
    assert attempt.code_verifier not in attrs["value"]


async def test_each_login_gets_new_state(metrics_path: Path) -> None:
    async with browser(build_app(auth_settings(metrics_path), FakeGitHub())) as client:
        first = await start_login(client)
        second = await start_login(client)
    assert first["state"] != second["state"]
    assert first["code_challenge"] != second["code_challenge"]


@pytest.mark.parametrize("missing", ["github_client_id", "github_client_secret", "session_secret"])
async def test_login_is_503_when_not_configured(metrics_path: Path, missing: str) -> None:
    settings = auth_settings(metrics_path, **{missing: ""})
    async with browser(build_app(settings, FakeGitHub())) as client:
        login_response = await client.get("/api/auth/github/login")
        callback = await client.get("/api/auth/github/callback", params={"code": CODE})

    assert login_response.status_code == callback.status_code == 503
    assert login_response.json()["detail"] == "GitHub 로그인이 설정되지 않았습니다"


async def test_callback_exchanges_code_and_sets_login_cookie(metrics_path: Path) -> None:
    settings = auth_settings(metrics_path)
    github = FakeGitHub()
    async with browser(build_app(settings, github)) as client:
        query = await start_login(client)
        before = int(time.time())
        response = await client.get(
            "/api/auth/github/callback", params={"code": CODE, "state": query["state"][0]}
        )
        me = await client.get("/api/me")

    assert response.status_code == 302
    assert response.headers["location"] == f"{WEB}/"
    assert response.headers["cache-control"] == "no-store"

    token_request, user_request = github.requests
    form = parse_qs(token_request.content.decode())
    assert token_request.headers["accept"] == "application/json"
    assert form["client_id"] == [CLIENT_ID]
    assert form["client_secret"] == [CLIENT_SECRET]
    assert form["code"] == [CODE]
    assert form["redirect_uri"] == [CALLBACK]
    assert code_challenge(form["code_verifier"][0]) == query["code_challenge"][0]
    assert user_request.headers["authorization"] == f"Bearer {TOKEN}"
    assert user_request.headers["x-github-api-version"]

    (session_header,) = set_cookie_headers(response, SESSION_COOKIE)
    attrs = cookie_attrs(session_header)
    assert attrs["path"] == "/"
    assert "httponly" in attrs
    assert "secure" in attrs
    assert attrs["samesite"].lower() == "lax"
    assert attrs["max-age"] == "28800"
    assert TOKEN not in attrs["value"]
    session = open_session(settings, attrs["value"])
    assert session is not None
    assert session.user_id == "4242"
    assert session.login == "octo"
    assert session.github_token.get_secret_value() == TOKEN
    assert before + 28800 <= session.expires_at <= int(time.time()) + 28800

    # 로그인 시도 쿠키는 지웁니다.
    (oauth_header,) = set_cookie_headers(response, OAUTH_COOKIE)
    assert cookie_attrs(oauth_header)["max-age"] == "0"

    assert me.status_code == 200
    assert me.headers["cache-control"] == "no-store"
    assert Me.model_validate(me.json()) == Me(
        user_id="4242", login="octo", avatar_url=GITHUB_USER["avatar_url"]
    )


async def test_session_follows_shorter_token_lifetime(metrics_path: Path) -> None:
    github = FakeGitHub()
    github.token_response = lambda: httpx.Response(
        200, json={"access_token": TOKEN, "expires_in": 3600, "token_type": "bearer"}
    )
    async with browser(build_app(auth_settings(metrics_path), github)) as client:
        response = await login(client)
    (header,) = set_cookie_headers(response, SESSION_COOKIE)
    assert cookie_attrs(header)["max-age"] == "3600"


@pytest.mark.parametrize("expires", [None, 0, -5])
async def test_non_expiring_token_is_capped_at_eight_hours(
    metrics_path: Path, expires: int | None
) -> None:
    github = FakeGitHub()
    body: dict[str, Any] = {"access_token": TOKEN, "scope": "", "token_type": "bearer"}
    if expires is not None:
        body["expires_in"] = expires
    github.token_response = lambda: httpx.Response(200, json=body)
    async with browser(build_app(auth_settings(metrics_path), github)) as client:
        response = await login(client)
    (header,) = set_cookie_headers(response, SESSION_COOKIE)
    assert cookie_attrs(header)["max-age"] == "28800"


async def test_cookie_secure_can_be_turned_off_for_local_dev(metrics_path: Path) -> None:
    settings = auth_settings(metrics_path, cookie_secure=False, web_origin="http://localhost:3000")
    app = build_app(settings, FakeGitHub())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:3000") as client:
        response = await login(client)
        me = await client.get("/api/me")
    (oauth_header,) = set_cookie_headers(response, OAUTH_COOKIE)
    (session_header,) = set_cookie_headers(response, SESSION_COOKIE)
    assert "secure" not in oauth_header.lower()
    assert "secure" not in session_header.lower()
    assert me.status_code == 200


async def test_callback_rejects_wrong_state_without_calling_github(metrics_path: Path) -> None:
    github = FakeGitHub()
    async with browser(build_app(auth_settings(metrics_path), github)) as client:
        await start_login(client)
        response = await client.get(
            "/api/auth/github/callback", params={"code": CODE, "state": "forged"}
        )
        me = await client.get("/api/me")

    assert response.status_code == 302
    assert response.headers["location"] == f"{WEB}/?login_error=invalid_state"
    assert github.requests == []
    assert set_cookie_headers(response, SESSION_COOKIE) == []
    # 위조 콜백은 진행 중인 로그인 시도 쿠키를 지우지 못합니다.
    assert set_cookie_headers(response, OAUTH_COOKIE) == []
    assert me.status_code == 401


async def test_callback_rejects_missing_oauth_cookie(metrics_path: Path) -> None:
    # 다른 브라우저에서 시작한 로그인(또는 공격자가 만든 링크)의 콜백입니다.
    github = FakeGitHub()
    app = build_app(auth_settings(metrics_path), github)
    async with browser(app) as attacker:
        query = await start_login(attacker)
    async with browser(app) as victim:
        response = await victim.get(
            "/api/auth/github/callback", params={"code": CODE, "state": query["state"][0]}
        )
    assert response.headers["location"] == f"{WEB}/?login_error=invalid_state"
    assert github.requests == []


async def test_oauth_cookie_expires_after_ten_minutes(metrics_path: Path) -> None:
    settings = auth_settings(metrics_path)
    attempt = OAuthAttempt(state="st", code_verifier="v" * 64)
    old = seal_oauth(settings, attempt, now=time.time() - 601)
    assert open_oauth(settings, old) is None
    assert open_oauth(settings, seal_oauth(settings, attempt)) == attempt


async def test_browser_drops_oauth_cookie_after_callback(metrics_path: Path) -> None:
    # 서버는 쿠키를 지우라고 보낼 뿐 한 번 쓴 값을 기억하지 않습니다(저장소 없음).
    # 같은 값을 10분 안에 다시 보내는 재사용은 GitHub code가 한 번만 쓰이고 PKCE verifier에 묶여 막힙니다.
    github = FakeGitHub()
    async with browser(build_app(auth_settings(metrics_path), github)) as client:
        query = await start_login(client)
        params = {"code": CODE, "state": query["state"][0]}
        first = await client.get("/api/auth/github/callback", params=params)
        second = await client.get("/api/auth/github/callback", params=params)
    assert first.headers["location"] == f"{WEB}/"
    assert second.headers["location"] == f"{WEB}/?login_error=invalid_state"


async def test_callback_reports_user_denial(metrics_path: Path) -> None:
    github = FakeGitHub()
    async with browser(build_app(auth_settings(metrics_path), github)) as client:
        query = await start_login(client)
        response = await client.get(
            "/api/auth/github/callback",
            params={"error": "access_denied", "state": query["state"][0]},
        )
    assert response.headers["location"] == f"{WEB}/?login_error=denied"
    assert github.requests == []
    (oauth_header,) = set_cookie_headers(response, OAUTH_COOKIE)
    assert cookie_attrs(oauth_header)["max-age"] == "0"


async def test_callback_error_with_forged_state_is_invalid_state(metrics_path: Path) -> None:
    async with browser(build_app(auth_settings(metrics_path), FakeGitHub())) as client:
        await start_login(client)
        response = await client.get(
            "/api/auth/github/callback", params={"error": "access_denied", "state": "forged"}
        )
    assert response.headers["location"] == f"{WEB}/?login_error=invalid_state"
    assert set_cookie_headers(response, OAUTH_COOKIE) == []


async def test_callback_reports_other_github_errors_as_github(
    metrics_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    async with browser(build_app(auth_settings(metrics_path), FakeGitHub())) as client:
        query = await start_login(client)
        response = await client.get(
            "/api/auth/github/callback",
            params={"error": "redirect_uri_mismatch", "state": query["state"][0]},
        )
    assert response.headers["location"] == f"{WEB}/?login_error=github"
    assert "redirect_uri_mismatch" in caplog.text


async def test_callback_rejects_oversized_params(metrics_path: Path) -> None:
    github = FakeGitHub()
    async with browser(build_app(auth_settings(metrics_path), github)) as client:
        await start_login(client)
        long_state = await client.get(
            "/api/auth/github/callback", params={"code": CODE, "state": "s" * 513}
        )
        query = await start_login(client)
        long_code = await client.get(
            "/api/auth/github/callback", params={"code": "c" * 513, "state": query["state"][0]}
        )
    assert long_state.headers["location"] == f"{WEB}/?login_error=invalid_state"
    assert long_code.headers["location"] == f"{WEB}/?login_error=github"
    assert github.requests == []


async def test_non_https_avatar_is_dropped(metrics_path: Path) -> None:
    github = FakeGitHub()
    github.user_response = lambda: httpx.Response(
        200, json={**GITHUB_USER, "avatar_url": "javascript:alert(1)"}
    )
    async with browser(build_app(auth_settings(metrics_path), github)) as client:
        await login(client)
        me = await client.get("/api/me")
    assert me.json()["avatar_url"] == ""


async def test_callback_rejects_missing_code(metrics_path: Path) -> None:
    github = FakeGitHub()
    async with browser(build_app(auth_settings(metrics_path), github)) as client:
        query = await start_login(client)
        response = await client.get(
            "/api/auth/github/callback", params={"state": query["state"][0]}
        )
    assert response.headers["location"] == f"{WEB}/?login_error=github"
    assert github.requests == []


def _raise_connect_error() -> httpx.Response:
    raise httpx.ConnectError("connection refused")


@pytest.mark.parametrize(
    ("target", "make_response"),
    [
        ("token", lambda: httpx.Response(200, json={"error": "bad_verification_code"})),
        ("token", lambda: httpx.Response(500, text="oops")),
        ("token", lambda: httpx.Response(200, text="not json")),
        ("token", lambda: httpx.Response(200, json={"access_token": TOKEN, "token_type": "mac"})),
        ("token", _raise_connect_error),
        ("user", lambda: httpx.Response(401, json={"message": "Bad credentials"})),
        ("user", lambda: httpx.Response(200, json={"login": "no-id"})),
        ("user", _raise_connect_error),
    ],
)
async def test_github_failures_redirect_without_leaking_secrets(
    metrics_path: Path,
    caplog: pytest.LogCaptureFixture,
    target: str,
    make_response: Callable[[], httpx.Response],
) -> None:
    github = FakeGitHub()
    if target == "token":
        github.token_response = make_response
    else:
        github.user_response = make_response
    caplog.set_level(logging.DEBUG)
    async with browser(build_app(auth_settings(metrics_path), github)) as client:
        response = await login(client)
        me = await client.get("/api/me")

    assert response.status_code == 302
    assert response.headers["location"] == f"{WEB}/?login_error=github"
    assert set_cookie_headers(response, SESSION_COOKIE) == []
    assert me.status_code == 401
    for secret in (CODE, TOKEN, CLIENT_SECRET, SESSION_SECRET):
        assert secret not in caplog.text


async def test_successful_login_logs_no_secrets(
    metrics_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    async with browser(build_app(auth_settings(metrics_path), FakeGitHub())) as client:
        await login(client)
        await client.get("/api/me")
    for secret in (CODE, TOKEN, CLIENT_SECRET, SESSION_SECRET):
        assert secret not in caplog.text


def test_login_session_repr_hides_token() -> None:
    session = LoginSession(
        user_id="1", login="octo", avatar_url="", github_token=SecretStr(TOKEN), expires_at=0
    )
    assert TOKEN not in repr(session)
    assert TOKEN not in str(session)


def _session(expires_at: int) -> LoginSession:
    return LoginSession(
        user_id="4242",
        login="octo",
        avatar_url="",
        github_token=SecretStr(TOKEN),
        expires_at=expires_at,
    )


def _tamper(value: str) -> str:
    middle = len(value) // 2
    return value[:middle] + ("A" if value[middle] != "A" else "B") + value[middle + 1 :]


@pytest.mark.parametrize(
    "make_cookie",
    [
        lambda s: _tamper(seal_session(s, _session(int(time.time()) + 3600))),
        # 다른 SESSION_SECRET으로 만든 쿠키
        lambda s: seal_session(
            auth_settings(s.metrics_path, session_secret="o" * 40),
            _session(int(time.time()) + 3600),
        ),
        # 쿠키 안의 만료 시각이 지남
        lambda s: seal_session(s, _session(int(time.time()) - 1)),
        # 8시간보다 오래전에 만든 쿠키
        lambda s: seal_session(
            s, _session(int(time.time()) + 3600), now=time.time() - 8 * 3600 - 1
        ),
        # 로그인 시도 쿠키를 로그인 쿠키 자리에 넣음
        lambda s: seal_oauth(s, OAuthAttempt(state="st", code_verifier="v" * 64)),
        lambda _s: "not-a-cookie",
        lambda _s: "",
    ],
)
async def test_invalid_login_cookie_is_401(
    metrics_path: Path, make_cookie: Callable[[Settings], str]
) -> None:
    settings = auth_settings(metrics_path)
    async with browser(build_app(settings, FakeGitHub())) as client:
        client.cookies.set(SESSION_COOKIE, make_cookie(settings))
        me = await client.get("/api/me")
        created = await client.post(
            "/api/sessions", json={"analysis_id": ANALYSIS_ID, "mode": "text"}
        )
    assert me.status_code == created.status_code == 401
    assert me.json()["detail"] == "로그인이 필요합니다"


async def test_login_cookie_without_session_secret_is_401(metrics_path: Path) -> None:
    signed = seal_session(auth_settings(metrics_path), _session(int(time.time()) + 3600))
    settings = auth_settings(metrics_path, session_secret="")
    async with browser(build_app(settings, FakeGitHub())) as client:
        client.cookies.set(SESSION_COOKIE, signed)
        me = await client.get("/api/me")
    assert me.status_code == 401


async def test_logout_clears_login_cookie(metrics_path: Path) -> None:
    async with browser(build_app(auth_settings(metrics_path), FakeGitHub())) as client:
        await login(client)
        assert (await client.get("/api/me")).status_code == 200
        response = await client.post("/api/auth/logout")
        me = await client.get("/api/me")

    assert response.status_code == 204
    (header,) = set_cookie_headers(response, SESSION_COOKIE)
    attrs = cookie_attrs(header)
    assert attrs["max-age"] == "0"
    assert attrs["path"] == "/"
    assert me.status_code == 401


async def test_logout_without_login_is_204(metrics_path: Path) -> None:
    github = FakeGitHub()
    async with browser(build_app(auth_settings(metrics_path), github)) as client:
        response = await client.post("/api/auth/logout")
    assert response.status_code == 204
    assert github.requests == []


async def test_logout_revokes_github_token(metrics_path: Path) -> None:
    github = FakeGitHub()
    async with browser(build_app(auth_settings(metrics_path), github)) as client:
        await login(client)
        response = await client.post("/api/auth/logout")

    assert response.status_code == 204
    (revoke,) = github.revoke_requests()
    assert str(revoke.url) == REVOKE_URL
    # App Client ID와 secret으로 Basic 인증하고, 폐기할 토큰은 본문에 넣습니다.
    basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    assert revoke.headers["Authorization"] == f"Basic {basic}"
    assert json.loads(revoke.content) == {"access_token": TOKEN}
    assert TOKEN not in str(revoke.url)


@pytest.mark.parametrize(
    "make_response",
    [
        lambda: httpx.Response(404, json={"message": "Not Found"}),
        lambda: httpx.Response(422, json={"message": "Validation Failed"}),
        lambda: httpx.Response(500, text="oops"),
        _raise_connect_error,
    ],
)
async def test_logout_clears_cookie_even_if_revoke_fails(
    metrics_path: Path,
    caplog: pytest.LogCaptureFixture,
    make_response: Callable[[], httpx.Response],
) -> None:
    github = FakeGitHub()
    github.revoke_response = make_response
    caplog.set_level(logging.DEBUG)
    async with browser(build_app(auth_settings(metrics_path), github)) as client:
        await login(client)
        response = await client.post("/api/auth/logout")
        me = await client.get("/api/me")

    assert response.status_code == 204
    (header,) = set_cookie_headers(response, SESSION_COOKIE)
    assert cookie_attrs(header)["max-age"] == "0"
    assert me.status_code == 401
    assert len(github.revoke_requests()) == 1
    for secret in (CODE, TOKEN, CLIENT_SECRET, SESSION_SECRET):
        assert secret not in caplog.text


async def test_logout_with_invalid_cookie_does_not_call_github(metrics_path: Path) -> None:
    github = FakeGitHub()
    async with browser(build_app(auth_settings(metrics_path), github)) as client:
        client.cookies.set(SESSION_COOKIE, "not-a-cookie")
        response = await client.post("/api/auth/logout")
    assert response.status_code == 204
    assert github.requests == []


async def test_logged_in_user_owns_sessions_and_others_get_403(metrics_path: Path) -> None:
    settings = auth_settings(metrics_path)
    app = build_app(settings, FakeGitHub())
    async with browser(app) as alice:
        await login(alice)
        created = await alice.post(
            "/api/sessions", json={"analysis_id": ANALYSIS_ID, "mode": "text"}
        )
        assert created.status_code == 201
        session_id = created.json()["session_id"]
        turn = {"turn_id": "00000000-0000-4000-8000-000000000001", "transcript": "답변입니다"}
        own = await alice.post(f"/api/sessions/{session_id}/turns", json=turn | {"source": "text"})

    other = LoginSession(
        user_id="9999",
        login="mallory",
        avatar_url="",
        github_token=SecretStr("ghu_other"),
        expires_at=int(time.time()) + 3600,
    )
    async with browser(app) as mallory:
        mallory.cookies.set(SESSION_COOKIE, seal_session(settings, other))
        stolen = await mallory.post(
            f"/api/sessions/{session_id}/turns",
            json={
                "turn_id": "00000000-0000-4000-8000-000000000002",
                "transcript": "답변입니다",
                "source": "text",
            },
        )

    assert own.status_code == 200
    assert stolen.status_code == 403


async def test_lab_mode_still_uses_lab_user(metrics_path: Path) -> None:
    settings = auth_settings(metrics_path, lab_enabled=True)
    app = create_app(settings, github_http=FakeGitHub().client())
    async with browser(app) as client:
        me = await client.get("/api/me")
    assert me.status_code == 200
    assert me.json() == {"user_id": "lab", "login": "lab", "avatar_url": ""}


def test_settings_reject_short_session_secret(metrics_path: Path) -> None:
    with pytest.raises(ValidationError) as excinfo:
        auth_settings(metrics_path, session_secret="short-secret")
    assert "short-secret" not in str(excinfo.value)


@pytest.mark.parametrize(
    "origin", ["gi.example", "ftp://gi.example", "https://gi.example/app", "https://gi.example?x=1"]
)
def test_settings_reject_bad_web_origin(metrics_path: Path, origin: str) -> None:
    with pytest.raises(ValidationError):
        auth_settings(metrics_path, web_origin=origin)


def test_secure_cookie_requires_https_origin(metrics_path: Path) -> None:
    with pytest.raises(ValidationError):
        auth_settings(metrics_path, web_origin="http://gi.example")
    assert auth_settings(metrics_path, web_origin="http://localhost:3000").cookie_secure
    insecure = auth_settings(
        metrics_path, web_origin="http://192.168.0.5:3000", cookie_secure=False
    )
    assert insecure.web_origin == "http://192.168.0.5:3000"


def test_settings_normalize_web_origin(metrics_path: Path) -> None:
    settings = auth_settings(metrics_path, web_origin=" https://gi.example/ ")
    assert settings.web_origin == "https://gi.example"
    assert settings.github_callback_url() == CALLBACK


def test_settings_secrets_are_hidden(metrics_path: Path) -> None:
    settings = auth_settings(metrics_path)
    text = repr(settings) + json.dumps(settings.model_dump(mode="json"))
    assert CLIENT_SECRET not in text
    assert SESSION_SECRET not in text


def test_access_log_hides_callback_query(metrics_path: Path) -> None:
    create_app(auth_settings(metrics_path), github_http=FakeGitHub().client())
    create_app(auth_settings(metrics_path), github_http=FakeGitHub().client())
    access = logging.getLogger("uvicorn.access")
    assert len([f for f in access.filters if type(f).__name__ == "_RedactCallbackQuery"]) == 1

    def formatted(path: str) -> str:
        record = logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            __file__,
            0,
            '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1:1", "GET", path, "1.1", 302),
            None,
        )
        assert access.filter(record)
        return record.getMessage()

    message = formatted(f"/api/auth/github/callback?code={CODE}&state=abc")
    assert CODE not in message
    assert "/api/auth/github/callback?[redacted]" in message
    assert "/api/me?x=1" in formatted("/api/me?x=1")
