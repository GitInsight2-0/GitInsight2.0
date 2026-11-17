"""GitHub 로그인 라우트 (#18 1단계).

GET  /api/auth/github/login     GitHub 인증 화면으로 보냅니다.
GET  /api/auth/github/callback  GitHub가 돌려보내는 곳. 로그인 쿠키를 굽고 웹으로 보냅니다.
POST /api/auth/logout           로그인 쿠키를 지웁니다.
GET  /api/me                    로그인 사용자를 돌려줍니다.

콜백 실패는 웹 첫 화면으로 보내며 ?login_error=<코드>를 붙입니다.
- denied: 사용자가 GitHub 화면에서 거절함 (error=access_denied)
- invalid_state: 로그인 시도 쿠키가 없거나, 만료됐거나, state가 다름 (위조 요청 또는 10분 초과)
- github: GitHub 토큰 교환이나 사용자 조회 실패, 또는 GitHub가 access_denied 밖의 오류를 돌려줌
"""

import hmac
import logging
import time

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import SecretStr

from gitinsight_api.github_oauth import (
    GitHubAuthError,
    GitHubOAuth,
    authorize_url,
    new_code_verifier,
    new_state,
    short_error_code,
)
from gitinsight_api.login_cookie import (
    OAUTH_COOKIE,
    SESSION_MAX_AGE_S,
    LoginSession,
    OAuthAttempt,
    clear_oauth_cookie,
    clear_session_cookie,
    open_oauth,
    seal_oauth,
    seal_session,
    set_oauth_cookie,
    set_session_cookie,
)
from gitinsight_api.routes.deps import UserDep
from gitinsight_api.settings import Settings
from gitinsight_contracts.api import Me

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

# GitHub가 주는 code와 state는 이보다 훨씬 짧습니다. 비정상 길이는 확인 없이 거절합니다.
_MAX_PARAM_LEN = 512
_NO_STORE = {"Cache-Control": "no-store"}


CALLBACK_PATH = "/api/auth/github/callback"


class _RedactCallbackQuery(logging.Filter):
    """uvicorn 접근 로그에서 콜백 주소의 쿼리(code, state)를 지웁니다."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        # uvicorn 접근 로그 인자: (클라이언트 주소, 메서드, 경로+쿼리, HTTP 버전, 상태 코드)
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            path = args[2]
            if path.startswith(f"{CALLBACK_PATH}?"):
                record.args = (*args[:2], f"{CALLBACK_PATH}?[redacted]", *args[3:])
        return True


def install_access_log_redaction() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _RedactCallbackQuery) for f in access.filters):
        access.addFilter(_RedactCallbackQuery())


def _login_config(request: Request) -> tuple[Settings, str, SecretStr]:
    """설정과 GitHub App Client ID·secret을 돌려줍니다. 하나라도 없으면 503입니다."""
    settings: Settings = request.app.state.settings
    client_id = settings.github_client_id
    client_secret = settings.github_client_secret
    if client_id is None or client_secret is None or settings.session_secret is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "GitHub 로그인이 설정되지 않았습니다"
        )
    return settings, client_id, client_secret


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND, headers=_NO_STORE)


@router.get("/api/auth/github/login")
async def github_login(request: Request) -> RedirectResponse:
    settings, client_id, _ = _login_config(request)
    attempt = OAuthAttempt(state=new_state(), code_verifier=new_code_verifier())
    response = _redirect(
        authorize_url(
            client_id=client_id,
            redirect_uri=settings.github_callback_url(),
            state=attempt.state,
            code_verifier=attempt.code_verifier,
        )
    )
    set_oauth_cookie(response, settings, seal_oauth(settings, attempt))
    return response


@router.get(CALLBACK_PATH)
async def github_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    settings, client_id, client_secret = _login_config(request)
    sealed = request.cookies.get(OAUTH_COOKIE)
    attempt = open_oauth(settings, sealed) if sealed else None

    def fail(reason: str) -> RedirectResponse:
        response = _redirect(f"{settings.web_origin}/?login_error={reason}")
        # state가 맞은 뒤에만 부릅니다. 그래서 위조 콜백이 진행 중인 로그인 시도 쿠키를 지우지 못합니다.
        clear_oauth_cookie(response, settings)
        return response

    # state를 먼저 확인합니다. error가 붙은 콜백도 이 브라우저가 시작한 로그인일 때만 믿습니다.
    if (
        attempt is None
        or not state
        or len(state) > _MAX_PARAM_LEN
        or not hmac.compare_digest(state.encode(), attempt.state.encode())
    ):
        logger.info("GitHub 로그인 콜백 state 불일치 또는 시도 쿠키 없음")
        return _redirect(f"{settings.web_origin}/?login_error=invalid_state")
    if error is not None:
        if error == "access_denied":
            return fail("denied")
        # redirect_uri_mismatch, application_suspended 같은 설정 문제입니다. 오류 코드만 남깁니다.
        logger.warning("GitHub 로그인 콜백 오류: %s", short_error_code(error))
        return fail("github")
    if not code or len(code) > _MAX_PARAM_LEN:
        return fail("github")

    client: httpx.AsyncClient = request.app.state.github_http
    github = GitHubOAuth(client, client_id=client_id, client_secret=client_secret)
    try:
        token = await github.exchange_code(
            code=code,
            redirect_uri=settings.github_callback_url(),
            code_verifier=attempt.code_verifier,
        )
        user = await github.fetch_user(token.access_token)
    except GitHubAuthError:
        return fail("github")

    now = int(time.time())
    lifetime = min(token.expires_in or SESSION_MAX_AGE_S, SESSION_MAX_AGE_S)
    session = LoginSession(
        user_id=user.user_id,
        login=user.login,
        avatar_url=user.avatar_url,
        github_token=token.access_token,
        expires_at=now + lifetime,
    )
    response = _redirect(f"{settings.web_origin}/")
    clear_oauth_cookie(response, settings)
    set_session_cookie(response, settings, seal_session(settings, session, now=now), lifetime)
    logger.info("GitHub 로그인 성공: user_id=%s", user.user_id)
    return response


@router.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request) -> Response:
    # 로그인 쿠키가 없어도 204입니다. 서버에 세션 저장소가 없어 지우는 것은 브라우저 쿠키뿐입니다.
    settings: Settings = request.app.state.settings
    response = Response(status_code=status.HTTP_204_NO_CONTENT, headers=_NO_STORE)
    clear_session_cookie(response, settings)
    return response


@router.get("/api/me")
async def me(user: UserDep, response: Response) -> Me:
    response.headers["Cache-Control"] = "no-store"
    return Me(user_id=user.user_id, login=user.login, avatar_url=user.avatar_url)
