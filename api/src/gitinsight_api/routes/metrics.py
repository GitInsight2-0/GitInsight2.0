"""지연 측정 이벤트 라우트 (POST /api/metrics).

공개 주소에서 아무나 metric_events에 쌓지 못하게 로그인 사용자만 받습니다 (#19).
실험 모드(LAB_ENABLED=true)는 다른 라우트처럼 고정 실험 사용자로 받습니다.
"""

from fastapi import APIRouter, HTTPException, Request, Response, status

from gitinsight_api.metrics_store import MetricsStore, MetricsStoreUnavailableError
from gitinsight_api.routes.deps import UserDep
from gitinsight_contracts.metrics import MetricEvent

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
async def post_metric(event: MetricEvent, request: Request, _user: UserDep) -> Response:
    store: MetricsStore = request.app.state.metrics_store
    try:
        await store.append(event)
    except MetricsStoreUnavailableError:
        # 측정 이벤트를 잃은 것을 클라이언트가 알 수 있게 503을 돌려줍니다. 면접 진행에는 영향이 없습니다.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "측정 이벤트를 저장하지 못했습니다"
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
