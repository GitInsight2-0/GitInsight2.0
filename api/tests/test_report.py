import asyncio
import re
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from gitinsight_api.brain.analysis_source import FixtureAnalysisSource
from gitinsight_api.brain.judge import JUDGE_PURPOSE
from gitinsight_api.brain.protocol import SessionClosedError
from gitinsight_api.brain.repository import InMemorySessionRepository
from gitinsight_api.brain.service import InterviewBrain
from gitinsight_api.llm import LLMTimeoutError
from gitinsight_api.llm.fake import FakeLLM, FakeStep
from gitinsight_api.report.builder import (
    REPORT_PURPOSE,
    ReportBuilder,
    build_report_messages,
    permalink,
)
from gitinsight_api.report.rubric import RUBRIC
from gitinsight_api.report.service import InMemoryReportStore, ReportService
from gitinsight_contracts.analysis import AnalysisResult, ContributionLabel
from gitinsight_contracts.fixtures import load_analysis
from gitinsight_contracts.interview import InterviewMode, SessionCreate, TurnRequest, TurnSource
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
from gitinsight_contracts.report import FallbackReason, ReportKind

ANALYSIS = load_analysis()
ROOT = Path(__file__).resolve().parents[2]
GOOD = "커밋 SHA를 컬렉션 이름에 넣어서 커밋 단위로 캐시합니다."
LATE = "같은 컬렉션이 있으면 GitHub를 다시 부르지 않고 복원합니다."


def judged(*covered: tuple[str, str]) -> Judgment:
    return Judgment(
        intent=Intent.ANSWER,
        coverage=[
            PointCoverage(point_id=p, status=CoverageStatus.COVERED, evidence_quote=q)
            for p, q in covered
        ],
        followup=None,
    )


async def run_interview(
    analysis: AnalysisResult = ANALYSIS,
) -> tuple[UUID, InMemorySessionRepository, list[UUID]]:
    """첫 턴은 판단 성공, 둘째 턴은 판단 시간 초과로 끝나는 짧은 면접입니다."""
    fake = FakeLLM()
    fake.push(JUDGE_PURPOSE, FakeStep(value=judged(("bgr-1", "커밋 SHA를 컬렉션 이름에 넣어서"))))
    fake.push(JUDGE_PURPOSE, FakeStep(delay_s=10))
    repository = InMemorySessionRepository()
    brain = InterviewBrain(
        analyses=FixtureAnalysisSource(analysis),
        repository=repository,
        llm=fake,
        judge_timeout_s=0.1,
    )
    session_id, _ = await brain.start(
        SessionCreate(analysis_id=analysis.analysis_id, mode=InterviewMode.TEXT)
    )
    turn_ids = []
    for transcript in (GOOD, LATE):
        request = TurnRequest(turn_id=uuid4(), transcript=transcript, source=TurnSource.TEXT)
        await brain.handle_turn(session_id, request)
        turn_ids.append(request.turn_id)
    return session_id, repository, turn_ids


def make_service(
    repository: InMemorySessionRepository,
    llm: FakeLLM,
    analysis: AnalysisResult = ANALYSIS,
    **kwargs: object,
) -> ReportService:
    return ReportService(
        analyses=FixtureAnalysisSource(analysis),
        sessions=repository,
        store=InMemoryReportStore(),
        builder=ReportBuilder(llm=llm, **kwargs),  # type: ignore[arg-type]
    )


def draft(turn_ids: list[UUID]) -> ReportDraft:
    first, second = (str(t) for t in turn_ids)
    return ReportDraft(
        competencies=[
            CompetencyDraft(
                name=Competency.CODE_UNDERSTANDING,
                level=3,
                rationale="흐름을 맞게 설명",
                evidence=[
                    EvidenceDraft(
                        turn_id=first,
                        quote="커밋 SHA를 컬렉션 이름에 넣어서",
                        symbol_id="app/agents/builder.py::build_github_repo",
                    ),
                    # 자막에 없는 인용문은 버려야 합니다.
                    EvidenceDraft(turn_id=second, quote="Redis로 캐시합니다", symbol_id=None),
                ],
            ),
            # 근거가 모두 가짜인 역량은 통째로 버려야 합니다.
            CompetencyDraft(
                name=Competency.SCALABILITY_OPERATIONS,
                level=4,
                rationale="근거 없음",
                evidence=[EvidenceDraft(turn_id=str(uuid4()), quote=GOOD, symbol_id=None)],
            ),
            CompetencyDraft(
                name=Competency.COMMUNICATION,
                level=2,
                rationale="짧게 답함",
                evidence=[
                    EvidenceDraft(
                        turn_id=second, quote="다시 부르지 않고", symbol_id="app/made/up.py::x"
                    )
                ],
            ),
        ],
        topics=[
            TopicDraft(topic_id="build-github-repo", model_answer="커밋 SHA로 캐시 키를 만들고..."),
            TopicDraft(topic_id="not-asked", model_answer="무시되어야 합니다"),
        ],
        strengths=["  흐름 설명이 정확함  ", ""],
        improvements=["한계 설명 보완"],
        next_topics=["캐시 무효화"],
    )


