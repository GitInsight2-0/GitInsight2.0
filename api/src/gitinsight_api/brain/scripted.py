"""음성 PoC용 대본 면접 두뇌. 답변 내용과 상관없이 샘플 데이터의 질문을 순서대로 돌려줍니다.

LAB_ENABLED=true일 때만 연결합니다. 진짜 면접 두뇌와 같은 경로와 계약을 써서
C의 음성 어댑터를 나중에 고치지 않아도 되게 합니다.
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from gitinsight_api.brain import utterance
from gitinsight_api.brain.protocol import AnalysisNotFoundError, SessionNotFoundError
from gitinsight_contracts.analysis import AnalysisResult
from gitinsight_contracts.common import SymbolRef
from gitinsight_contracts.interview import Action, BrainReply, SessionCreate, TurnRequest


@dataclass(frozen=True)
class _Line:
    action: Action
    question: str
    topic_id: str
    depth: int
    highlight: SymbolRef | None


@dataclass
class _ScriptedSession:
    started_at: float
    duration_sec: int
    owner_id: str | None = None
    position: int = 0
    replies: dict[UUID, BrainReply] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def build_script(analysis: AnalysisResult) -> list[_Line]:
    lines: list[_Line] = []
    for index, topic in enumerate(analysis.topics):
        lines.append(
            _Line(
                action=Action.ASK if index == 0 else Action.NEXT_TOPIC,
                question=topic.base_question,
                topic_id=topic.topic_id,
                depth=0,
                highlight=topic.symbol,
            )
        )
        for depth, followup in enumerate(topic.prepared_followups, start=1):
            symbol = topic.find_symbol(followup.symbol_id) if followup.symbol_id else None
            lines.append(
                _Line(
                    action=Action.FOLLOW_UP,
                    question=followup.question,
                    topic_id=topic.topic_id,
                    depth=depth,
                    highlight=symbol or topic.symbol,
                )
            )
    return lines


class ScriptedBrain:
    def __init__(
        self,
        analyses: dict[str, AnalysisResult],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._analyses = analyses
        self._scripts = {key: build_script(a) for key, a in analyses.items()}
        self._clock = clock
        self._sessions: dict[UUID, tuple[str, _ScriptedSession]] = {}

    async def owner_of(self, session_id: UUID) -> str | None:
        try:
            _, session = self._sessions[session_id]
        except KeyError:
            raise SessionNotFoundError(session_id) from None
        return session.owner_id

    async def start(
        self, request: SessionCreate, owner_id: str | None = None
    ) -> tuple[UUID, BrainReply]:
        if request.analysis_id not in self._analyses:
            raise AnalysisNotFoundError(request.analysis_id)
        session_id = uuid4()
        session = _ScriptedSession(
            started_at=self._clock(), duration_sec=request.duration_min * 60, owner_id=owner_id
        )
        self._sessions[session_id] = (request.analysis_id, session)
        line = self._scripts[request.analysis_id][0]
        reply = self._reply(None, line, session, utterance.join(utterance.GREETING, line.question))
        return session_id, reply

    async def handle_turn(self, session_id: UUID, request: TurnRequest) -> BrainReply:
        try:
            analysis_id, session = self._sessions[session_id]
        except KeyError:
            raise SessionNotFoundError(session_id) from None
        async with session.lock:
            if request.turn_id in session.replies:
                return session.replies[request.turn_id]
            script = self._scripts[analysis_id]
            session.position += 1
            remaining = self._remaining(session)
            if remaining <= 0 or session.position >= len(script):
                text = utterance.WRAP_UP_TIMEOUT if remaining <= 0 else utterance.WRAP_UP_DONE
                reply = BrainReply(
                    turn_id=request.turn_id,
                    action=Action.WRAP_UP,
                    utterance=text,
                    highlight=None,
                    topic_id=None,
                    depth=0,
                    remaining_sec=remaining,
                    done=True,
                )
            else:
                line = script[session.position]
                text = (
                    utterance.join(utterance.NEXT_TOPIC, line.question)
                    if line.action is Action.NEXT_TOPIC
                    else line.question
                )
                reply = self._reply(request.turn_id, line, session, text)
            session.replies[request.turn_id] = reply
            return reply

    def _remaining(self, session: _ScriptedSession) -> int:
        elapsed = self._clock() - session.started_at
        return max(0, int(session.duration_sec - elapsed))

    def _reply(
        self, turn_id: UUID | None, line: _Line, session: _ScriptedSession, text: str
    ) -> BrainReply:
        return BrainReply(
            turn_id=turn_id,
            action=line.action,
            utterance=text,
            highlight=line.highlight,
            topic_id=line.topic_id,
            depth=line.depth,
            remaining_sec=self._remaining(session),
            done=False,
        )
