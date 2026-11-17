"""로그인 사용자의 GitHub 토큰을 쓰는 라우트 (#18 2단계).

GET /api/repos                              면접 볼 레포 목록 (계약 Repo 배열)
GET /api/code?analysis_id=<id>&path=<경로>   분석에 나온 파일의 코드 (text/plain)

/api/code는 클라이언트가 보낸 레포 이름을 쓰지 않습니다. 요청자 본인의 분석 결과에 기록된 레포와 커밋에서,
그 분석의 주제 심볼이 있는 파일만 돌려줍니다. 받은 코드는 응답으로만 넘기고 저장하지 않습니다.
실험 모드(LAB_ENABLED=true)에는 GitHub 토큰이 없으므로 두 라우트 모두 404입니다. 웹은 데모 레포로 시작합니다.
"""

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse

from gitinsight_api.analyses.source import StoredAnalysisSource
from gitinsight_api.auth import read_login_session
from gitinsight_api.github_api import (
    GitHubAPI,
    GitHubFileTooLargeError,
    GitHubUnauthorizedError,
    GitHubUnavailableError,
)
from gitinsight_api.login_cookie import LoginSession, clear_session_cookie
from gitinsight_api.settings import Settings
from gitinsight_contracts.api import Repo

router = APIRouter(tags=["github"])

LOGIN_REQUIRED = "로그인이 필요합니다"
CODE_NOT_FOUND = "코드를 찾을 수 없습니다"
_NO_STORE = {"Cache-Control": "no-store"}


def _login(request: Request) -> LoginSession:
    settings: Settings = request.app.state.settings
    if settings.lab_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "실험 모드에서는 쓰지 않습니다")
    session = read_login_session(request)
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, LOGIN_REQUIRED)
    return session


LoginDep = Annotated[LoginSession, Depends(_login)]


def _github(request: Request, session: LoginSession) -> GitHubAPI:
    client: httpx.AsyncClient = request.app.state.github_http
    return GitHubAPI(client, session.github_token)


def _signed_out(request: Request) -> JSONResponse:
    response = JSONResponse(
        {"detail": LOGIN_REQUIRED}, status_code=status.HTTP_401_UNAUTHORIZED, headers=_NO_STORE
    )
    clear_session_cookie(response, request.app.state.settings)
    return response


@router.get("/api/repos", response_model=list[Repo])
async def list_repos(request: Request, session: LoginDep) -> Response:
    try:
        repos = await _github(request, session).list_repos()
    except GitHubUnauthorizedError:
        return _signed_out(request)
    except GitHubUnavailableError:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "GitHub에서 레포 목록을 받지 못했습니다"
        ) from None
    return JSONResponse([r.model_dump(mode="json") for r in repos], headers=_NO_STORE)


@router.get("/api/code", response_class=PlainTextResponse)
async def get_code(
    request: Request,
    session: LoginDep,
    analysis_id: Annotated[str, Query(min_length=1, max_length=200)],
    path: Annotated[str, Query(min_length=1, max_length=1000)],
) -> Response:
    analyses: StoredAnalysisSource | None = request.app.state.analyses
    if analyses is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "분석 저장소가 설정되지 않았습니다"
        )
    # 다른 사람의 분석과 없는 분석을 구분하지 않습니다 (분석 id가 있는지 알려 주지 않기 위해).
    if await analyses.owner_of(analysis_id) != session.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, CODE_NOT_FOUND)
    result = await analyses.get(analysis_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, CODE_NOT_FOUND)
    allowed = {s.path for t in result.topics for s in (t.symbol, *t.related_symbols)}
    if path not in allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, CODE_NOT_FOUND)
    try:
        text = await _github(request, session).file_text(
            result.repo_full_name, result.commit_sha, path
        )
    except GitHubUnauthorizedError:
        return _signed_out(request)
    except GitHubFileTooLargeError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "파일이 너무 커서 보여 줄 수 없습니다"
        ) from None
    except GitHubUnavailableError:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "GitHub에서 코드를 받지 못했습니다"
        ) from None
    if text is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, CODE_NOT_FOUND)
    return PlainTextResponse(
        text,
        headers={
            "Cache-Control": "private, no-store",
            # 코드를 HTML이나 스크립트로 해석하지 않게 합니다.
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )
