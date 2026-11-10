import asyncio
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from gitinsight_api.brain.scripted import ScriptedBrain
from gitinsight_api.main import create_app
from gitinsight_api.settings import Settings
from gitinsight_contracts.fixtures import load_analysis
from gitinsight_contracts.interview import (
    Action,
    BrainReply,
    InterviewMode,
    SessionCreate,
    SessionCreated,
    TurnRequest,
)

from api_test_support import ClientFactory

ANALYSIS_ID = "demo-gitinsight-agent"


def lab_settings(metrics_path: Path, *, lab: bool = True) -> Settings:
    return Settings(lab_enabled=lab, metrics_path=metrics_path)


async def start(client: httpx.AsyncClient) -> SessionCreated:
    response = await client.post(
        "/api/sessions", json={"analysis_id": ANALYSIS_ID, "mode": "voice"}
    )
    assert response.status_code == 201, response.text
    return SessionCreated.model_validate(response.json())


def turn(turn_id: str | None = None) -> dict[str, object]:
    return {"turn_id": turn_id or str(uuid4()), "transcript": "답변입니다", "source": "voice"}


async def test_lab_session_starts_with_first_topic_highlight(
    client_for: ClientFactory, metrics_path: Path
) -> None:
    analysis = load_analysis()
    async with client_for(create_app(lab_settings(metrics_path))) as client:
        created = await start(client)

    first = created.first
    assert first.action is Action.ASK
    assert first.turn_id is None
    assert first.highlight == analysis.topics[0].symbol
    assert analysis.topics[0].base_question in first.utterance


async def test_lab_delay_is_applied(client_for: ClientFactory, metrics_path: Path) -> None:
    async with client_for(create_app(lab_settings(metrics_path))) as client:
        created = await start(client)
        began = time.perf_counter()
        response = await client.post(
            f"/api/sessions/{created.session_id}/turns", params={"lab_delay_ms": 300}, json=turn()
        )
        elapsed = time.perf_counter() - began

    assert response.status_code == 200
    assert elapsed >= 0.3


async def test_lab_delay_is_rejected_when_lab_disabled(
    client_for: ClientFactory, metrics_path: Path
) -> None:
    brain = ScriptedBrain({ANALYSIS_ID: load_analysis()})
    app = create_app(lab_settings(metrics_path, lab=False), brain=brain)
    async with client_for(app) as client:
        created = await start(client)
        response = await client.post(
            f"/api/sessions/{created.session_id}/turns", params={"lab_delay_ms": 800}, json=turn()
        )

    assert response.status_code == 400
    assert "LAB_ENABLED" in response.json()["detail"]


async def test_sessions_unavailable_without_brain(
    client_for: ClientFactory, metrics_path: Path
) -> None:
    async with client_for(create_app(lab_settings(metrics_path, lab=False))) as client:
        response = await client.post(
            "/api/sessions", json={"analysis_id": ANALYSIS_ID, "mode": "voice"}
        )

    assert response.status_code == 503


async def test_duplicate_turn_id_is_processed_once(
    client_for: ClientFactory, metrics_path: Path
) -> None:
    async with client_for(create_app(lab_settings(metrics_path))) as client:
        created = await start(client)
        url = f"/api/sessions/{created.session_id}/turns"
        same = turn()
        first, second = await asyncio.gather(
            client.post(url, json=same), client.post(url, json=same)
        )
        following = await client.post(url, json=turn())

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    replies = [BrainReply.model_validate(r.json()) for r in (first, following)]
    # 중복 요청이 대본을 두 칸 넘기지 않았다면 다음 턴은 대본의 두 번째 꼬리질문입니다.
    assert [r.depth for r in replies] == [1, 2]


async def test_unknown_session_returns_404(client_for: ClientFactory, metrics_path: Path) -> None:
    async with client_for(create_app(lab_settings(metrics_path))) as client:
        response = await client.post(f"/api/sessions/{uuid4()}/turns", json=turn())

    assert response.status_code == 404


async def test_scripted_brain_wraps_up_when_time_is_over() -> None:
    now = [0.0]
    brain = ScriptedBrain({ANALYSIS_ID: load_analysis()}, clock=lambda: now[0])
    session_id, _ = await brain.start(
        SessionCreate(analysis_id=ANALYSIS_ID, mode=InterviewMode.VOICE, duration_min=3)
    )
    now[0] = 181.0
    reply = await brain.handle_turn(session_id, TurnRequest.model_validate(turn()))
    assert reply.action is Action.WRAP_UP
    assert reply.done is True
    assert reply.remaining_sec == 0


async def test_metrics_are_appended_as_jsonl(client_for: ClientFactory, metrics_path: Path) -> None:
    async with client_for(create_app(lab_settings(metrics_path))) as client:
        response = await client.post(
            "/api/metrics",
            json={
                "name": "first_audio",
                "at_ms": 1700000000000,
                "value_ms": 812.5,
                "provider": "openai",
            },
        )

    assert response.status_code == 204
    record = json.loads(metrics_path.read_text(encoding="utf-8").strip())
    assert record["name"] == "first_audio"
    assert record["value_ms"] == 812.5
    assert "received_at_ms" in record


@pytest.mark.parametrize(
    "body",
    [
        {"name": "vad_end", "at_ms": 1, "transcript": "원문 자막"},
        {"name": "error", "at_ms": 1, "error_code": "Traceback: secret code"},
    ],
)
async def test_metrics_reject_fields_outside_allowlist(
    client_for: ClientFactory, metrics_path: Path, body: dict[str, object]
) -> None:
    async with client_for(create_app(lab_settings(metrics_path))) as client:
        response = await client.post("/api/metrics", json=body)

    assert response.status_code == 422
    assert not metrics_path.exists()
