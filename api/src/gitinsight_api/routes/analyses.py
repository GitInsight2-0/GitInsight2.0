"""분석 요청과 진행 상태 조회 (#12). 화면은 web/components/RepoPicker.tsx입니다.

- POST /api/analyses: 레포 id를 받아 요청자 토큰으로 레포와 최신 커밋을 확인하고 분석을 요청합니다.
  같은 (레포, 커밋, 요청자)의 끝난 분석이 있으면 cached=True, 진행 중이면 그 분석을 돌려줍니다.
- GET /api/analyses/{analysis_id}: 요청자 본인의 분석 상태입니다. 다른 사람의 분석은 없는 분석과 같은 404입니다.

분석 실행기는 gitinsight_worker.server:app_factory가 넣습니다. 없으면(DB 없음, 실험 모드, gitinsight_api.main) 503입니다.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from gitinsight_api.analyses.service import AnalysisLaunchError, AnalysisService, LaunchContext
from gitinsight_api.auth import read_login_session
from gitinsight_api.github_repos import (
    EmptyRepoError,
    GitHubTokenError,
    GitHubUnavailableError,
    RepoNotFoundError,
    resolve_repo,
)
from gitinsight_api.routes.deps import UserDep
from gitinsight_contracts.api import AnalysisCreate, AnalysisCreated, AnalysisState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analyses", tags=["analyses"])

ANALYSIS_NOT_FOUND = "분석을 찾을 수 없습니다"


def get_analysis_service(request: Request) -> AnalysisService:
    service: AnalysisService | None = request.app.state.analysis_service
    if service is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "분석을 시작할 수 없는 서버 설정입니다"
        )
    return service


AnalysisServiceDep = Annotated[AnalysisService, Depends(get_analysis_service)]


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_analysis(
    body: AnalysisCreate, request: Request, user: UserDep, service: AnalysisServiceDep
) -> AnalysisCreated:
    session = read_login_session(request)
    if session is None or session.user_id != user.user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "로그인이 필요합니다")
    try:
        target = await resolve_repo(
            request.app.state.github_http, session.github_token, body.repo_id
        )
    except RepoNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "레포를 찾을 수 없습니다") from None
    except EmptyRepoError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "커밋이 없는 레포입니다"
        ) from None
    except GitHubTokenError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "GitHub 로그인이 만료되었습니다. 다시 로그인해 주세요"
        ) from None
    except GitHubUnavailableError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "GitHub에 연결하지 못했습니다") from None
    context = LaunchContext(github_token=session.github_token, login=session.login)
    try:
        created = await service.request(target.full_name, target.commit_sha, user.user_id, context)
    except AnalysisLaunchError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "분석을 시작하지 못했습니다"
        ) from None
    # 레포 이름은 비공개일 수 있어 로그에 남기지 않습니다.
    logger.info(
        "analysis request analysis=%s status=%s cached=%s",
        created.analysis_id,
        created.status.value,
        created.cached,
    )
    return created


@router.get("/{analysis_id}")
async def analysis_state(
    analysis_id: str, user: UserDep, service: AnalysisServiceDep
) -> AnalysisState:
    state = await service.state(analysis_id, user.user_id)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, ANALYSIS_NOT_FOUND)
    return state