async def test_full_report_keeps_only_verified_evidence() -> None:
    session_id, repository, turn_ids = await run_interview()
    llm = FakeLLM()
    llm.push(JUDGE_PURPOSE, FakeStep(value=judged(("bgr-2", "GitHub를 다시 부르지 않고"))))
    llm.push(REPORT_PURPOSE, FakeStep(value=draft(turn_ids)))

    report = await make_service(repository, llm).end_session(session_id)

    assert report.kind is ReportKind.FULL
    assert [c.name for c in report.competencies] == [
        Competency.CODE_UNDERSTANDING,
        Competency.COMMUNICATION,
    ]
    code = report.competencies[0]
    assert [e.quote for e in code.evidence] == ["커밋 SHA를 컬렉션 이름에 넣어서"]
    assert code.evidence[0].symbol == ANALYSIS.topic("build-github-repo").symbol
    # 허용 목록 밖 symbol_id는 근거를 남기되 코드 위치는 붙이지 않습니다.
    assert report.competencies[1].evidence[0].symbol is None
    assert report.strengths == ["흐름 설명이 정확함"]


async def test_timed_out_turn_is_rejudged_for_report() -> None:
    session_id, repository, turn_ids = await run_interview()
    llm = FakeLLM()
    llm.push(JUDGE_PURPOSE, FakeStep(value=judged(("bgr-2", "GitHub를 다시 부르지 않고"))))
    llm.push(REPORT_PURPOSE, FakeStep(value=draft(turn_ids)))

    report = await make_service(repository, llm).end_session(session_id)

    # 첫 턴은 이미 판단이 있어 다시 판단하지 않습니다.
    assert len(llm.calls_for(JUDGE_PURPOSE)) == 1
    topic = report.topics[0]
    assert topic.topic_id == "build-github-repo"
    expected = ANALYSIS.topic("build-github-repo").expected_points
    assert topic.covered == [expected[0].text, expected[1].text]
    assert topic.missing == [expected[2].text, expected[3].text]
    rows = {r.turn_id: r for r in report.turns}
    assert rows[turn_ids[1]].covered_points == ["bgr-2"]
    # 대체 사유는 면접 당시 기록 그대로 둡니다.
    assert rows[turn_ids[1]].fallback_reason is FallbackReason.TIMEOUT
    # 저장된 턴은 바꾸지 않습니다.
    stored = await repository.list_turns(session_id)
    assert stored[1].judgment is None


async def test_model_answers_and_code_links_only_for_asked_topics() -> None:
    session_id, repository, turn_ids = await run_interview()
    llm = FakeLLM()
    llm.push(JUDGE_PURPOSE, FakeStep(error=LLMTimeoutError("t")))
    llm.push(REPORT_PURPOSE, FakeStep(value=draft(turn_ids)))

    report = await make_service(repository, llm).end_session(session_id)

    assert [t.topic_id for t in report.topics] == ["build-github-repo"]
    topic = report.topics[0]
    assert topic.model_answer == "커밋 SHA로 캐시 키를 만들고..."
    urls = [link.url for link in topic.code_links]
    sha = ANALYSIS.commit_sha
    assert urls[0] == (
        f"https://github.com/tttae210-sys/gitinsight_agent/blob/{sha}/app/agents/builder.py#L86-L252"
    )
    # 이 면접은 PROBE와 미리 만든 꼬리질문만 나와서 관련 함수는 하이라이트되지 않았습니다.
    # 화면에 나오지 않은 코드는 링크에 넣지 않습니다.
    assert len(urls) == 1


def test_permalink_format() -> None:
    symbol = ANALYSIS.topics[1].symbol
    assert re.fullmatch(
        r"https://github\.com/[\w.-]+/[\w.-]+/blob/[0-9a-f]{40}/[\w/.-]+#L\d+-L\d+",
        permalink(ANALYSIS.repo_full_name, symbol),
    )


