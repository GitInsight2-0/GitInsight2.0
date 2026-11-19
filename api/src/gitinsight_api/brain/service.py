"""진짜 면접 두뇌. 세션 시작과 턴 처리를 맡습니다.

턴 처리 순서:
0. 세션을 잠그기 전에 분석 결과를 읽습니다. 분석 결과는 바뀌지 않는 값입니다.
   잠금 안에서 읽으면 Postgres에서 턴 하나가 연결을 두 개 잡습니다 (세션 행 잠금 + 분석 읽기, PR #57 리뷰).
1. 세션을 잠그고, 이미 처리한 turn_id면 저장된 응답을 그대로 돌려줍니다.
2. 남은 시간은 서버 시각으로 계산합니다. 클라이언트 시각은 지연 측정에만 씁니다.
3. 시간이 끝났으면 LLM을 부르지 않습니다.
4~5. 제한 시간 안에서 판단하고 검증합니다. 실패하면 판단 없이 진행합니다.
6. 서버 규칙(rules.decide)이 행동을 정합니다.
   판단 없이 지나간 답변이 있는 주제에서는 빠진 포인트를 다시 묻지 않습니다 (이미 설명했을 수 있음).
   다른 주제가 다루는 코드를 가리키는 미리 만든 꼬리질문은 쓰지 않습니다 (usable_followups).
7. 행동에 맞는 발화문과 하이라이트를 만듭니다. 하이라이트는 항상 분석 결과의 SymbolRef입니다.
8. 턴, 판단, 대체 사유를 저장하고 응답합니다.

일부러 틀린 말 (#133):
- 주제를 마칠 때 규칙이 MISSTATE를 정하면, 분석 결과의 틀린 말을 그대로 읽습니다. 면접 한 번에 최대 한 번입니다.
- 다음 답변은 요점 판단 대신 틀린 말을 짚었는지만 판단합니다 (brain/misstatement.py).
- 그 답변 뒤에는 짚었으면 인정하는 문장, 아니면 분석 결과의 정답을 다음 질문(또는 마무리) 앞에 붙여 말합니다.
  판단하지 못했어도 정답을 말합니다. 면접자가 틀린 내용을 맞는 것으로 기억하지 않게 하기 위해서입니다.
- LLM을 쓰지 않는 면접(force_prepared)과 다시 보기 면접에서는 하지 않습니다.
"""

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from gitinsight_api.brain import utterance
from gitinsight_api.brain.analysis_source import AnalysisSource
from gitinsight_api.brain.config import InterviewConfig
from gitinsight_api.brain.judge import judge_answer
from gitinsight_api.brain.misstatement import catch_status, judge_misstatement
from gitinsight_api.brain.planner import ReviewSource, plan_interview
from gitinsight_api.brain.protocol import (
    AnalysisNotFoundError,
    NothingToReviewError,
    SessionClosedError,
    SessionNotFoundError,
)
from gitinsight_api.brain.quotes import EXACT, QuoteMatcher
from gitinsight_api.brain.repository import SessionRepository
from gitinsight_api.brain.rules import (
    Decision,
    MisstatementInput,
    RuleInput,
    decide,
    decide_after_misstatement,
)
from gitinsight_api.brain.state import SessionState, TopicProgress, TurnRecord
from gitinsight_api.llm import LLMClient
from gitinsight_contracts.analysis import AnalysisResult, Misstatement, PreparedFollowup, Topic
from gitinsight_contracts.common import LineSpan, SymbolRef
from gitinsight_contracts.interview import Action, BrainReply, SessionCreate, TurnRequest
from gitinsight_contracts.llm import CatchStatus, Intent, Judgment, MisstatementJudgment
from gitinsight_contracts.report import FallbackReason

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def focus_within(focus: LineSpan | None, highlight: SymbolRef | None) -> LineSpan | None:
    """화면에 띄울 코드(highlight) 안의 줄일 때만 focus를 돌려줍니다 (#156).

    BrainReply 계약은 highlight 밖의 focus를 거부합니다. 응답을 만드는 자리마다 맞추지 못한 것이 있어도
    면접 도중 턴이 검증 오류로 실패하지 않게, 여기 한 곳에서 focus만 비웁니다.
    """
    if focus is None or highlight is None or not focus.within(highlight):
        if focus is not None:
            logger.warning("focus_dropped reason=outside_highlight")
        return None
    return focus


