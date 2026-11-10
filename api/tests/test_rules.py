"""서버 규칙표 테스트. 표의 한 줄이 규칙 하나입니다."""

from dataclasses import dataclass, field

import pytest

from gitinsight_api.brain.config import InterviewConfig
from gitinsight_api.brain.rules import RuleInput, decide
from gitinsight_api.brain.state import TopicProgress
from gitinsight_contracts.interview import Action
from gitinsight_contracts.llm import CoverageStatus, Followup, Intent, Judgment, PointCoverage

CONFIG = InterviewConfig()
POINTS = ["p1", "p2", "p3", "p4", "p5"]


def judgment(
    intent: Intent = Intent.ANSWER,
    covered: tuple[str, ...] = (),
    wrong: tuple[str, ...] = (),
    followup: bool = True,
) -> Judgment:
    coverage = [
        PointCoverage(point_id=p, status=CoverageStatus.COVERED, evidence_quote="인용")
        for p in covered
    ] + [PointCoverage(point_id=p, status=CoverageStatus.WRONG, evidence_quote=None) for p in wrong]
    return Judgment(
        intent=intent,
        coverage=coverage,
        followup=Followup(question="왜 그렇게 했나요?", symbol_id=None) if followup else None,
    )


@dataclass(frozen=True)
class Case:
    name: str
    judgment: Judgment | None
    expect: Action
    rule: str
    remaining_sec: int = 300
    topic_elapsed_sec: float = 10
    depth: int = 0
    hint_given: bool = False
    probed: tuple[str, ...] = ()
    previously_covered: tuple[str, ...] = ()
    off_topic_count: int = 0
    has_next_topic: bool = True
    prepared_left: int = 2
    probe_point: str | None = None
    extra: dict[str, object] = field(default_factory=dict)


