"""면접자 시선 요약 (#177). 브라우저가 보낸 요약을 리포트에 담기만 하고 점수에는 쓰지 않습니다."""

from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from gitinsight_api.auth import CurrentUser, get_current_user
from gitinsight_api.brain.factory import build_fixture_brain, build_report_service
from gitinsight_api.brain.state import TurnRecord
from gitinsight_api.llm.fake import FakeLLM
from gitinsight_api.main import create_app
from gitinsight_api.settings import Settings
from gitinsight_contracts.fixtures import load_analysis
from gitinsight_contracts.interview import GazeSummary, SessionCreated
from gitinsight_contracts.report import Report

from api_test_support import ClientFactory

ANALYSIS_ID = "demo-gitinsight-agent"
GAZE: dict[str, Any] = {
    "measured_seconds": 600.0,
    "camera_ratio": 0.35,
    "screen_ratio": 0.5,
    "away_ratio": 0.1,
    "no_face_ratio": 0.05,
    "away_count": 4,
}


def app_for(metrics_path: Path) -> FastAPI:
    config = Settings(lab_enabled=False, metrics_path=metrics_path)
    llm = FakeLLM()
    brain = build_fixture_brain(config, llm=llm, force_prepared=True)
    reports = build_report_service(config, brain, load_analysis(), llm=llm, force_prepared=True)
    app = create_app(config, brain=brain, report_service=reports)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("alice", "alice")
    return app


async def start(client: httpx.AsyncClient) -> UUID:
    response = await client.post("/api/sessions", json={"analysis_id": ANALYSIS_ID, "mode": "text"})
    assert response.status_code == 201, response.text
    session_id = SessionCreated.model_validate(response.json()).session_id
    answer = {
        "turn_id": "00000000-0000-4000-8000-000000000001",
        "transcript": "답변",
        "source": "text",
    }
    turn = await client.post(f"/api/sessions/{session_id}/turns", json=answer)
    assert turn.status_code == 200, turn.text
    return session_id


async def end_and_read(
    client: httpx.AsyncClient, session_id: UUID, body: dict[str, Any] | None
) -> Report:
    ended = await client.post(f"/api/sessions/{session_id}/end", json=body)
    assert ended.status_code == 200, ended.text
    report = await client.get(f"/api/reports/{ended.json()['report_id']}")
    assert report.status_code == 200, report.text
    return Report.model_validate(report.json())


async def test_gaze_is_kept_in_report_without_changing_scores(
    client_for: ClientFactory, metrics_path: Path
) -> None:
    async with client_for(app_for(metrics_path)) as client:
        with_gaze = await end_and_read(client, await start(client), {"gaze": GAZE})
        without = await end_and_read(client, await start(client), None)
        empty_body = await end_and_read(client, await start(client), {})

    assert with_gaze.gaze == GazeSummary.model_validate(GAZE)
    assert (without.gaze, empty_body.gaze) == (None, None)
    # 시선은 참고 정보입니다. 같은 답변이면 점수와 판단 표가 시선과 상관없이 같습니다.
    assert with_gaze.competencies == without.competencies
    assert [r.covered_points for r in with_gaze.turns] == [r.covered_points for r in without.turns]


async def test_first_end_wins_and_later_gaze_is_ignored(
    client_for: ClientFactory, metrics_path: Path
) -> None:
    async with client_for(app_for(metrics_path)) as client:
        session_id = await start(client)
        first = await end_and_read(client, session_id, {"gaze": GAZE})
        later = await end_and_read(
            client, session_id, {"gaze": {**GAZE, "camera_ratio": 0.45, "screen_ratio": 0.4}}
        )

    assert later.report_id == first.report_id
    assert later.gaze == first.gaze


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"camera_ratio": 0.9}, "시선 비율의 합은 1이어야 합니다"),
        ({"camera_ratio": -0.1, "screen_ratio": 0.95}, "greater than or equal to 0"),
        ({"measured_seconds": 0}, "greater than 0"),
        ({"away_count": -1}, "greater than or equal to 0"),
        ({"frames": [1, 2, 3]}, "Extra inputs are not permitted"),
    ],
)
async def test_bad_gaze_is_422_and_session_stays_open(
    client_for: ClientFactory, metrics_path: Path, change: dict[str, Any], message: str
) -> None:
    async with client_for(app_for(metrics_path)) as client:
        session_id = await start(client)
        bad = await client.post(
            f"/api/sessions/{session_id}/end", json={"gaze": {**GAZE, **change}}
        )
        # 요청 검증에서 막혀 세션이 닫히지 않았으므로 면접을 이어 갈 수 있습니다.
        more = {
            "turn_id": "00000000-0000-4000-8000-000000000002",
            "transcript": "더",
            "source": "text",
        }
        turn = await client.post(f"/api/sessions/{session_id}/turns", json=more)

    assert bad.status_code == 422
    assert message in bad.text
    assert turn.status_code == 200, turn.text


