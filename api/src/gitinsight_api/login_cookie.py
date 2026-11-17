"""로그인 쿠키 (#18). DB 없이 암호화한 쿠키에 로그인 정보를 담습니다.

쿠키는 두 가지입니다.
- 로그인 쿠키(gi_session): GitHub 사용자 ID, login, 아바타 주소, 사용자 토큰, 만료 시각
- 로그인 시도 쿠키(gi_oauth): OAuth state와 PKCE code_verifier. 콜백에서 확인한 뒤 지웁니다.

둘 다 Fernet(AES-128-CBC + HMAC-SHA256)으로 암호화하므로 브라우저가 내용을 읽거나 고칠 수 없습니다.
키는 SESSION_SECRET에서 쿠키 종류별로 따로 만들어, 한 쿠키를 다른 쿠키 자리에 넣어도 열리지 않습니다.
데모 한정 방식입니다. 서버에서 특정 사용자의 로그인을 끊을 수 없으므로 해커톤 뒤에는 DB 세션으로 바꿉니다.
"""

import base64
import json
import time
from dataclasses import dataclass, field
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import Response
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from gitinsight_api.settings import Settings

SESSION_COOKIE = "gi_session"
OAUTH_COOKIE = "gi_oauth"
# 로그인 시도 쿠키는 콜백 경로에만 보냅니다.
OAUTH_COOKIE_PATH = "/api/auth/github"
# GitHub App 사용자 토큰 수명(8시간)과 같게 둡니다. 토큰 수명이 더 짧으면 그쪽을 따릅니다.
SESSION_MAX_AGE_S = 8 * 60 * 60
# GitHub 인증 화면에서 돌아올 때까지 기다리는 시간입니다.
OAUTH_MAX_AGE_S = 10 * 60

_SESSION_PURPOSE = b"gitinsight login session v1"
_OAUTH_PURPOSE = b"gitinsight oauth attempt v1"


@dataclass(frozen=True)
class LoginSession:
    """로그인 쿠키 내용입니다. 토큰은 SecretStr이라 repr과 로그에 나오지 않습니다."""

    user_id: str
    login: str
    avatar_url: str
    github_token: SecretStr
    expires_at: int


@dataclass(frozen=True)
class OAuthAttempt:
    state: str = field(repr=False)
    code_verifier: str = field(repr=False)


class _SessionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: Literal[1]
    uid: str
    login: str
    avatar: str
    tok: str
    exp: int


class _OAuthPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: Literal[1]
    state: str
    verifier: str


def _fernet(secret: SecretStr, purpose: bytes) -> Fernet:
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=purpose).derive(
        secret.get_secret_value().encode()
    )
    return Fernet(base64.urlsafe_b64encode(key))


def _seal(secret: SecretStr, purpose: bytes, payload: BaseModel, now: float) -> str:
    data = payload.model_dump_json().encode()
    return _fernet(secret, purpose).encrypt_at_time(data, int(now)).decode("ascii")


def _open(
    secret: SecretStr, purpose: bytes, value: str, max_age_s: int, now: float
) -> bytes | None:
    try:
        return _fernet(secret, purpose).decrypt_at_time(value.encode(), max_age_s, int(now))
    except (InvalidToken, UnicodeEncodeError):
        return None


def seal_session(settings: Settings, session: LoginSession, *, now: float | None = None) -> str:
    secret = _require_secret(settings)
    payload = _SessionPayload(
        v=1,
        uid=session.user_id,
        login=session.login,
        avatar=session.avatar_url,
        tok=session.github_token.get_secret_value(),
        exp=session.expires_at,
    )
    return _seal(secret, _SESSION_PURPOSE, payload, time.time() if now is None else now)


def open_session(
    settings: Settings, value: str, *, now: float | None = None
) -> LoginSession | None:
    """쿠키를 열어 로그인 정보를 돌려줍니다. 키가 없거나, 위조·변조됐거나, 만료됐으면 None입니다."""
    secret = settings.session_secret
    if secret is None:
        return None
    current = time.time() if now is None else now
    raw = _open(secret, _SESSION_PURPOSE, value, SESSION_MAX_AGE_S, current)
    if raw is None:
        return None
    try:
        payload = _SessionPayload.model_validate(json.loads(raw))
    except (ValueError, ValidationError):
        return None
    if payload.exp <= current:
        return None
    return LoginSession(
        user_id=payload.uid,
        login=payload.login,
        avatar_url=payload.avatar,
        github_token=SecretStr(payload.tok),
        expires_at=payload.exp,
    )


def seal_oauth(settings: Settings, attempt: OAuthAttempt, *, now: float | None = None) -> str:
    secret = _require_secret(settings)
    payload = _OAuthPayload(v=1, state=attempt.state, verifier=attempt.code_verifier)
    return _seal(secret, _OAUTH_PURPOSE, payload, time.time() if now is None else now)


def open_oauth(settings: Settings, value: str, *, now: float | None = None) -> OAuthAttempt | None:
    secret = settings.session_secret
    if secret is None:
        return None
    raw = _open(secret, _OAUTH_PURPOSE, value, OAUTH_MAX_AGE_S, time.time() if now is None else now)
    if raw is None:
        return None
    try:
        payload = _OAuthPayload.model_validate(json.loads(raw))
    except (ValueError, ValidationError):
        return None
    return OAuthAttempt(state=payload.state, code_verifier=payload.verifier)


def set_session_cookie(response: Response, settings: Settings, value: str, max_age_s: int) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        value,
        max_age=max_age_s,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax"
    )


def set_oauth_cookie(response: Response, settings: Settings, value: str) -> None:
    # GitHub에서 돌아오는 요청은 다른 사이트에서 시작한 최상위 GET 이동이라 SameSite=Lax면 쿠키가 붙습니다.
    response.set_cookie(
        OAUTH_COOKIE,
        value,
        max_age=OAUTH_MAX_AGE_S,
        path=OAUTH_COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_oauth_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        OAUTH_COOKIE,
        path=OAUTH_COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _require_secret(settings: Settings) -> SecretStr:
    if settings.session_secret is None:
        raise RuntimeError("SESSION_SECRET이 설정되지 않았습니다")
    return settings.session_secret