CASES = [
    Case(
        "시간 초과면 판단과 상관없이 마무리",
        judgment(),
        Action.WRAP_UP,
        "time_over",
        remaining_sec=0,
    ),
    Case("시간 초과는 판단이 없어도 마무리", None, Action.WRAP_UP, "time_over", remaining_sec=0),
    Case("판단 없음 → 미리 만든 꼬리질문", None, Action.FOLLOW_UP, "no_judgment"),
    Case(
        "판단 없음, 꼬리질문 소진 → 다음 주제",
        None,
        Action.NEXT_TOPIC,
        "no_judgment:followups_exhausted",
        prepared_left=0,
    ),
    Case(
        "판단 없음, 깊이 한도 → 다음 주제",
        None,
        Action.NEXT_TOPIC,
        "no_judgment:followups_exhausted",
        depth=2,
    ),
    Case("REPEAT → 이전 질문", judgment(Intent.REPEAT), Action.REPEAT, "repeat"),
    Case(
        "REPEAT는 주제 시간 초과보다 우선",
        judgment(Intent.REPEAT),
        Action.REPEAT,
        "repeat",
        topic_elapsed_sec=500,
    ),
    Case("CLARIFY → 취지 설명 후 재질문", judgment(Intent.CLARIFY), Action.CLARIFY, "clarify"),
    Case(
        "주제 시간 예산 초과 → 다음 주제",
        judgment(covered=("p1",)),
        Action.NEXT_TOPIC,
        "topic_budget",
        topic_elapsed_sec=120,
    ),
    Case(
        "첫 주제 이탈 → 같은 질문으로 복귀",
        judgment(Intent.OFF_TOPIC),
        Action.REPEAT,
        "off_topic_back",
        extra={"off_topic": True},
    ),
    Case(
        "두 번째 주제 이탈 → 다음 주제",
        judgment(Intent.OFF_TOPIC),
        Action.NEXT_TOPIC,
        "off_topic_again",
        off_topic_count=1,
        extra={"off_topic": True},
    ),
    Case("모름, 힌트 전 → 힌트", judgment(Intent.DONT_KNOW), Action.HINT, "dont_know_hint"),
    Case(
        "모름, 힌트 후 → 다음 주제",
        judgment(Intent.DONT_KNOW),
        Action.NEXT_TOPIC,
        "dont_know_after_hint",
        hint_given=True,
    ),
    Case(
        "충족률 0.2 → 첫 빠진 포인트 PROBE",
        judgment(covered=("p1",)),
        Action.PROBE,
        "low_coverage_probe",
        probe_point="p2",
    ),
    Case(
        "틀린 포인트는 빠진 것으로 보고 PROBE",
        judgment(wrong=("p1",)),
        Action.PROBE,
        "low_coverage_probe",
        probe_point="p1",
    ),
    Case(
        "이미 물어본 포인트는 건너뛰고 PROBE",
        judgment(covered=("p1",)),
        Action.PROBE,
        "low_coverage_probe",
        probed=("p2", "p3"),
        probe_point="p4",
    ),
    Case(
        "빠진 포인트를 모두 물어봤으면 꼬리질문",
        judgment(covered=("p1",)),
        Action.FOLLOW_UP,
        "follow_up_llm",
        probed=("p2", "p3", "p4", "p5"),
    ),
    Case(
        "충족률 경계 0.4는 PROBE하지 않음",
        judgment(covered=("p1", "p2")),
        Action.FOLLOW_UP,
        "follow_up_llm",
        extra={"use_llm_followup": True},
    ),
    Case(
        "이전 턴의 충족 포인트도 누적",
        judgment(covered=("p2",)),
        Action.FOLLOW_UP,
        "follow_up_llm",
        previously_covered=("p1",),
    ),
    Case(
        "깊이 1 → 꼬리질문",
        judgment(covered=("p1", "p2", "p3")),
        Action.FOLLOW_UP,
        "follow_up_llm",
        depth=1,
    ),
    Case(
        "LLM 꼬리질문 없음 → 미리 만든 꼬리질문",
        judgment(covered=("p1", "p2", "p3"), followup=False),
        Action.FOLLOW_UP,
        "follow_up_prepared",
        extra={"use_llm_followup": False},
    ),
    Case(
        "LLM 꼬리질문 없음, 미리 만든 것도 소진 → 다음 주제",
        judgment(covered=("p1", "p2", "p3"), followup=False),
        Action.NEXT_TOPIC,
        "follow_up_prepared:followups_exhausted",
        prepared_left=0,
    ),
    Case(
        "깊이 2 → 다음 주제",
        judgment(covered=("p1", "p2", "p3")),
        Action.NEXT_TOPIC,
        "topic_done",
        depth=2,
    ),
    Case(
        "남은 주제 없음 → 마무리",
        judgment(covered=("p1", "p2", "p3")),
        Action.WRAP_UP,
        "topic_done:no_more_topics",
        depth=2,
        has_next_topic=False,
    ),
    Case(
        "남은 시간이 새 주제에 부족 → 마무리",
        judgment(covered=("p1", "p2", "p3")),
        Action.WRAP_UP,
        "topic_done:not_enough_time",
        depth=2,
        remaining_sec=29,
    ),
    Case(
        "남은 시간 30초면 새 주제 시작",
        judgment(covered=("p1", "p2", "p3")),
        Action.NEXT_TOPIC,
        "topic_done",
        depth=2,
        remaining_sec=30,
    ),
]


def build_input(case: Case) -> RuleInput:
    return RuleInput(
        remaining_sec=case.remaining_sec,
        topic_elapsed_sec=case.topic_elapsed_sec,
        progress=TopicProgress(
            topic_id="t1",
            depth=case.depth,
            hint_given=case.hint_given,
            probed_points=list(case.probed),
            covered_points=list(case.previously_covered),
            off_topic_count=case.off_topic_count,
        ),
        point_ids=POINTS,
        has_next_topic=case.has_next_topic,
        judgment=case.judgment,
        prepared_followups_left=case.prepared_left,
    )


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_rule_table(case: Case) -> None:
    decision = decide(build_input(case), CONFIG)

    assert decision.action is case.expect
    assert decision.rule == case.rule
    assert decision.probe_point_id == case.probe_point
    for key, value in case.extra.items():
        assert getattr(decision, key) == value


def test_coverage_ratio_is_computed_by_server() -> None:
    case = Case(
        "누적 충족률",
        judgment(covered=("p2", "p9")),
        Action.FOLLOW_UP,
        "",
        previously_covered=("p1",),
    )
    decision = decide(build_input(case), CONFIG)

    # 주제에 없는 포인트(p9)는 세지 않습니다.
    assert decision.covered_after == ("p1", "p2")
    assert decision.coverage_ratio == pytest.approx(0.4)


def test_rule_input_has_no_contribution_label() -> None:
    # 라벨이 규칙 입력에 없어야 라벨이 행동과 점수에 영향을 줄 수 없습니다.
    assert not any("label" in name for name in RuleInput.__dataclass_fields__)
