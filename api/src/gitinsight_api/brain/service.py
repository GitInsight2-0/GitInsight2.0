"""진짜 면접 두뇌. 세션 시작과 턴 처리를 맡습니다.

턴 처리 순서:
1. 세션을 잠그고, 이미 처리한 turn_id면 저장된 응답을 그대로 돌려줍니다.
2. 남은 시간은 서버 시각으로 계산합니다. 클라이언트 시각은 지연 측정에만 씁니다.
3. 시간이 끝났으면 LLM을 부르지 않습니다.
4~5. 제한 시간 안에서 판단하고 검증합니다. 실패하면 판단 없이 진행합니다.
6. 서버 규칙(rules.decide)이 행동을 정합니다.
7. 행동에 맞는 발화문과 하이라이트를 만듭니다. 하이라이트는 항상 분석 결과의 SymbolRef입니다.
8. 턴, 판단, 대체 사유를 저장하고 응답합니다.
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
from gitinsight_api.brain.planner import plan_interview
from gitinsight_api.brain.protocol import (
    AnalysisNotFoundError,
    SessionClosedError,
)
from gitinsight_api.brain.repository import SessionRepository
from gitinsight_api.brain.rules import Decision, RuleInput, decide
from gitinsight_api.brain.state import SessionState, TopicProgress, TurnRecord
from gitinsight_api.llm import LLMClient
from gitinsight_contracts.analysis import AnalysisResult, Topic
from gitinsight_contracts.interview import Action, BrainReply, SessionCreate, TurnRequest
from gitinsight_contracts.llm import Intent, Judgment
from gitinsight_contracts.report import FallbackReason

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


class InterviewBrain:
    def __init__(
        self,
        *,
        analyses: AnalysisSource,
        repository: SessionRepository,
        llm: LLMClient,
        config: InterviewConfig | None = None,
        judge_timeout_s: float = 2.5,
        plan_timeout_s: float = 8.0,
        judge_model: str | None = None,
        plan_model: str | None = None,
        force_prepared: bool = False,
        clock: Clock = utc_now,
        perf_counter: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._analyses = analyses
        self._repository = repository
        self._llm = llm
        self._config = config or InterviewConfig()
        self._judge_timeout_s = judge_timeout_s
        self._plan_timeout_s = plan_timeout_s
        self._judge_model = judge_model
        self._plan_model = plan_model
        # true면 LLM 없이 미리 만든 질문만 씁니다 (장애 대비 스위치 BRAIN_FORCE_PREPARED).
        self._force_prepared = force_prepared
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

    async def start(self, request: SessionCreate) -> tuple[UUID, BrainReply]:
        analysis = await self._analysis(request.analysis_id)
        plan = await plan_interview(
            analysis,
            None if self._force_prepared else request.jd_text,
            llm=self._llm,
            config=self._config,
            timeout_s=self._plan_timeout_s,
            model=self._plan_model,
        )
        topic = analysis.topic(plan.topic_order[0])
        now = self._clock()
        state = SessionState(
            session_id=uuid4(),
            analysis_id=analysis.analysis_id,
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
        )
        await self._repository.create(state)
        reply = BrainReply(
            turn_id=None,
            action=Action.ASK,
            utterance=utterance.join(
                utterance.topic_intro(topic.contribution_label, first=True), topic.base_question
            ),
            highlight=topic.symbol,
            topic_id=topic.topic_id,
            depth=0,
            remaining_sec=state.remaining_sec(now),
            done=False,
        )
        return state.session_id, reply

    async def handle_turn(self, session_id: UUID, request: TurnRequest) -> BrainReply:
        received = self._perf_counter()
        async with self._repository.locked(session_id) as handle:
            existing = await handle.get_turn(request.turn_id)
            if existing is not None:
                return existing.reply
            state = handle.state.model_copy(deep=True)
            if state.closed:
                raise SessionClosedError(session_id)
            analysis = await self._analysis(state.analysis_id)
            topic = analysis.topic(state.current_topic_id)
            question = state.last_question

            now = self._clock()
            remaining = state.remaining_sec(now)
            judgment: Judgment | None = None
            reason: FallbackReason | None = None
            judge_ms: float | None = None
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
                    prepared_followups_left=len(topic.prepared_followups)
                    - state.progress.prepared_used,
                ),
                self._config,
            )
            reply = self._apply(state, analysis, topic, decision, judgment, request.turn_id, now)
            server_ms = (self._perf_counter() - received) * 1000
            turn = TurnRecord(
                session_id=session_id,
                turn_id=request.turn_id,
                index=state.turn_count,
                topic_id=topic.topic_id,
                question=question,
                transcript=request.transcript,
                judgment=judgment,
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
        )
        return outcome.judgment, outcome.fallback_reason, outcome.elapsed_ms

    def _apply(
        self,
        state: SessionState,
        analysis: AnalysisResult,
        topic: Topic,
        decision: Decision,
        judgment: Judgment | None,
        turn_id: UUID,
        now: datetime,
    ) -> BrainReply:
        """결정을 상태에 반영하고 응답을 만듭니다. state를 직접 고칩니다."""
        progress = state.progress
        progress.covered_points = list(decision.covered_after)
        action = decision.action
        highlight = state.last_highlight
        remaining = state.remaining_sec(now)

        if action is Action.WRAP_UP:
            state.closed = True
            state.last_action = action
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
            text = point.probe_question
            state.last_question = text
        elif action is Action.FOLLOW_UP:
            if decision.use_llm_followup and judgment is not None and judgment.followup is not None:
                text = judgment.followup.question
                symbol_id = judgment.followup.symbol_id
            else:
                prepared = topic.prepared_followups[progress.prepared_used]
                progress.prepared_used += 1
                text = prepared.question
                symbol_id = prepared.symbol_id
            highlight = (topic.find_symbol(symbol_id) if symbol_id else None) or topic.symbol
            progress.depth += 1
            state.last_question = text
        elif action is Action.NEXT_TOPIC:
            state.current_index += 1
            topic = analysis.topic(state.current_topic_id)
            state.progress = progress = TopicProgress(
                topic_id=topic.topic_id, started_at_sec=state.elapsed_sec(now)
            )
            highlight = topic.symbol
            text = utterance.join(
                utterance.topic_intro(topic.contribution_label, first=False), topic.base_question
            )
            state.last_question = topic.base_question
        else:
            raise ValueError(f"턴 처리에서 나올 수 없는 행동입니다: {action}")

        state.last_action = action
        state.last_highlight = highlight
        return BrainReply(
            turn_id=turn_id,
            action=action,
            utterance=text,
            highlight=highlight,
            topic_id=topic.topic_id,
            depth=progress.depth,
            remaining_sec=remaining,
            done=False,
        )
