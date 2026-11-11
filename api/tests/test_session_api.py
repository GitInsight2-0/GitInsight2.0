"""실제 모드(LAB_ENABLED=false) 세션 HTTP API 테스트. LLM은 FakeLLM만 씁니다."""

import logging
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from gitinsight_api.auth import CurrentUser, get_current_user
from gitinsight_api.brain.factory import build_fixture_brain, build_report_service
from gitinsight_api.brain.judge import JUDGE_PURPOSE
from gitinsight_api.brain.service import InterviewBrain
from gitinsight_api.llm import LiteLLMClient, LLMTimeoutError
from gitinsight_api.llm.fake import FakeLLM, FakeStep
from gitinsight_api.main import create_app
from gitinsight_api.report.builder import REPORT_PURPOSE
from gitinsight_api.settings import Settings
from gitinsight_contracts.fixtures import load_analysis
from gitinsight_contracts.interview import BrainReply, SessionCreated, SessionEnded
from gitinsight_contracts.llm import (
    Competency,
    CompetencyDraft,
    CoverageStatus,
    EvidenceDraft,
    Intent,
    Judgment,
    PointCoverage,
    ReportDraft,
    TopicDraft,
)
from gitinsight_contracts.report import Report, ReportKind

from api_test_support import ClientFactory

ANALYSIS_ID = "demo-gitinsight-agent"
ALICE = CurrentUser(user_id="alice", login="alice")
BOB = CurrentUser(user_id="bob", login="bob")
GOOD = "커밋 SHA를 컬렉션 이름에 넣어서 커밋 단위로 캐시합니다."
QUOTE = "커밋 SHA를 컬렉션 이름에 넣어서"


class Users:
    """테스트 중 로그인 사용자를 바꿀 수 있게 합니다."""

    def __init__(self) -> None:
        self.current = ALICE

    def __call__(self) -> CurrentUser:
        return self.current


def real_app(
    metrics_path: Path, llm: FakeLLM | None = None, **settings: Any
) -> tuple[FastAPI, InterviewBrain, Users]:
    config = Settings(lab_enabled=False, metrics_path=metrics_path, **settings)
    fake = llm if llm is not None else FakeLLM()
    forced = llm is None
    brain = build_fixture_brain(config, llm=fake, force_prepared=forced)
    reports = build_report_service(config, brain, load_analysis(), llm=fake, force_prepared=forced)
    app = create_app(config, brain=brain, report_service=reports)
    users = Users()
    app.dependency_overrides[get_current_user] = users
    return app, brain, users


def turn(transcript: str = "답변입니다", turn_id: UUID | None = None) -> dict[str, object]:
    return {"turn_id": str(turn_id or uuid4()), "transcript": transcript, "source": "text"}


async def create(client: httpx.AsyncClient, **extra: object) -> SessionCreated:
    response = await client.post(
        "/api/sessions", json={"analysis_id": ANALYSIS_ID, "mode": "text", **extra}
    )
    assert response.status_code == 201, response.text
    return SessionCreated.model_validate(response.json())


async def end(client: httpx.AsyncClient, session_id: UUID) -> SessionEnded:
    response = await client.post(f"/api/sessions/{session_id}/end")
    assert response.status_code == 200, response.text
    return SessionEnded.model_validate(response.json())


async def get_report(client: httpx.AsyncClient, report_id: UUID) -> Report:
    response = await client.get(f"/api/reports/{report_id}")
    assert response.status_code == 200, response.text
    return Report.model_validate(response.json())


def report_draft(turn_id: UUID) -> ReportDraft:
    return ReportDraft(
        competencies=[
            CompetencyDraft(
                name=Competency.CODE_UNDERSTANDING,
                level=3,
                rationale="흐름을 맞게 설명",
                evidence=[EvidenceDraft(turn_id=str(turn_id), quote=QUOTE, symbol_id=None)],
            )
        ],
        topics=[TopicDraft(topic_id="build-github-repo", model_answer="커밋 SHA로 캐시합니다.")],
        strengths=["흐름 설명이 정확함"],
        improvements=["한계 설명 보완"],
        next_topics=["캐시 무효화"],
    )


