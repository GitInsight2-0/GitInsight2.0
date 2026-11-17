from uuid import uuid4

import pytest

from gitinsight_api.brain.factory import (
    build_analysis_llm,
    build_fixture_brain,
    build_report_service,
)
from gitinsight_api.llm.fake import FakeLLM
from gitinsight_api.settings import PROVIDER_MODELS, Settings
from gitinsight_contracts.interview import (
    Action,
    InterviewMode,
    SessionCreate,
    TurnRequest,
    TurnSource,
)
from gitinsight_contracts.report import FallbackReason, ReportKind

MODEL_ENV = (
    "LLM_PROVIDER",
    "LLM_JUDGE_MODEL",
    "LLM_ANALYSIS_MODEL",
    "LLM_REPORT_MODEL",
    "BRAIN_FORCE_PREPARED",
    "LLM_ANALYSIS_REASONING_EFFORT",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in MODEL_ENV:
        monkeypatch.delenv(name, raising=False)


def settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_default_provider_is_gemini() -> None:
    # 판단·분석·리포트 LLM도 Gemini만 쓰기로 했습니다 (#63). OPENAI_API_KEY 없이 기본 설정으로 돌아야 합니다.
    s = settings()
    assert s.llm_provider == "gemini"
    assert s.llm_judge_model == PROVIDER_MODELS["gemini"]["llm_judge_model"]
    assert s.llm_analysis_model == PROVIDER_MODELS["gemini"]["llm_analysis_model"]
    assert s.llm_report_model == PROVIDER_MODELS["gemini"]["llm_report_model"]


def test_openai_can_still_be_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    s = settings()

    assert s.llm_provider == "openai"
    assert s.llm_judge_model == PROVIDER_MODELS["openai"]["llm_judge_model"]
    assert s.llm_report_model == PROVIDER_MODELS["openai"]["llm_report_model"]


def test_provider_switch_changes_all_default_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "Gemini")
    s = settings()

    assert s.llm_provider == "gemini"
    assert s.llm_judge_model == "gemini/gemini-3.8-flash"
    assert s.llm_analysis_model == "gemini/gemini-3.8-flash"
    assert s.llm_report_model == "gemini/gemini-3.8-flash"


def test_explicit_model_wins_and_empty_model_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_JUDGE_MODEL", "gemini/custom-judge")
    monkeypatch.setenv("LLM_REPORT_MODEL", "")
    s = settings()

    assert s.llm_judge_model == "gemini/custom-judge"
    assert s.llm_report_model == "gemini/gemini-3.8-flash"


def test_analysis_reasoning_effort_defaults_to_low(monkeypatch: pytest.MonkeyPatch) -> None:
    # 분석 지연이 44~57초에서 7~9초로 줄어든 설정입니다(docs/evals/stage2-2026-11-12.md).
    assert settings().llm_analysis_reasoning_effort == "low"
    assert build_analysis_llm(settings())._optional_params == {"reasoning_effort": "low"}

    # 비워 두면 모델 기본값을 씁니다.
    monkeypatch.setenv("LLM_ANALYSIS_REASONING_EFFORT", "")
    assert settings().llm_analysis_reasoning_effort is None
    assert build_analysis_llm(settings())._optional_params == {}


def test_unknown_provider_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "upstage")
    with pytest.raises(ValueError, match="llm_provider"):
        settings()


async def test_force_prepared_switch_keeps_llm_out_of_interview_and_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_FORCE_PREPARED", "true")
    s = settings()
    fake = FakeLLM()
    brain = build_fixture_brain(s, llm=fake)
    analysis_id = "demo-gitinsight-agent"

    session_id, _ = await brain.start(
        SessionCreate(analysis_id=analysis_id, mode=InterviewMode.VOICE, jd_text="LangGraph")
    )
    reply = await brain.handle_turn(
        session_id, TurnRequest(turn_id=uuid4(), transcript="답변", source=TurnSource.VOICE)
    )
    from gitinsight_contracts.fixtures import load_analysis

    report = await build_report_service(s, brain, load_analysis(), llm=fake).end_session(session_id)

    assert reply.action is Action.FOLLOW_UP
    assert fake.calls == []
    assert report.kind is ReportKind.TURN_TABLE
    assert report.turns[0].fallback_reason is FallbackReason.FORCED
