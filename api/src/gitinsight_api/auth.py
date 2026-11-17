"""로그인 사용자 확인.
라우트는 get_current_user로 로그인 사용자를 받습니다.
authenticate는 GitHub 로그인(#18) 쿠키를 열어 사용자를 돌려주고, 없거나 위조·만료됐으면 401을 냅니다.
LAB_ENABLED=true(음성 PoC)일 때만 로그인 없이 고정 실험 사용자를 씁니다.
LAB_ENABLED는 데모와 배포에서 끄므로 고정 실험 사용자는 운영에 나가지 않습니다.
테스트는 app.dependency_overrides로 get_current_user를 바꿉니다.
"""

from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from gitinsight_api.login_cookie import SESSION_COOKIE, LoginSession, open_session
from gitinsight_api.settings import Settings


@dataclass(frozen=True)
class CurrentUser:
    # GitHub 숫자 사용자 ID를 문자열로 씁니다. login은 바뀔 수 있어 소유자 비교에 쓰지 않습니다.
    user_id: str
    login: str
    avatar_url: str = ""


LAB_USER = CurrentUser(user_id="lab", login="lab")


def read_login_session(request: Request) -> LoginSession | None:
    """로그인 쿠키를 엽니다. GitHub 토큰이 필요한 곳(2단계 레포 목록·코드)은 이것을 씁니다."""
    value = request.cookies.get(SESSION_COOKIE)
    if not value:
        return None
    settings: Settings = request.app.state.settings
    return open_session(settings, value)


async def authenticate(request: Request) -> CurrentUser:
    """요청의 로그인 사용자를 돌려줍니다. 토큰은 CurrentUser에 넣지 않습니다."""
    session = read_login_session(request)
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "로그인이 필요합니다")
    return CurrentUser(user_id=session.user_id, login=session.login, avatar_url=session.avatar_url)


async def get_current_user(request: Request) -> CurrentUser:
    # 실험 예외를 authenticate 밖에 두어, 로그인을 붙여도 음성 PoC가 깨지지 않게 합니다.
    settings: Settings = request.app.state.settings
    if settings.lab_enabled:
        return LAB_USER
    return await authenticate(request)