def judged() -> Judgment:
    return Judgment(
        intent=Intent.ANSWER,
        coverage=[
            PointCoverage(point_id="bgr-1", status=CoverageStatus.COVERED, evidence_quote=QUOTE)
        ],
        followup=None,
    )


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/sessions"),
        ("POST", f"/api/sessions/{uuid4()}/turns"),
        ("POST", f"/api/sessions/{uuid4()}/end"),
        ("GET", f"/api/reports/{uuid4()}"),
    ],
)
async def test_real_mode_requires_login(
    client_for: ClientFactory, metrics_path: Path, method: str, path: str
) -> None:
    app, _, _ = real_app(metrics_path)
    app.dependency_overrides.clear()
    body = {"analysis_id": ANALYSIS_ID, "mode": "text"} if path == "/api/sessions" else turn()
    async with client_for(app) as client:
        response = await client.request(method, path, json=body if method == "POST" else None)

    assert response.status_code == 401
    assert response.json()["detail"] == "로그인이 필요합니다"


async def test_full_flow_with_llm_report(client_for: ClientFactory, metrics_path: Path) -> None:
    llm = FakeLLM()
    turn_id = uuid4()
    llm.push(JUDGE_PURPOSE, FakeStep(value=judged()))
    llm.push(REPORT_PURPOSE, FakeStep(value=report_draft(turn_id)))
    app, brain, _ = real_app(metrics_path, llm)
    async with client_for(app) as client:
        created = await create(client)
        response = await client.post(
            f"/api/sessions/{created.session_id}/turns", json=turn(GOOD, turn_id)
        )
        assert response.status_code == 200, response.text
        BrainReply.model_validate(response.json())
        ended = await end(client, created.session_id)
        report = await get_report(client, ended.report_id)

    state = await brain.repository.get(created.session_id)
    assert state is not None and state.owner_id == "alice"
    assert report.report_id == ended.report_id
    assert report.session_id == created.session_id
    assert report.kind is ReportKind.FULL
    assert [c.name for c in report.competencies] == [Competency.CODE_UNDERSTANDING]
    assert [r.turn_id for r in report.turns] == [turn_id]


async def test_full_flow_with_llm_failure_returns_turn_table(
    client_for: ClientFactory, metrics_path: Path
) -> None:
    llm = FakeLLM()
    llm.push(JUDGE_PURPOSE, FakeStep(value=judged()))
    llm.push(REPORT_PURPOSE, FakeStep(error=LLMTimeoutError("t")))
    app, _, _ = real_app(metrics_path, llm)
    async with client_for(app) as client:
        created = await create(client)
        response = await client.post(f"/api/sessions/{created.session_id}/turns", json=turn(GOOD))
        assert response.status_code == 200, response.text
        ended = await end(client, created.session_id)
        report = await get_report(client, ended.report_id)

    assert report.session_id == created.session_id
    assert report.kind is ReportKind.TURN_TABLE
    assert report.competencies == []
    assert len(report.turns) == 1


async def test_end_is_idempotent(client_for: ClientFactory, metrics_path: Path) -> None:
    app, _, _ = real_app(metrics_path)
    async with client_for(app) as client:
        created = await create(client)
        first = await end(client, created.session_id)
        second = await end(client, created.session_id)

    assert first.report_id == second.report_id


async def test_turn_after_end_conflicts(client_for: ClientFactory, metrics_path: Path) -> None:
    app, _, _ = real_app(metrics_path)
    async with client_for(app) as client:
        created = await create(client)
        await end(client, created.session_id)
        response = await client.post(f"/api/sessions/{created.session_id}/turns", json=turn())

    assert response.status_code == 409


async def test_unknown_session_and_report_return_404(
    client_for: ClientFactory, metrics_path: Path
) -> None:
    app, _, _ = real_app(metrics_path)
    async with client_for(app) as client:
        turns = await client.post(f"/api/sessions/{uuid4()}/turns", json=turn())
        ended = await client.post(f"/api/sessions/{uuid4()}/end")
        report = await client.get(f"/api/reports/{uuid4()}")

    assert turns.status_code == ended.status_code == report.status_code == 404


