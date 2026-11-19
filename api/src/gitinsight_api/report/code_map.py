"""설명 못 한 코드 지도 (#107). 턴 기록을 집계해서 만들고 LLM은 부르지 않습니다.

- 질문받은 함수는 그 질문을 할 때 화면에 띄운 코드입니다 (면접관 발화의 하이라이트, 분석 결과의 SymbolRef).
- 상태는 판단 의도(답변, 모르겠다고 답함), 힌트 행동, 다룬 포인트로 정합니다. 기여 라벨은 쓰지 않습니다.
- 판단이 없는 답변만 있는 함수는 설명했는지 알 수 없어서 지도에 넣지 않습니다.
- 누적 숫자는 같은 사용자가 같은 레포로 본 면접의 코드 지도를 합쳐, 함수마다 가장 최근 상태로 셉니다.
- 다시 보기 면접에서 물을 주제(막혔거나 힌트를 받고 설명한 함수의 주제)도 여기서 고릅니다.
"""

from collections.abc import Sequence
from itertools import pairwise
from typing import TYPE_CHECKING
from uuid import UUID

from gitinsight_api.brain.state import SessionState, TurnRecord
from gitinsight_contracts.analysis import AnalysisResult
from gitinsight_contracts.common import SymbolRef
from gitinsight_contracts.interview import Action
from gitinsight_contracts.llm import CoverageStatus, Intent
from gitinsight_contracts.report import CodeMap, CodeMapEntry, ExplainStatus, Report

if TYPE_CHECKING:
    # service.py가 builder.py를 거쳐 이 모듈을 불러오므로 타입 검사 때만 가져옵니다.
    from gitinsight_api.report.service import ReportStore

# 지난 코드 지도를 찾을 때 보는 최근 리포트 수입니다.
HISTORY_LIMIT = 20
# 다시 보기 면접에서 다시 묻는 상태입니다. 힌트를 받고 설명한 함수도 힌트 없이 다시 설명해 보게 합니다.
REVIEW_STATUSES = frozenset({ExplainStatus.STUCK, ExplainStatus.EXPLAINED_AFTER_HINT})


def after_hint_turn_ids(turns: list[TurnRecord]) -> set[UUID]:
    """힌트를 받은 뒤에 한 답변의 turn_id입니다. 바로 앞 턴에서 면접관이 같은 주제로 힌트를 준 경우입니다."""
    hinted: set[UUID] = set()
    for before, after in pairwise(turns):
        if before.reply.action is Action.HINT and before.topic_id == after.topic_id:
            hinted.add(after.turn_id)
    return hinted


def permalink(repo_full_name: str, symbol: SymbolRef) -> str:
    return (
        f"https://github.com/{repo_full_name}/blob/{symbol.commit_sha}/{symbol.path}"
        f"#L{symbol.start_line}-L{symbol.end_line}"
    )


def asked_symbols(
    analysis: AnalysisResult, state: SessionState, turns: list[TurnRecord]
) -> list[tuple[TurnRecord, SymbolRef]]:
    """답변마다, 그 답변이 받은 질문이 가리킨 코드를 붙입니다.

    첫 질문은 첫 주제의 코드를 띄웁니다. 그 뒤 질문은 바로 앞 면접관 발화의 하이라이트를 띄웁니다.
    """
    shown: SymbolRef | None = analysis.topic(state.topic_order[0]).symbol
    asked: list[tuple[TurnRecord, SymbolRef]] = []
    for turn in turns:
        if shown is not None and turn.topic_id is not None:
            asked.append((turn, shown))
        shown = turn.reply.highlight
    return asked


def _has_covered_point(turn: TurnRecord) -> bool:
    return turn.judgment is not None and any(
        c.status is CoverageStatus.COVERED for c in turn.judgment.coverage
    )


def explain_status(
    turns: list[TurnRecord], *, main_symbol: bool, after_hint: set[UUID]
) -> ExplainStatus | None:
    """한 함수를 물은 질문에 대한 답변들(순서대로)로 상태를 정합니다. 알 수 없으면 None입니다.

    after_hint는 면접 전체에서 힌트를 받은 뒤에 한 답변의 turn_id입니다.

    - 답변도 모르겠다는 말도 없으면(판단 없음, 되묻기, 다른 이야기만 있음) 알 수 없습니다.
    - 마지막이 모르겠다는 답이면 막힘입니다.
    - 주제의 주 코드인데 답변이 기대 포인트를 하나도 다루지 못했으면 막힘입니다.
      꼬리질문으로 본 다른 코드는 기대 포인트가 없어서 이 규칙을 쓰지 않습니다.
    - 이 함수를 묻는 질문에서 힌트를 받았거나, 답변이 모두 힌트를 받은 뒤의 답변이면 힌트 받고 설명함입니다.
    - 그 밖에는 설명함입니다.
    """
    meaningful = [
        t
        for t in turns
        if t.judgment is not None and t.judgment.intent in (Intent.ANSWER, Intent.DONT_KNOW)
    ]
    if not meaningful:
        return None
    last = meaningful[-1].judgment
    if last is not None and last.intent is Intent.DONT_KNOW:
        return ExplainStatus.STUCK
    if main_symbol and not any(_has_covered_point(t) for t in meaningful):
        return ExplainStatus.STUCK
    answers = [
        t for t in meaningful if t.judgment is not None and t.judgment.intent is Intent.ANSWER
    ]
    hinted_here = any(t.reply.action is Action.HINT for t in turns)
    if hinted_here or all(t.turn_id in after_hint for t in answers):
        return ExplainStatus.EXPLAINED_AFTER_HINT
    return ExplainStatus.EXPLAINED