async def test_llm_failure_returns_turn_table() -> None:
    session_id, repository, turn_ids = await run_interview()
    llm = FakeLLM()
    llm.push(JUDGE_PURPOSE, FakeStep(error=LLMTimeoutError("t")))
    llm.push(REPORT_PURPOSE, FakeStep(error=LLMTimeoutError("t")))

    report = await make_service(repository, llm).end_session(session_id)

    assert report.kind is ReportKind.TURN_TABLE
    assert report.competencies == []
    assert [r.turn_id for r in report.turns] == turn_ids
    assert report.turns[0].intent is Intent.ANSWER
    assert report.topics[0].model_answer is None


async def test_force_prepared_report_never_calls_llm() -> None:
    session_id, repository, _ = await run_interview()
    llm = FakeLLM()

    report = await make_service(repository, llm, force_prepared=True).end_session(session_id)

    assert report.kind is ReportKind.TURN_TABLE
    assert llm.calls == []


async def test_end_session_is_idempotent_and_closes_session() -> None:
    session_id, repository, _ = await run_interview()
    llm = FakeLLM(responder=lambda *_: FakeStep(error=LLMTimeoutError("t"), delay_s=0.02))
    service = make_service(repository, llm)

    first, second = await asyncio.gather(
        service.end_session(session_id), service.end_session(session_id)
    )

    assert first.report_id == second.report_id
    assert await service.get(first.report_id) == first
    assert len(llm.calls_for(REPORT_PURPOSE)) == 1
    state = await repository.get(session_id)
    assert state is not None and state.closed
    brain = InterviewBrain(
        analyses=FixtureAnalysisSource(ANALYSIS), repository=repository, llm=FakeLLM()
    )
    with pytest.raises(SessionClosedError):
        await brain.handle_turn(
            session_id, TurnRequest(turn_id=uuid4(), transcript="더", source=TurnSource.TEXT)
        )


async def test_contribution_label_does_not_reach_report_prompt() -> None:
    session_id, repository, _ = await run_interview()
    turns = await repository.list_turns(session_id)
    relabeled = ANALYSIS.model_copy(
        update={
            "topics": [
                t.model_copy(update={"contribution_label": ContributionLabel.UNVERIFIED})
                for t in ANALYSIS.topics
            ]
        }
    )
    topic_ids = ["build-github-repo"]

    original = build_report_messages(ANALYSIS, topic_ids, turns)
    changed = build_report_messages(relabeled, topic_ids, turns)

    assert original == changed
    assert "verified" not in original[1]["content"]
    assert "기여 근거" not in original[1]["content"]


def test_rubric_matches_docs() -> None:
    doc = (ROOT / "docs" / "rubric.md").read_text(encoding="utf-8")
    for competency, levels in RUBRIC.items():
        assert f"(`{competency.value}`)" in doc
        for level, text in enumerate(levels, start=1):
            assert f"| {level} | {text} |" in doc, (competency, level)


async def test_highlighted_related_symbol_gets_code_link() -> None:
    from gitinsight_contracts.llm import Followup

    related = ANALYSIS.topic("build-github-repo").related_symbols[0]
    fake = FakeLLM()
    fake.push(
        JUDGE_PURPOSE,
        FakeStep(
            value=Judgment(
                intent=Intent.ANSWER,
                coverage=[
                    PointCoverage(
                        point_id="bgr-1", status=CoverageStatus.COVERED, evidence_quote="커밋 SHA를"
                    ),
                    PointCoverage(
                        point_id="bgr-2", status=CoverageStatus.COVERED, evidence_quote="캐시합니다"
                    ),
                ],
                followup=Followup(
                    question="연결을 재사용한 이유는 무엇인가요?", symbol_id=related.symbol_id
                ),
            )
        ),
    )
    repository = InMemorySessionRepository()
    brain = InterviewBrain(
        analyses=FixtureAnalysisSource(ANALYSIS), repository=repository, llm=fake
    )
    session_id, _ = await brain.start(
        SessionCreate(analysis_id=ANALYSIS.analysis_id, mode=InterviewMode.TEXT)
    )
    await brain.handle_turn(
        session_id, TurnRequest(turn_id=uuid4(), transcript=GOOD, source=TurnSource.TEXT)
    )

    report = await make_service(repository, FakeLLM(), force_prepared=True).end_session(session_id)

    urls = [link.url for link in report.topics[0].code_links]
    assert urls[1] == permalink(ANALYSIS.repo_full_name, related)