def usable_followups(
    analysis: AnalysisResult, topic_order: list[str], topic: Topic
) -> list[PreparedFollowup]:
    """이 주제에서 쓸 수 있는 미리 만든 꼬리질문입니다.

    이번 면접의 다른 주제가 다루는 코드를 가리키는 꼬리질문은 뺍니다.
    그 코드는 자기 주제에서 물으므로, 여기서 먼저 물으면 같은 함수를 두 번 묻게 됩니다.
    """
    other_targets = {
        analysis.topic(topic_id).symbol.symbol_id
        for topic_id in topic_order
        if topic_id != topic.topic_id
    }
    return [
        f
        for f in topic.prepared_followups
        if f.symbol_id is None or f.symbol_id not in other_targets
    ]


class InterviewBrain:
    def __init__(
        self,
        *,
        analyses: AnalysisSource,
        repository: SessionRepository,
        llm: LLMClient,
        config: InterviewConfig | None = None,
        judge_timeout_s: float = 4.0,
        plan_timeout_s: float = 8.0,
        judge_model: str | None = None,
        plan_model: str | None = None,
        force_prepared: bool = False,
        quote_matcher: QuoteMatcher = EXACT,
        clock: Clock = utc_now,
        perf_counter: Callable[[], float] = time.perf_counter,
        review: ReviewSource | None = None,
    ) -> None:
        self._analyses = analyses
        # 지난 면접 기록입니다. 다시 보기 면접에서 답변이 부족했던 주제를 찾는 데만 씁니다.
        self._review = review
        self._repository = repository
        self._llm = llm
        self._config = config or InterviewConfig()
        self._judge_timeout_s = judge_timeout_s
        self._plan_timeout_s = plan_timeout_s
        self._judge_model = judge_model
        self._plan_model = plan_model
        # true면 LLM 없이 미리 만든 질문만 씁니다 (장애 대비 스위치 BRAIN_FORCE_PREPARED).
        self._force_prepared = force_prepared
        self._quote_matcher = quote_matcher
        self._clock = clock
        self._perf_counter = perf_counter

    @property
    def repository(self) -> SessionRepository:
        return self._repository

    async def _analysis(self, analysis_id: str) -> AnalysisResult:
        analysis = await self._analyses.get(analysis_id)
        if analysis is None:
            raise AnalysisNotFoundError(analysis_id)
        return analysis

    async def owner_of(self, session_id: UUID) -> str | None:
        state = await self._repository.get(session_id)
        if state is None:
            raise SessionNotFoundError(session_id)
        return state.owner_id

    async def start(
        self, request: SessionCreate, owner_id: str | None = None
    ) -> tuple[UUID, BrainReply]:
        analysis = await self._analysis(request.analysis_id)
        # 다시 보기 면접은 지난 면접에서 답변이 부족했던 주제만 묻습니다 (#107).
        # 요청하지 않은 면접은 지난 기록을 읽지 않고 늘 같은 순서로 묻습니다.
        only: frozenset[str] | None = None
        if request.review_only:
            only = (
                await self._review.review_topic_ids(owner_id, analysis)
                if self._review is not None
                else frozenset()
            )
            if not only:
                raise NothingToReviewError(request.analysis_id)
        plan = await plan_interview(
            analysis,
            None if self._force_prepared else request.jd_text,
            llm=self._llm,
            config=self._config,
            timeout_s=self._plan_timeout_s,
            model=self._plan_model,
            only=only,
        )
        topic = analysis.topic(plan.topic_order[0])
        intro = utterance.topic_intro(
            topic.contribution_label, first=True, review=request.review_only
        )
        now = self._clock()
        state = SessionState(
            session_id=uuid4(),
            analysis_id=analysis.analysis_id,
            owner_id=owner_id,
            mode=request.mode,
            duration_sec=request.duration_min * 60,
            started_at=now,
            topic_order=plan.topic_order,
            main_topic_count=plan.main_topic_count,
            progress=TopicProgress(topic_id=topic.topic_id),
            last_question=topic.base_question,
            last_action=Action.ASK,
            last_highlight=topic.symbol,
            persona_keywords=plan.persona_keywords,
            plan_source=plan.source,
            review_only=request.review_only,
        )
        await self._repository.create(state)
        reply = BrainReply(
            turn_id=None,
            action=Action.ASK,
            utterance=utterance.join(intro, topic.base_question),
            highlight=topic.symbol,
            topic_id=topic.topic_id,
            depth=0,
            remaining_sec=state.remaining_sec(now),
            done=False,
        )
        return state.session_id, reply

    async def handle_turn(self, session_id: UUID, request: TurnRequest) -> BrainReply:
        received = self._perf_counter()
        unlocked = await self._repository.get(session_id)
        if unlocked is None:
            raise SessionNotFoundError(session_id)
        # analysis_id는 세션을 만든 뒤 바뀌지 않아 잠금 없이 읽습니다.
        loaded = await self._analyses.get(unlocked.analysis_id)
        async with self._repository.locked(session_id) as handle:
            existing = await handle.get_turn(request.turn_id)
            if existing is not None:
                return existing.reply
            state = handle.state.model_copy(deep=True)
            if state.closed:
                raise SessionClosedError(session_id)
            if loaded is None:
                raise AnalysisNotFoundError(state.analysis_id)
            analysis = loaded
            topic = analysis.topic(state.current_topic_id)
            question = state.last_question

            now = self._clock()
            remaining = state.remaining_sec(now)
            judgment: Judgment | None = None
            catch: MisstatementJudgment | None = None
            reason: FallbackReason | None = None
            judge_ms: float | None = None
            # 틀린 말에 답한 뒤 다음 발화 앞에 붙이는 문장(인정 또는 정정)입니다.
            reveal: str | None = None
            prepared = usable_followups(analysis, state.topic_order, topic)
            # 바로 앞에서 면접관이 일부러 틀린 말을 했으면, 이 답변은 그 말을 짚었는지만 봅니다 (#133).
            pending = topic.misstatement if state.misstatement_pending else None
            if pending is not None:
                if remaining > 0:
                    catch, reason, judge_ms = await self._judge_misstatement(
                        topic, pending, request
                    )
                decision = decide_after_misstatement(
                    MisstatementInput(
                        remaining_sec=remaining,
                        progress=state.progress,
                        has_next_topic=state.has_next_topic(),
                        judgment=catch,
                    ),
                    self._config,
                )
                if decision.action is not Action.REPEAT:
                    state.misstatement_pending = False
                    caught = catch_status(catch) is CatchStatus.CAUGHT
                    reveal = utterance.MISSTATE_CAUGHT if caught else pending.correction
            else:
                state.misstatement_pending = False
                if remaining > 0:
                    judgment, reason, judge_ms = await self._judge(state, topic, question, request)
                decision = decide(
                    RuleInput(
                        remaining_sec=remaining,
                        topic_elapsed_sec=state.elapsed_sec(now) - state.progress.started_at_sec,
                        progress=state.progress,
                        point_ids=[p.point_id for p in topic.expected_points],
                        has_next_topic=state.has_next_topic(),
                        judgment=judgment,
                        prepared_followups_left=len(prepared) - state.progress.prepared_used,
                        coverage_unknown=state.progress.unjudged_answers > 0,
                        misstatement_ready=self._misstatement_ready(state, topic),
                    ),
                    self._config,
                )
                if judgment is None:
                    state.progress.unjudged_answers += 1
            reply = self._apply(
                state, analysis, topic, prepared, decision, judgment, request.turn_id, now, reveal
            )
            server_ms = (self._perf_counter() - received) * 1000
            turn = TurnRecord(
                session_id=session_id,
                turn_id=request.turn_id,
                index=state.turn_count,
                topic_id=topic.topic_id,
                question=question,
                transcript=request.transcript,
                judgment=judgment,
                misstatement=pending is not None,
                catch=catch,
                fallback_reason=reason,
                reply=reply,
                received_at=now,
                client_speech_started_at=request.speech_started_at,
                client_speech_ended_at=request.speech_ended_at,
                server_ms=server_ms,
                judge_ms=judge_ms,
            )
            state.turn_count += 1
            await handle.commit(state, turn)
        logger.info(
            "turn session=%s action=%s rule=%s fallback=%s judge_ms=%s server_ms=%.0f",
            session_id,
            decision.action.value,
            decision.rule,
            reason.value if reason else None,
            f"{judge_ms:.0f}" if judge_ms is not None else None,
            server_ms,
        )
        return reply

    async def _judge(
        self, state: SessionState, topic: Topic, question: str, request: TurnRequest
    ) -> tuple[Judgment | None, FallbackReason | None, float | None]:
        if self._force_prepared:
            return None, FallbackReason.FORCED, None
        if not request.transcript.strip():
            # 아무 말도 인식되지 않았으면 LLM 없이 질문을 다시 합니다.
            return Judgment(intent=Intent.REPEAT, coverage=[], followup=None), None, None
        outcome = await judge_answer(
            self._llm,
            topic,
            question,
            request.transcript,
            state.persona_keywords,
            timeout_s=self._judge_timeout_s,
            model=self._judge_model,
            quote_matcher=self._quote_matcher,
        )
        return outcome.judgment, outcome.fallback_reason, outcome.elapsed_ms

    def _misstatement_ready(self, state: SessionState, topic: Topic) -> bool:
        """이 주제에서 일부러 틀린 말을 할 수 있는지입니다. 언제 할지는 규칙이 정합니다."""
        return (
            topic.misstatement is not None
            and state.misstatement_topic_id is None
            # 판단 LLM이 있어야 짚었는지 볼 수 있습니다.
            and not self._force_prepared
            # 다시 보기 면접은 지난 면접에서 부족했던 질문만 다시 묻습니다.
            and not state.review_only
        )

    async def _judge_misstatement(
        self, topic: Topic, misstatement: Misstatement, request: TurnRequest
    ) -> tuple[MisstatementJudgment | None, FallbackReason | None, float | None]:
        if self._force_prepared:
            return None, FallbackReason.FORCED, None
        if not request.transcript.strip():
            # 아무 말도 인식되지 않았으면 LLM 없이 다시 말합니다.
            silent = MisstatementJudgment(
                intent=Intent.REPEAT, status=CatchStatus.MISSED, evidence_quote=None
            )
            return silent, None, None
        outcome = await judge_misstatement(
            self._llm,
            topic,
            misstatement,
            request.transcript,
            timeout_s=self._judge_timeout_s,
            model=self._judge_model,
            quote_matcher=self._quote_matcher,
        )
        return outcome.judgment, outcome.fallback_reason, outcome.elapsed_ms

    def _apply(
        self,
        state: SessionState,
        analysis: AnalysisResult,
        topic: Topic,
        prepared: list[PreparedFollowup],
        decision: Decision,
        judgment: Judgment | None,
        turn_id: UUID,
        now: datetime,
        reveal: str | None = None,
    ) -> BrainReply:
        """결정을 상태에 반영하고 응답을 만듭니다. state를 직접 고칩니다.

        reveal은 일부러 한 틀린 말에 답한 뒤 다음 발화 앞에 붙이는 한 문장입니다 (인정 또는 정정).
        """
        progress = state.progress
        progress.covered_points = list(decision.covered_after)
        action = decision.action
        highlight = state.last_highlight
        # 질문을 다시 말하는 행동(REPEAT, CLARIFY, HINT)은 직전 질문이 가리키던 줄을 그대로 둡니다 (#156).
        focus = state.last_focus
        remaining = state.remaining_sec(now)

        if action is Action.WRAP_UP:
            state.closed = True
            state.last_action = action
            if reveal is not None:
                # 2문장 제한을 지키려고 한 문장짜리 마무리를 붙입니다.
                closing = (
                    utterance.WRAP_UP_TIMEOUT_SHORT
                    if remaining <= 0
                    else utterance.WRAP_UP_DONE_SHORT
                )
                text = utterance.join(reveal, closing)
            else:
                text = utterance.WRAP_UP_TIMEOUT if remaining <= 0 else utterance.WRAP_UP_DONE
            return BrainReply(
                turn_id=turn_id,
                action=action,
                utterance=text,
                highlight=None,
                topic_id=None,
                depth=progress.depth,
                remaining_sec=remaining,
                done=True,
            )

        if action is Action.REPEAT:
            if decision.off_topic:
                progress.off_topic_count += 1
                text = utterance.join(utterance.BACK_TO_QUESTION, state.last_question)
            else:
                text = utterance.join(utterance.REPEAT, state.last_question)
        elif action is Action.CLARIFY:
            text = utterance.join(topic.question_intent, state.last_question)
        elif action is Action.HINT:
            progress.hint_given = True
            highlight = topic.symbol
            text = utterance.join(topic.hint, utterance.HINT_RETRY)
        elif action is Action.PROBE:
            point = next(p for p in topic.expected_points if p.point_id == decision.probe_point_id)
            progress.probed_points.append(point.point_id)
            # 기대 포인트는 주제 코드에 대한 것입니다. 직전 꼬리질문이 다른 심볼을 보여 주고 있었어도 주제 코드로 돌아옵니다.
            highlight = topic.symbol
            # 분석이 이 요점에 고른 줄입니다. 없으면 주제 코드 전체를 봅니다.
            focus = point.focus
            text = point.probe_question
            state.last_question = text
        elif action is Action.FOLLOW_UP:
            if decision.use_llm_followup and judgment is not None and judgment.followup is not None:
                text = judgment.followup.question
                symbol_id = judgment.followup.symbol_id
            else:
                followup = prepared[progress.prepared_used]
                progress.prepared_used += 1
                text = followup.question
                symbol_id = followup.symbol_id
            highlight = (topic.find_symbol(symbol_id) if symbol_id else None) or topic.symbol
            focus = None
            progress.depth += 1
            state.last_question = text
        elif action is Action.NEXT_TOPIC:
            state.current_index += 1
            topic = analysis.topic(state.current_topic_id)
            state.progress = progress = TopicProgress(
                topic_id=topic.topic_id, started_at_sec=state.elapsed_sec(now)
            )
            highlight = topic.symbol
            focus = None
            # 틀린 말에 답한 뒤에는 주제를 시작하는 문장 자리에 인정 또는 정정을 말합니다.
            intro = reveal or utterance.topic_intro(
                topic.contribution_label, first=False, review=state.review_only
            )
            text = utterance.join(intro, topic.base_question)
            state.last_question = topic.base_question
        elif action is Action.MISSTATE:
            if topic.misstatement is None:
                raise ValueError(f"틀린 말이 없는 주제입니다: {topic.topic_id}")
            state.misstatement_topic_id = topic.topic_id
            state.misstatement_pending = True
            # 틀린 말은 주제 코드에 대한 것입니다.
            highlight = topic.symbol
            focus = None
            text = utterance.join(utterance.MISSTATE_INTRO, topic.misstatement.statement)
            state.last_question = topic.misstatement.statement
        else:
            raise ValueError(f"턴 처리에서 나올 수 없는 행동입니다: {action}")

        focus = focus_within(focus, highlight)
        state.last_action = action
        state.last_highlight = highlight
        state.last_focus = focus
        return BrainReply(
            turn_id=turn_id,
            action=action,
            utterance=text,
            highlight=highlight,
            focus=focus,
            topic_id=topic.topic_id,
            depth=progress.depth,
            remaining_sec=remaining,
            done=False,
        )