def build_entries(
    analysis: AnalysisResult, state: SessionState, turns: list[TurnRecord]
) -> list[CodeMapEntry]:
    """이번 면접에서 질문받은 함수와 상태를 처음 물은 순서대로 돌려줍니다."""
    by_symbol: dict[str, tuple[SymbolRef, str, list[TurnRecord]]] = {}
    for turn, symbol in asked_symbols(analysis, state, turns):
        if turn.topic_id is None:
            continue
        by_symbol.setdefault(symbol.symbol_id, (symbol, turn.topic_id, []))[2].append(turn)
    after_hint = after_hint_turn_ids(turns)
    entries: list[CodeMapEntry] = []
    for symbol, topic_id, symbol_turns in by_symbol.values():
        is_main = analysis.topic(topic_id).symbol.symbol_id == symbol.symbol_id
        status = explain_status(symbol_turns, main_symbol=is_main, after_hint=after_hint)
        if status is None:
            continue
        entries.append(
            CodeMapEntry(
                symbol=symbol,
                url=permalink(analysis.repo_full_name, symbol),
                topic_id=topic_id,
                status=status,
            )
        )
    return entries


def latest_statuses(maps: Sequence[CodeMap]) -> dict[str, ExplainStatus]:
    """코드 지도 여러 개(오래된 것부터)를 합쳐 함수마다 가장 최근 상태를 돌려줍니다."""
    latest: dict[str, ExplainStatus] = {}
    for code_map in maps:
        for entry in code_map.entries:
            latest[entry.symbol.symbol_id] = entry.status
    return latest


def build_code_map(
    analysis: AnalysisResult,
    state: SessionState,
    turns: list[TurnRecord],
    previous: Sequence[CodeMap] = (),
) -> CodeMap:
    """previous는 같은 사용자가 같은 레포로 본 지난 면접의 코드 지도입니다 (오래된 것부터)."""
    entries = build_entries(analysis, state, turns)
    code_map = CodeMap(
        analysis_id=analysis.analysis_id,
        repo_full_name=analysis.repo_full_name,
        entries=entries,
        key_function_count=0,
        explained_count=0,
        after_hint_count=0,
        stuck_count=0,
    )
    latest = latest_statuses([*previous, code_map])
    # 함수 하나가 주제 여러 개의 주 코드일 수 있어서 symbol_id로 한 번만 셉니다.
    key_ids = {topic.symbol.symbol_id for topic in analysis.topics}
    statuses = [latest[symbol_id] for symbol_id in key_ids if symbol_id in latest]
    return code_map.model_copy(
        update={
            "key_function_count": len(key_ids),
            "explained_count": statuses.count(ExplainStatus.EXPLAINED),
            "after_hint_count": statuses.count(ExplainStatus.EXPLAINED_AFTER_HINT),
            "stuck_count": statuses.count(ExplainStatus.STUCK),
        }
    )


def repo_code_maps(reports: Sequence[Report], repo_full_name: str) -> list[CodeMap]:
    """리포트 목록(오래된 것부터)에서 그 레포의 코드 지도만 고릅니다."""
    return [
        r.code_map
        for r in reports
        if r.code_map is not None and r.code_map.repo_full_name == repo_full_name
    ]


def review_topic_ids(analysis: AnalysisResult, maps: Sequence[CodeMap]) -> frozenset[str]:
    """다시 보기 면접에서 물을 주제입니다. maps는 이 레포의 지난 코드 지도입니다 (오래된 것부터).

    함수마다 가장 최근 상태가 막힘이거나 힌트 받고 설명함이면 답변이 부족했던 것으로 봅니다.
    다시 보기 면접에서 힌트 없이 설명하면 그 함수는 다음 다시 보기에서 빠집니다.
    """
    weak_symbols = {
        symbol_id
        for symbol_id, status in latest_statuses(maps).items()
        if status in REVIEW_STATUSES
    }
    main_ids = {topic.symbol.symbol_id for topic in analysis.topics}
    weak: list[str] = []
    for topic in analysis.topics:
        # 꼬리질문으로 본 코드에서 부족했고 그 코드가 주제의 주 코드가 아니면, 그 코드를 관련 코드로 가진 주제에서 다시 묻습니다.
        related = {s.symbol_id for s in topic.related_symbols} - main_ids
        if topic.symbol.symbol_id in weak_symbols or related & weak_symbols:
            weak.append(topic.topic_id)
    return frozenset(weak)


class ReportHistory:
    """저장된 리포트에서 지난 코드 지도를 읽습니다. 면접 두뇌(ReviewSource)와 리포트 서비스가 같이 씁니다."""

    def __init__(self, store: "ReportStore") -> None:
        self._store = store

    async def code_maps(self, owner_id: str | None, repo_full_name: str) -> list[CodeMap]:
        """그 사용자가 그 레포로 본 지난 면접의 코드 지도입니다 (오래된 것부터). 로그인 사용자가 없으면 비어 있습니다."""
        if owner_id is None:
            return []
        saved = await self._store.list_by_owner(owner_id, HISTORY_LIMIT)
        return repo_code_maps([item.report for item in saved], repo_full_name)

    async def review_topic_ids(
        self, owner_id: str | None, analysis: AnalysisResult
    ) -> frozenset[str]:
        """그 사용자가 그 레포로 본 지난 면접에서 답변이 부족했던 주제입니다."""
        maps = await self.code_maps(owner_id, analysis.repo_full_name)
        return review_topic_ids(analysis, maps)
