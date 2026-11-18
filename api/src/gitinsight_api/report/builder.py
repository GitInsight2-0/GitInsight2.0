"""리포트 생성: 빈 판단 재판단, LLM 리포트 초안, 근거 대조, 대비책(턴별 판단 표).

- 역량 점수는 LLM이 매기지만, 근거(turn_id와 인용문)가 실제 자막과 맞지 않으면 버립니다.
- 주제별 다룬 포인트와 빠진 포인트는 서버가 판단 기록에서 계산합니다.
- 코드 위치는 분석 결과의 SymbolRef에서만 가져옵니다.
- 기여 라벨은 프롬프트에 넣지 않습니다. 라벨이 점수에 영향을 줄 수 없게 하기 위해서입니다.
- 답변마다 서버가 기록한 사실(모르겠다고 답함, 힌트를 받은 뒤의 답변)을 프롬프트에 같이 넘깁니다 (#82).
  가장 잘한 답변 하나만 보고 높은 점수를 주지 않게 하기 위해서입니다.
- 근거의 코드 위치는 면접 중 화면에 띄운 코드일 때만 붙입니다.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from itertools import pairwise
from uuid import UUID, uuid4

from gitinsight_api.brain.judge import judge_answer
from gitinsight_api.brain.quotes import EXACT, QuoteMatcher
from gitinsight_api.brain.state import SessionState, TurnRecord
from gitinsight_api.llm import LLMClient, LLMError, Message
from gitinsight_api.llm.untrusted import UNTRUSTED_NOTICE, untrusted_block
from gitinsight_api.report.rubric import rubric_text
from gitinsight_contracts.analysis import AnalysisResult, Topic
from gitinsight_contracts.common import SymbolRef
from gitinsight_contracts.interview import Action
from gitinsight_contracts.llm import CompetencyDraft, CoverageStatus, Intent, ReportDraft
from gitinsight_contracts.report import (
    CodeLink,
    CompetencyScore,
    Evidence,
    Report,
    ReportKind,
    TopicReport,
    TurnRow,
)

logger = logging.getLogger(__name__)

REPORT_PURPOSE = "report"
MAX_LIST_ITEMS = 5
MAX_ITEM_CHARS = 200
MAX_MODEL_ANSWER_CHARS = 800
MAX_REJUDGE_CONCURRENCY = 4

REPORT_SYSTEM_PROMPT = f"""당신은 한국어 기술 모의면접의 평가 리포트를 작성합니다.
아래 루브릭으로 역량마다 1~4점을 매기세요.

{rubric_text()}

규칙:
- 점수는 지원자 답변 내용만으로 매깁니다. 누가 코드를 작성했는지는 고려하지 않습니다.
- 역량마다 근거(evidence)를 하나 이상 답니다. turn_id는 답변 목록에 있는 값을 그대로 쓰고, quote는 그 턴의 답변에서 글자 그대로 옮깁니다.
- 근거로 삼을 답변이 없는 역량은 competencies에 넣지 마세요. 근거가 없다고 낮은 점수를 주지 않습니다.
- 역량의 점수는 가장 잘한 답변 하나가 아니라 그 역량에 해당하는 답변 전체를 보고 매깁니다. 잘한 답변과 못한 답변이 섞여 있으면 그만큼 낮춥니다.
- 답변 목록의 dont_know와 after_hint는 서버가 기록한 사실입니다. dont_know가 true인 답변은 지원자가 그 질문에 모르겠다고 답한 것이고, 그 질문이 묻는 역량의 점수를 낮추는 근거입니다. after_hint가 true인 답변은 면접관의 힌트를 받은 뒤에 한 답변이라, 힌트 없이 한 같은 수준의 답변보다 낮게 봅니다. 힌트를 받은 뒤의 답변만 근거인 역량에는 4점을 주지 않습니다.
- 근거가 코드와 관련 있으면 symbol_id에 주제 자료의 symbol_id를 쓰고, 아니면 null로 둡니다.
- topics에는 면접에서 다룬 주제마다 model_answer(모범 답안)를 씁니다. 주제 요약과 기대 포인트를 근거로 하고, 코드에 없는 내용을 지어내지 마세요.
- strengths, improvements, next_topics는 각각 {MAX_LIST_ITEMS}개 이하의 짧은 문장으로 씁니다.

