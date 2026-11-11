import logging

import pytest

from gitinsight_api.brain.judge import JudgmentInvalidError, validate_judgment
from gitinsight_api.brain.quotes import EXACT, QuoteMatcher, fuzzy_alignment, normalize
from gitinsight_api.settings import Settings
from gitinsight_contracts.fixtures import load_analysis
from gitinsight_contracts.llm import (
    CompetencyDraft,
    CoverageStatus,
    EvidenceDraft,
    Intent,
    Judgment,
    PointCoverage,
)

FUZZY = QuoteMatcher(mode="fuzzy")
TOPIC = load_analysis().topic("build-github-repo")
# 음성 인식 자막: "SHA"가 "샤"로, "GitHub"가 "깃허브"로 받아 적혔습니다.
TRANSCRIPT = (
    "먼저 깃허브 API로 최신 커밋 샤를 가져와서 컬렉션 이름에 넣습니다. "
    "같은 컬렉션이 이미 있으면 깃허브를 다시 안 부르고 저장된 청크를 복원해요."
)


@pytest.mark.parametrize(
    ("quote", "exact", "fuzzy"),
    [
        # 공백·문장부호 차이는 두 방식 모두 통과합니다.
        ("최신 커밋 샤를, 가져와서", True, True),
        # 음성 인식 오차는 fuzzy에서만 통과합니다.
        ("최신 커밋 SHA를 가져와서 컬렉션 이름에 넣습니다", False, True),
        ("저장된 청크가 복원해요", False, True),
        # 여러 단어를 바꿔 쓴 문장은 받아 적기 오차가 아니므로 거부합니다.
        ("같은 컬렉션이 이미 있으면 GitHub를 다시 부르지 않고", False, False),
        # 지어낸 인용문은 두 방식 모두 거부합니다.
        ("확장자로 기술 스택을 추정한다", False, False),
        ("커밋 해시로 Redis에 캐시합니다", False, False),
        ("커밋 단위로 캐시하고 있으면 재사용합니다", False, False),
        # 짧은 인용문은 유사도 비교를 하지 않습니다.
        ("커밋 시", False, False),
        ("", False, False),
    ],
)
def test_quote_matching(quote: str, exact: bool, fuzzy: bool) -> None:
    assert EXACT.matches(quote, TRANSCRIPT) is exact
    assert FUZZY.matches(quote, TRANSCRIPT) is fuzzy


@pytest.mark.parametrize(
    ("quote", "transcript"),
    [
        # 자막보다 긴 인용문: 자막 전체가 인용문 안에 있어도 덧붙인 내용은 지어낸 것입니다.
        (
            "캐시를 썼습니다. 그리고 TTL을 30초로 설정해 만료와 무효화를 직접 관리했습니다",
            "캐시를 썼습니다",
        ),
        (f"{TRANSCRIPT} 그리고 TTL을 30초로 설정했습니다", TRANSCRIPT),
        # 자막 앞이나 뒤에 지어낸 내용을 붙인 인용문
        ("레디스를 먼저 띄우고 먼저 깃허브 API로 최신 커밋", TRANSCRIPT),
        ("저장된 청크를 복원해요 그리고 TTL도 설정했어요", TRANSCRIPT),
    ],
)
def test_fuzzy_rejects_text_outside_transcript(quote: str, transcript: str) -> None:
    assert not EXACT.matches(quote, transcript)
    assert not FUZZY.matches(quote, transcript)


def test_appended_sentence_is_rejected_by_outside_chars_not_score() -> None:
    # 유사도(77.9점)는 기준값과 2점 차이라, 기준값을 낮춰도 막히도록 바깥 글자 수로 확인합니다.
    quote = f"{TRANSCRIPT} 그리고 TTL을 30초로 설정했습니다"
    _, outside = fuzzy_alignment(normalize(quote), normalize(TRANSCRIPT))
    assert outside > FUZZY.max_outside_chars
    assert not QuoteMatcher(mode="fuzzy", threshold=50).matches(quote, TRANSCRIPT)


# 긴 인용문 끝에 짧은 주장을 덧붙이면 유사도는 80점을 넘지만 자막 바깥 글자가 많습니다.
LONG_TRANSCRIPT = (
    "먼저 깃허브 API로 최신 커밋 SHA를 가져와서 컬렉션 이름에 넣고요 "
    "같은 커밋이면 저장된 청크를 복원해요"
)
LONG_QUOTE = "깃허브 API로 최신 커밋 SHA를 가져와서 컬렉션 이름에 넣고요 같은 커밋이면 저장된 청크를 복원해요"


@pytest.mark.parametrize("added", [" TTL은 30초", " TTL 30초로 만료", " 레디스로 캐시"])
def test_fuzzy_rejects_short_claim_added_to_long_quote(added: str) -> None:
    assert not EXACT.matches(LONG_QUOTE + added, LONG_TRANSCRIPT)
    assert not FUZZY.matches(LONG_QUOTE + added, LONG_TRANSCRIPT)


@pytest.mark.parametrize(
    "quote",
    [
        # NUL 문자만 있는 인용문
        "\0" * 20,
        # 자막 바깥 패딩과 겹치도록 NUL 문자를 섞은, 지어낸 내용이 붙은 인용문
        "\0" * 10 + LONG_QUOTE + " TTL은 30초" + "\0" * 10,
    ],
)
def test_fuzzy_rejects_quote_with_nul(quote: str) -> None:
    # 패딩(NUL 문자)과 실제 글자가 겹치지 않는다는 전제를 고정합니다.
    assert "\0" not in normalize(quote)
    assert not EXACT.matches(quote, LONG_TRANSCRIPT)
    assert not FUZZY.matches(quote, LONG_TRANSCRIPT)


