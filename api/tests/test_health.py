import pytest

from gitinsight_api.main import create_app
from gitinsight_api.settings import Settings

from api_test_support import ClientFactory


async def test_health_returns_ok(client_for: ClientFactory) -> None:
    async with client_for(create_app(Settings())) as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_empty_reasoning_effort_means_model_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_JUDGE_REASONING_EFFORT", "")
    assert Settings(_env_file=None).llm_judge_reasoning_effort is None  # type: ignore[call-arg]