async def test_other_user_is_forbidden(client_for: ClientFactory, metrics_path: Path) -> None:
    app, _, users = real_app(metrics_path)
    async with client_for(app) as client:
        created = await create(client)
        users.current = BOB
        turns = await client.post(f"/api/sessions/{created.session_id}/turns", json=turn())
        bob_end = await client.post(f"/api/sessions/{created.session_id}/end")
        users.current = ALICE
        ended = await end(client, created.session_id)
        users.current = BOB
        bob_report = await client.get(f"/api/reports/{ended.report_id}")
        users.current = ALICE
        report = await get_report(client, ended.report_id)

    assert turns.status_code == bob_end.status_code == 403
    assert turns.json()["detail"] == "이 세션에 접근할 수 없습니다"
    assert bob_report.status_code == 403
    assert bob_report.json()["detail"] == "이 리포트에 접근할 수 없습니다"
    assert report.session_id == created.session_id


async def test_extra_fields_are_rejected(client_for: ClientFactory, metrics_path: Path) -> None:
    app, _, _ = real_app(metrics_path)
    async with client_for(app) as client:
        owner = await client.post(
            "/api/sessions", json={"analysis_id": ANALYSIS_ID, "mode": "text", "owner_id": "bob"}
        )
        created = await create(client)
        remaining = await client.post(
            f"/api/sessions/{created.session_id}/turns", json={**turn(), "remaining_sec": 0}
        )

    assert owner.status_code == remaining.status_code == 422


async def test_lab_mode_needs_no_login_and_has_no_reports(
    client_for: ClientFactory, metrics_path: Path
) -> None:
    app = create_app(Settings(lab_enabled=True, metrics_path=metrics_path))
    async with client_for(app) as client:
        created = await create(client)
        response = await client.post(f"/api/sessions/{created.session_id}/turns", json=turn())
        ended = await client.post(f"/api/sessions/{created.session_id}/end")
        report = await client.get(f"/api/reports/{uuid4()}")

    assert response.status_code == 200
    assert ended.status_code == report.status_code == 503
    assert ended.json()["detail"] == "리포트 서비스가 설정되지 않았습니다"


def test_default_wiring_uses_real_brain(metrics_path: Path) -> None:
    app = create_app(Settings(lab_enabled=False, metrics_path=metrics_path))

    assert isinstance(app.state.brain, InterviewBrain)
    assert app.state.report_service is not None


async def test_force_prepared_default_wiring_never_calls_llm(
    client_for: ClientFactory, metrics_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def no_llm(self: LiteLLMClient, *args: object, **kwargs: object) -> object:
        calls.append(str(kwargs.get("purpose")))
        raise AssertionError("BRAIN_FORCE_PREPARED에서 LLM을 불렀습니다")

    monkeypatch.setattr(LiteLLMClient, "complete_structured", no_llm)
    app = create_app(
        Settings(lab_enabled=False, metrics_path=metrics_path, brain_force_prepared=True)
    )
    app.dependency_overrides[get_current_user] = Users()
    async with client_for(app) as client:
        created = await create(client, jd_text="파이썬 백엔드 개발자")
        for _ in range(2):
            response = await client.post(
                f"/api/sessions/{created.session_id}/turns", json=turn(GOOD)
            )
            assert response.status_code == 200, response.text
        ended = await end(client, created.session_id)
        report = await get_report(client, ended.report_id)

    assert calls == []
    assert report.kind is ReportKind.TURN_TABLE


async def test_logs_do_not_contain_transcript(
    client_for: ClientFactory, metrics_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    secret = "비밀답변문자열"
    llm = FakeLLM(responder=lambda *_: FakeStep(error=LLMTimeoutError("t")))
    app, _, _ = real_app(metrics_path, llm)
    async with client_for(app) as client:
        created = await create(client)
        response = await client.post(f"/api/sessions/{created.session_id}/turns", json=turn(secret))
        assert response.status_code == 200, response.text
        await end(client, created.session_id)

    assert secret not in caplog.text
    assert str(created.session_id) in caplog.text
    assert "session end" in caplog.text
