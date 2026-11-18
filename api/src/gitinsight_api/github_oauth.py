"""GitHub App 사용자 인증(OAuth 웹 흐름) 호출 (#18).

순서: 인증 주소로 보냄 → 콜백의 code를 사용자 토큰으로 교환 → 토큰으로 GET /user.
PKCE(S256)와 state를 같이 씁니다. 토큰과 code는 로그와 예외 메시지에 넣지 않습니다.
GitHub 문서: https://docs.github.com/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app
"""

import base64
import hashlib
import logging
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"
# 사용자 토큰 하나를 폐기합니다. App 설치와 사용자 승인은 그대로 둡니다.
# GitHub 문서: https://docs.github.com/rest/apps/oauth-applications#delete-an-app-token
REVOKE_URL_TEMPLATE = "https://api.github.com/applications/{client_id}/token"
API_VERSION = "2026-03-10"
# GitHub 호출 하나의 제한 시간(초)입니다. 넘으면 로그인 실패로 웹에 돌려보냅니다.
HTTP_TIMEOUT_S = 10.0
# 로그아웃은 이 시간만 기다립니다. 폐기가 늦어도 쿠키는 지우고 바로 응답합니다.
REVOKE_TIMEOUT_S = 3.0


class GitHubAuthError(Exception):
    """GitHub 호출 실패입니다. 메시지에는 토큰, code, 응답 본문을 넣지 않습니다."""


@dataclass(frozen=True)
class UserToken:
    access_token: SecretStr
    # 만료하지 않는 토큰(App 설정에서 만료를 끈 경우)은 None입니다.
    expires_in: int | None


@dataclass(frozen=True)
class GitHubUser:
    user_id: str
    login: str
    avatar_url: str


class _TokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int | None = None


class _UserResponse(BaseModel):
    id: int
    login: str
    avatar_url: str = ""


def new_state() -> str:
    return secrets.token_urlsafe(32)


def new_code_verifier() -> str:
    # RFC 7636은 43~128자를 요구합니다. 64바이트 → 86자입니다.
    return secrets.token_urlsafe(64)


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorize_url(*, client_id: str, redirect_uri: str, state: str, code_verifier: str) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge(code_verifier),
            "code_challenge_method": "S256",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


class GitHubOAuth:
    def __init__(
        self, client: httpx.AsyncClient, *, client_id: str, client_secret: SecretStr
    ) -> None:
        self._client = client
        self._client_id = client_id
        self._client_secret = client_secret

    async def exchange_code(self, *, code: str, redirect_uri: str, code_verifier: str) -> UserToken:
        try:
            response = await self._client.post(
                TOKEN_URL,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret.get_secret_value(),
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": code_verifier,
                },
                headers={"Accept": "application/json"},
                timeout=HTTP_TIMEOUT_S,
            )
        except httpx.HTTPError as exc:
            logger.warning("GitHub 토큰 교환 요청 실패: %s", type(exc).__name__)
            raise GitHubAuthError("토큰 교환 요청 실패") from None
        body = _json_object(response)
        if response.status_code != 200 or body is None:
            logger.warning("GitHub 토큰 교환 응답 오류: HTTP %s", response.status_code)
            raise GitHubAuthError("토큰 교환 응답 오류")
        # 실패해도 HTTP 200에 error 필드로 옵니다 (예: bad_verification_code). 오류 코드만 남깁니다.
        error = body.get("error")
        if error is not None:
            logger.warning("GitHub 토큰 교환 거절: %s", short_error_code(error))
            raise GitHubAuthError("토큰 교환 거절")
        try:
            token = _TokenResponse.model_validate(body)
        except ValidationError:
            logger.warning("GitHub 토큰 교환 응답 형식 오류")
            raise GitHubAuthError("토큰 교환 응답 형식 오류") from None
        if token.token_type.lower() != "bearer" or not token.access_token:
            logger.warning("GitHub 토큰 교환 응답 형식 오류")
            raise GitHubAuthError("토큰 교환 응답 형식 오류")
        expires_in = token.expires_in if token.expires_in and token.expires_in > 0 else None
        return UserToken(access_token=SecretStr(token.access_token), expires_in=expires_in)

    async def fetch_user(self, token: SecretStr) -> GitHubUser:
        try:
            response = await self._client.get(
                USER_URL,
                headers={
                    "Authorization": f"Bearer {token.get_secret_value()}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": API_VERSION,
                },
                timeout=HTTP_TIMEOUT_S,
            )
        except httpx.HTTPError as exc:
            logger.warning("GitHub 사용자 조회 요청 실패: %s", type(exc).__name__)
            raise GitHubAuthError("사용자 조회 요청 실패") from None
        body = _json_object(response)
        if response.status_code != 200 or body is None:
            logger.warning("GitHub 사용자 조회 응답 오류: HTTP %s", response.status_code)
            raise GitHubAuthError("사용자 조회 응답 오류")
        try:
            user = _UserResponse.model_validate(body)
        except ValidationError:
            logger.warning("GitHub 사용자 조회 응답 형식 오류")
            raise GitHubAuthError("사용자 조회 응답 형식 오류") from None
        # 웹이 <img src>로 쓰므로 https 주소만 넘깁니다.
        avatar = user.avatar_url if user.avatar_url.startswith("https://") else ""
        return GitHubUser(user_id=str(user.id), login=user.login, avatar_url=avatar)

    async def revoke_token(self, token: SecretStr) -> bool:
        """사용자 토큰을 폐기합니다. 실패해도 예외를 내지 않고 False를 돌려줍니다.

        이미 만료됐거나 폐기된 토큰은 GitHub가 404로 답합니다. 쓸 수 없는 토큰이므로 성공으로 봅니다.
        """
        try:
            response = await self._client.request(
                "DELETE",
                REVOKE_URL_TEMPLATE.format(client_id=self._client_id),
                json={"access_token": token.get_secret_value()},
                auth=(self._client_id, self._client_secret.get_secret_value()),
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": API_VERSION,
                },
                timeout=REVOKE_TIMEOUT_S,
            )
        except httpx.HTTPError as exc:
            logger.warning("GitHub 토큰 폐기 요청 실패: %s", type(exc).__name__)
            return False
        if response.status_code in (204, 404):
            return True
        logger.warning("GitHub 토큰 폐기 응답 오류: HTTP %s", response.status_code)
        return False


def _json_object(response: httpx.Response) -> dict[str, object] | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def short_error_code(value: object) -> str:
    # GitHub 오류 코드만 남깁니다. 예상 밖 값이 길게 와도 로그에 그대로 쓰지 않습니다.
    text = str(value)
    return text[:40] if text.isascii() and text.replace("_", "").isalnum() else "unknown"
