"""로그인 사용자 확인 자리.

라우트는 get_current_user로 로그인 사용자를 받습니다.
실제 GitHub 로그인은 D가 #18에서 authenticate 본문을 바꿔 붙입니다.
그 전까지 authenticate는 항상 401을 냅니다.
LAB_ENABLED=true(음성 PoC)일 때만 로그인 없이 고정 실험 사용자를 씁니다.
LAB_ENABLED는 데모와 배포에서 끄므로 고정 실험 사용자는 운영에 나가지 않습니다.
테스트는 app.dependency_overrides로 get_current_user를 바꿉니다.
"""

from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from gitinsight_api.settings import Settings


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    login: str


LAB_USER = CurrentUser(user_id="lab", login="lab")


async def authenticate(request: Request) -> CurrentUser:
    """요청의 로그인 사용자를 돌려줍니다. D가 #18(GitHub 로그인)에서 이 본문을 바꿉니다."""
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "로그인이 필요합니다")


async def get_current_user(request: Request) -> CurrentUser:
    # 실험 예외를 authenticate 밖에 두어, 로그인을 붙여도 음성 PoC가 깨지지 않게 합니다.
    settings: Settings = request.app.state.settings
    if settings.lab_enabled:
        return LAB_USER
    return await authenticate(request)