def test_ratios_within_rounding_are_accepted() -> None:
    rounded = GazeSummary.model_validate({**GAZE, "no_face_ratio": 0.055})

    assert rounded.no_face_ratio == 0.055
    with pytest.raises(ValidationError, match="시선 비율의 합은 1이어야 합니다"):
        GazeSummary.model_validate({**GAZE, "no_face_ratio": 0.07})


# ── 답변별 시선 (#180) ──


def answer_gaze(away: float) -> dict[str, Any]:
    """화면 밖 비율이 away인 답변 하나의 시선 요약입니다."""
    return {
        "measured_seconds": 30.0,
        "camera_ratio": 0.3,
        "screen_ratio": round(0.7 - away, 3),
        "away_ratio": away,
        "no_face_ratio": 0.0,
        "away_count": 1 if away else 0,
    }


def turn_id(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


async def answer_with_gaze(client: httpx.AsyncClient, gazes: list[dict[str, Any] | None]) -> UUID:
    """답변마다 시선 요약(없으면 None)을 실어 보낸 세션입니다."""
    response = await client.post("/api/sessions", json={"analysis_id": ANALYSIS_ID, "mode": "text"})
    assert response.status_code == 201, response.text
    session_id = SessionCreated.model_validate(response.json()).session_id
    for i, gaze in enumerate(gazes, start=1):
        body: dict[str, Any] = {"turn_id": turn_id(i), "transcript": f"답변 {i}", "source": "text"}
        if gaze is not None:
            body["gaze"] = gaze
        turn = await client.post(f"/api/sessions/{session_id}/turns", json=body)
        assert turn.status_code == 200, turn.text
    return session_id


async def test_report_points_at_answer_with_most_away_gaze(
    client_for: ClientFactory, metrics_path: Path
) -> None:
    async with client_for(app_for(metrics_path)) as client:
        session_id = await answer_with_gaze(
            client, [answer_gaze(0.1), None, answer_gaze(0.4), answer_gaze(0.4)]
        )
        report = await end_and_read(client, session_id, {"gaze": GAZE})

    assert report.gaze_peak is not None
    # 같은 비율이면 먼저 한 답변을 짚습니다.
    assert str(report.gaze_peak.turn_id) == turn_id(3)
    assert report.gaze_peak.away_ratio == 0.4
    assert str(report.gaze_peak.turn_id) in {str(t.turn_id) for t in report.turns}


async def test_no_peak_without_whole_gaze_or_away_answers(
    client_for: ClientFactory, metrics_path: Path
) -> None:
    async with client_for(app_for(metrics_path)) as client:
        # 면접 도중 시선 분석을 끄면 끝낼 때 gaze가 오지 않습니다. 앞서 보낸 답변별 요약도 쓰지 않습니다.
        stopped = await end_and_read(
            client, await answer_with_gaze(client, [answer_gaze(0.5)]), None
        )
        # 화면 밖을 본 답변이 없으면 짚을 답변이 없습니다.
        steady = await end_and_read(
            client, await answer_with_gaze(client, [answer_gaze(0.0), None]), {"gaze": GAZE}
        )
        # 시선 분석을 켜지 않은 면접은 예전과 같습니다.
        plain = await end_and_read(client, await answer_with_gaze(client, [None]), None)

    assert (stopped.gaze, stopped.gaze_peak) == (None, None)
    assert steady.gaze is not None and steady.gaze_peak is None
    assert (plain.gaze, plain.gaze_peak) == (None, None)


async def test_answer_gaze_does_not_change_scores(
    client_for: ClientFactory, metrics_path: Path
) -> None:
    async with client_for(app_for(metrics_path)) as client:
        with_gaze = await end_and_read(
            client,
            await answer_with_gaze(client, [answer_gaze(0.6), answer_gaze(0.2)]),
            {"gaze": GAZE},
        )
        without = await end_and_read(client, await answer_with_gaze(client, [None, None]), None)

    assert with_gaze.competencies == without.competencies
    assert [r.covered_points for r in with_gaze.turns] == [r.covered_points for r in without.turns]
    assert [r.action for r in with_gaze.turns] == [r.action for r in without.turns]


async def test_bad_answer_gaze_is_422(client_for: ClientFactory, metrics_path: Path) -> None:
    async with client_for(app_for(metrics_path)) as client:
        response = await client.post(
            "/api/sessions", json={"analysis_id": ANALYSIS_ID, "mode": "text"}
        )
        session_id = SessionCreated.model_validate(response.json()).session_id
        bad = await client.post(
            f"/api/sessions/{session_id}/turns",
            json={
                "turn_id": turn_id(1),
                "transcript": "답변",
                "source": "text",
                "gaze": {**answer_gaze(0.1), "landmarks": [0.1, 0.2]},
            },
        )

    assert bad.status_code == 422
    assert "Extra inputs are not permitted" in bad.text


def test_turn_record_without_gaze_still_reads() -> None:
    """이 필드가 생기기 전에 저장한 턴 기록도 그대로 읽힙니다."""
    assert TurnRecord.model_fields["gaze"].default is None
