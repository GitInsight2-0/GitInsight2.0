"""지연 측정 이벤트 라우트 (POST /api/metrics)."""

from fastapi import APIRouter, Request, Response, status

from gitinsight_api.metrics_store import MetricsStore
from gitinsight_contracts.metrics import MetricEvent

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
async def post_metric(event: MetricEvent, request: Request) -> Response:
    store: MetricsStore = request.app.state.metrics_store
    await store.append(event)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