@pytest.mark.parametrize(
    "quote",
    [
        # 자막 끝 인용문에서 조사 하나가 다르거나 한 글자가 더 붙은 음성 인식 오차는 통과합니다.
        "같은 커밋이면 저장된 청크가 복원해요",
        "같은 커밋이면 저장된 청크를 복원해요요",
        # 자막 맨 앞 인용문의 인식 오차
        "먼저 깃허브 API로 최신 커밋 샤를",
    ],
)
def test_fuzzy_allows_recognition_errors_at_transcript_edges(quote: str) -> None:
    assert not EXACT.matches(quote, LONG_TRANSCRIPT)
    assert FUZZY.matches(quote, LONG_TRANSCRIPT)


def test_rejection_log_has_score_and_outside_chars_only(caplog: pytest.LogCaptureFixture) -> None:
    quote = LONG_QUOTE + " TTL은 30초"
    with caplog.at_level(logging.INFO, logger="gitinsight_api.brain.quotes"):
        assert not FUZZY.matches(quote, LONG_TRANSCRIPT)

    [record] = [r for r in caplog.records if r.name == "gitinsight_api.brain.quotes"]
    message = record.getMessage()
    assert "score=86.3" in message
    assert "outside=5" in message
    # 인용문과 자막 원문은 로그에 남기지 않습니다.
    assert "TTL" not in message
    assert normalize(LONG_QUOTE)[:6] not in message


def test_threshold_is_configurable() -> None:
    strict = QuoteMatcher(mode="fuzzy", threshold=95)
    assert not strict.matches("최신 커밋 SHA를 가져와서 컬렉션 이름에 넣습니다", TRANSCRIPT)


def stt_judgment() -> Judgment:
    return Judgment(
        intent=Intent.ANSWER,
        coverage=[
            PointCoverage(
                point_id="bgr-1",
                status=CoverageStatus.COVERED,
                evidence_quote="최신 커밋 SHA를 가져와서 컬렉션 이름에 넣습니다",
            )
        ],
        followup=None,
    )


def test_judgment_validation_uses_matcher() -> None:
    with pytest.raises(JudgmentInvalidError, match="evidence_quote"):
        validate_judgment(stt_judgment(), TOPIC, TRANSCRIPT)
    assert validate_judgment(stt_judgment(), TOPIC, TRANSCRIPT, FUZZY) == stt_judgment()


def test_report_evidence_uses_matcher() -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    from gitinsight_api.brain.state import TurnRecord
    from gitinsight_api.report.builder import validate_competencies
    from gitinsight_contracts.interview import Action, BrainReply
    from gitinsight_contracts.llm import Competency

    analysis = load_analysis()
    turn_id = uuid4()
    turn = TurnRecord(
        session_id=uuid4(),
        turn_id=turn_id,
        index=0,
        topic_id=TOPIC.topic_id,
        question=TOPIC.base_question,
        transcript=TRANSCRIPT,
        judgment=None,
        fallback_reason=None,
        reply=BrainReply(
            turn_id=turn_id,
            action=Action.FOLLOW_UP,
            utterance="질문입니다?",
            highlight=None,
            topic_id=TOPIC.topic_id,
            depth=1,
            remaining_sec=100,
            done=False,
        ),
        received_at=datetime.now(UTC),
    )
    drafts = [
        CompetencyDraft(
            name=Competency.CODE_UNDERSTANDING,
            level=3,
            rationale="",
            evidence=[
                EvidenceDraft(
                    turn_id=str(turn_id),
                    quote="최신 커밋 SHA를 가져와서 컬렉션 이름에 넣습니다",
                    symbol_id=None,
                )
            ],
        )
    ]

    assert validate_competencies(drafts, [turn], analysis) == []
    assert len(validate_competencies(drafts, [turn], analysis, FUZZY)) == 1


def test_settings_build_matcher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUOTE_MATCH", "fuzzy")
    monkeypatch.setenv("QUOTE_FUZZY_THRESHOLD", "85")
    matcher = Settings(_env_file=None).quote_matcher()  # type: ignore[call-arg]

    assert matcher == QuoteMatcher(mode="fuzzy", threshold=85.0)


def test_settings_default_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUOTE_MATCH", raising=False)
    assert Settings(_env_file=None).quote_matcher() == EXACT  # type: ignore[call-arg]


@pytest.mark.parametrize(("matcher", "fallback"), [(EXACT, "invalid"), (FUZZY, None)])
async def test_brain_passes_matcher_to_judge(matcher: QuoteMatcher, fallback: str | None) -> None:
    from uuid import uuid4

    from gitinsight_api.brain.analysis_source import FixtureAnalysisSource
    from gitinsight_api.brain.repository import InMemorySessionRepository
    from gitinsight_api.brain.service import InterviewBrain
    from gitinsight_api.llm.fake import FakeLLM, FakeStep
    from gitinsight_contracts.interview import InterviewMode, SessionCreate, TurnRequest, TurnSource

    analysis = load_analysis()
    fake = FakeLLM(responder=lambda *_: FakeStep(value=stt_judgment()))
    brain = InterviewBrain(
        analyses=FixtureAnalysisSource(analysis),
        repository=InMemorySessionRepository(),
        llm=fake,
        quote_matcher=matcher,
    )
    session_id, _ = await brain.start(
        SessionCreate(analysis_id=analysis.analysis_id, mode=InterviewMode.VOICE)
    )
    await brain.handle_turn(
        session_id, TurnRequest(turn_id=uuid4(), transcript=TRANSCRIPT, source=TurnSource.VOICE)
    )

    turn = (await brain.repository.list_turns(session_id))[0]
    assert (turn.fallback_reason.value if turn.fallback_reason else None) == fallback