{UNTRUSTED_NOTICE}"""


def permalink(repo_full_name: str, symbol: SymbolRef) -> str:
    return (
        f"https://github.com/{repo_full_name}/blob/{symbol.commit_sha}/{symbol.path}"
        f"#L{symbol.start_line}-L{symbol.end_line}"
    )


def asked_topic_ids(state: SessionState, turns: list[TurnRecord]) -> list[str]:
    """면접에서 실제로 물어본 주제를 순서대로 돌려줍니다."""
    asked = {t.topic_id for t in turns if t.topic_id} | {state.current_topic_id}
    return [topic_id for topic_id in state.topic_order if topic_id in asked]


def after_hint_turn_ids(turns: list[TurnRecord]) -> set[UUID]:
    """힌트를 받은 뒤에 한 답변의 turn_id입니다. 바로 앞 턴에서 면접관이 같은 주제로 힌트를 준 경우입니다."""
    hinted: set[UUID] = set()
    for before, after in pairwise(turns):
        if before.reply.action is Action.HINT and before.topic_id == after.topic_id:
            hinted.add(after.turn_id)
    return hinted


def shown_symbol_ids(turns: list[TurnRecord], topic: Topic) -> set[str]:
    """면접 중 이 주제에서 화면에 띄운 코드입니다. 주제 코드와, 면접관 발화에 붙은 하이라이트입니다."""
    # 하이라이트는 면접관 발화(reply)에 붙습니다. 다음 주제로 넘어가는 발화의 하이라이트는 새 주제의 것이라
    # 답변한 주제(turn.topic_id)가 아니라 발화의 주제(reply.topic_id)로 가릅니다.
    return {topic.symbol.symbol_id} | {
        t.reply.highlight.symbol_id
        for t in turns
        if t.reply.topic_id == topic.topic_id and t.reply.highlight is not None
    }


def covered_points(turns: list[TurnRecord], topic_id: str) -> set[str]:
    covered: set[str] = set()
    for turn in turns:
        if turn.topic_id == topic_id and turn.judgment is not None:
            covered |= {
                c.point_id for c in turn.judgment.coverage if c.status is CoverageStatus.COVERED
            }
    return covered


def build_report_messages(
    analysis: AnalysisResult, topic_ids: list[str], turns: list[TurnRecord]
) -> list[Message]:
    topics = [
        {
            "topic_id": topic.topic_id,
            "title": topic.title,
            "summary": topic.summary,
            "expected_points": [p.text for p in topic.expected_points],
            "symbols": [
                {"symbol_id": s.symbol_id, "name": s.name}
                for s in (topic.symbol, *topic.related_symbols)
            ],
        }
        for topic in (analysis.topic(t) for t in topic_ids)
    ]
    hinted = after_hint_turn_ids(turns)
    answers = [
        {
            "turn_id": str(t.turn_id),
            "topic_id": t.topic_id,
            "question": t.question,
            "answer": t.transcript,
            # 아래 둘은 서버가 기록한 사실입니다. 기여 라벨은 넣지 않습니다.
            "dont_know": t.judgment is not None and t.judgment.intent is Intent.DONT_KNOW,
            "after_hint": t.turn_id in hinted,
        }
        for t in turns
        if t.transcript.strip()
    ]
    user = "\n\n".join(
        [
            "면접 주제 자료입니다.",
            untrusted_block("topics", json.dumps(topics, ensure_ascii=False)),
            "면접 질문과 지원자 답변(음성 인식 자막) 목록입니다.",
            untrusted_block("answers", json.dumps(answers, ensure_ascii=False)),
        ]
    )
    return [
        {"role": "system", "content": REPORT_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _clean_list(items: list[str]) -> list[str]:
    cleaned = [i.strip() for i in items if i.strip()]
    return [i[:MAX_ITEM_CHARS] for i in cleaned][:MAX_LIST_ITEMS]


def validate_competencies(
    drafts: list[CompetencyDraft],
    turns: list[TurnRecord],
    analysis: AnalysisResult,
    matcher: QuoteMatcher = EXACT,
) -> list[CompetencyScore]:
    """근거가 실제 자막과 맞는 역량만 남깁니다. 근거가 하나도 남지 않으면 그 역량을 버립니다.

    근거의 코드 위치는 면접 중 그 주제에서 화면에 띄운 코드일 때만 붙입니다.
    띄운 적 없는 코드면 인용문은 남기고 코드 위치만 뺍니다.
    """
    by_id = {str(t.turn_id): t for t in turns}
    scores: list[CompetencyScore] = []
    seen = set()
    for draft in drafts:
        if draft.name in seen:
            continue
        evidence: list[Evidence] = []
        for item in draft.evidence:
            turn = by_id.get(item.turn_id.strip())
            if turn is None or not matcher.matches(item.quote, turn.transcript):
                continue
            symbol = None
            if item.symbol_id and turn.topic_id:
                topic = analysis.topic(turn.topic_id)
                if item.symbol_id in shown_symbol_ids(turns, topic):
                    symbol = topic.find_symbol(item.symbol_id)
            evidence.append(Evidence(turn_id=turn.turn_id, quote=item.quote.strip(), symbol=symbol))
        if not evidence:
            logger.info(
                "report_competency_dropped name=%s reason=no_valid_evidence", draft.name.value
            )
            continue
        seen.add(draft.name)
        scores.append(CompetencyScore(name=draft.name, level=draft.level, evidence=evidence))
    return scores


def topic_reports(
    analysis: AnalysisResult,
    topic_ids: list[str],
    turns: list[TurnRecord],
    model_answers: dict[str, str],
) -> list[TopicReport]:
    reports: list[TopicReport] = []
    for topic_id in topic_ids:
        topic = analysis.topic(topic_id)
        covered = covered_points(turns, topic_id)
        used = shown_symbol_ids(turns, topic)
        symbols = [s for s in (topic.symbol, *topic.related_symbols) if s.symbol_id in used]
        answer = model_answers.get(topic_id, "").strip()
        reports.append(
            TopicReport(
                topic_id=topic_id,
                question=topic.base_question,
                covered=[p.text for p in topic.expected_points if p.point_id in covered],
                missing=[p.text for p in topic.expected_points if p.point_id not in covered],
                model_answer=answer[:MAX_MODEL_ANSWER_CHARS] or None,
                code_links=[
                    CodeLink(symbol=s, url=permalink(analysis.repo_full_name, s)) for s in symbols
                ],
            )
        )
    return reports


def turn_rows(turns: list[TurnRecord]) -> list[TurnRow]:
    return [
        TurnRow(
            turn_id=t.turn_id,
            topic_id=t.topic_id,
            question=t.question,
            transcript=t.transcript,
            intent=t.judgment.intent if t.judgment else None,
            covered_points=sorted(
                c.point_id
                for c in (t.judgment.coverage if t.judgment else [])
                if c.status is CoverageStatus.COVERED
            ),
            action=t.reply.action,
            fallback_reason=t.fallback_reason,
        )
        for t in turns
    ]


@dataclass
class ReportBuilder:
    llm: LLMClient
    report_timeout_s: float = 30.0
    rejudge_timeout_s: float = 10.0
    report_model: str | None = None
    judge_model: str | None = None
    # true면 LLM을 부르지 않고 턴별 판단 표만 만듭니다 (장애 대비 스위치).
    force_prepared: bool = False
    # 판단 검증과 같은 인용문 비교 방식을 씁니다.
    quote_matcher: QuoteMatcher = EXACT

    async def rejudge_missing(
        self, analysis: AnalysisResult, state: SessionState, turns: list[TurnRecord]
    ) -> list[TurnRecord]:
        """판단이 비어 있는 턴(시간 초과, 검증 실패 등)을 다시 판단합니다. 저장된 턴은 바꾸지 않습니다."""
        semaphore = asyncio.Semaphore(MAX_REJUDGE_CONCURRENCY)

        async def rejudge(turn: TurnRecord) -> TurnRecord:
            if turn.judgment is not None or not turn.transcript.strip() or turn.topic_id is None:
                return turn
            async with semaphore:
                outcome = await judge_answer(
                    self.llm,
                    analysis.topic(turn.topic_id),
                    turn.question,
                    turn.transcript,
                    state.persona_keywords,
                    timeout_s=self.rejudge_timeout_s,
                    model=self.judge_model,
                    quote_matcher=self.quote_matcher,
                )
            if outcome.judgment is None:
                return turn
            return turn.model_copy(update={"judgment": outcome.judgment})

        return list(await asyncio.gather(*(rejudge(t) for t in turns)))

    async def build(
        self, analysis: AnalysisResult, state: SessionState, turns: list[TurnRecord]
    ) -> Report:
        topic_ids = asked_topic_ids(state, turns)
        if self.force_prepared:
            return self._turn_table(analysis, state, topic_ids, turns)
        turns = await self.rejudge_missing(analysis, state, turns)
        answered = [t for t in turns if t.transcript.strip()]
        if not answered:
            return self._turn_table(analysis, state, topic_ids, turns)
        try:
            result = await self.llm.complete_structured(
                ReportDraft,
                build_report_messages(analysis, topic_ids, turns),
                purpose=REPORT_PURPOSE,
                timeout_s=self.report_timeout_s,
                model=self.report_model,
            )
        except LLMError as error:
            logger.info("report_fallback reason=%s", type(error).__name__)
            return self._turn_table(analysis, state, topic_ids, turns)
        draft = result.value
        allowed = set(topic_ids)
        model_answers = {t.topic_id: t.model_answer for t in draft.topics if t.topic_id in allowed}
        return Report(
            report_id=uuid4(),
            session_id=state.session_id,
            kind=ReportKind.FULL,
            competencies=validate_competencies(
                draft.competencies, turns, analysis, self.quote_matcher
            ),
            topics=topic_reports(analysis, topic_ids, turns, model_answers),
            strengths=_clean_list(draft.strengths),
            improvements=_clean_list(draft.improvements),
            next_topics=_clean_list(draft.next_topics),
            turns=turn_rows(turns),
        )

    def _turn_table(
        self,
        analysis: AnalysisResult,
        state: SessionState,
        topic_ids: list[str],
        turns: list[TurnRecord],
    ) -> Report:
        return Report(
            report_id=uuid4(),
            session_id=state.session_id,
            kind=ReportKind.TURN_TABLE,
            competencies=[],
            topics=topic_reports(analysis, topic_ids, turns, {}),
            strengths=[],
            improvements=[],
            next_topics=[],
            turns=turn_rows(turns),
        )
