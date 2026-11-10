"""다음 행동을 정하는 서버 규칙. 순수 함수라서 모든 분기를 표로 테스트합니다.

LLM의 판단(Judgment)은 근거로만 쓰고, 행동은 이 규칙이 정합니다.
기여 라벨은 입력에 들어오지 않습니다. 라벨이 행동이나 점수에 영향을 줄 수 없게 하기 위해서입니다.
"""

from dataclasses import dataclass, replace

from gitinsight_api.brain.config import InterviewConfig
from gitinsight_api.brain.state import TopicProgress
from gitinsight_contracts.interview import Action
from gitinsight_contracts.llm import CoverageStatus, Intent, Judgment


@dataclass(frozen=True)
class RuleInput:
    remaining_sec: int
    # 현재 주제를 시작한 뒤 흐른 시간(초)입니다.
    topic_elapsed_sec: float
    progress: TopicProgress
    # 현재 주제의 기대 포인트 ID(분석 결과 순서)입니다.
    point_ids: list[str]
    has_next_topic: bool
    # 검증을 통과한 판단입니다. 시간 초과·검증 실패면 None입니다.
    judgment: Judgment | None
    prepared_followups_left: int


@dataclass(frozen=True)
class Decision:
    action: Action
    # 어떤 규칙이 적용됐는지 남깁니다. 로그와 테스트에 씁니다.
    rule: str
    probe_point_id: str | None = None
    # FOLLOW_UP에서 LLM이 만든 꼬리질문을 쓸지, 미리 만든 꼬리질문을 쓸지입니다.
    use_llm_followup: bool = False
    off_topic: bool = False
    # 이 턴까지 누적으로 다룬 포인트와 충족률입니다.
    covered_after: tuple[str, ...] = ()
    coverage_ratio: float = 0.0


def covered_after(rule_input: RuleInput) -> tuple[str, ...]:
    covered = set(rule_input.progress.covered_points)
    if rule_input.judgment is not None:
        covered |= {
            c.point_id for c in rule_input.judgment.coverage if c.status is CoverageStatus.COVERED
        }
    return tuple(p for p in rule_input.point_ids if p in covered)


def _next_topic(rule_input: RuleInput, config: InterviewConfig, rule: str) -> Decision:
    if not rule_input.has_next_topic:
        return Decision(Action.WRAP_UP, f"{rule}:no_more_topics")
    if rule_input.remaining_sec < config.min_sec_for_new_topic:
        return Decision(Action.WRAP_UP, f"{rule}:not_enough_time")
    return Decision(Action.NEXT_TOPIC, rule)


def _prepared_followup(rule_input: RuleInput, config: InterviewConfig, rule: str) -> Decision:
    if rule_input.progress.depth < config.max_depth and rule_input.prepared_followups_left > 0:
        return Decision(Action.FOLLOW_UP, rule)
    return _next_topic(rule_input, config, f"{rule}:followups_exhausted")


def _decide(
    rule_input: RuleInput, config: InterviewConfig, ratio: float, covered: set[str]
) -> Decision:
    progress = rule_input.progress

    # 1. 시간이 끝나면 LLM 판단과 상관없이 마무리합니다.
    if rule_input.remaining_sec <= 0:
        return Decision(Action.WRAP_UP, "time_over")

    judgment = rule_input.judgment
    # 판단이 없으면(시간 초과·검증 실패) 미리 만든 꼬리질문, 소진 시 다음 주제입니다.
    if judgment is None:
        return _prepared_followup(rule_input, config, "no_judgment")

    # 2~3. 다시 말해 달라는 요청과 취지 확인은 주제 시간과 상관없이 응합니다.
    if judgment.intent is Intent.REPEAT:
        return Decision(Action.REPEAT, "repeat")
    if judgment.intent is Intent.CLARIFY:
        return Decision(Action.CLARIFY, "clarify")

    # 주제에 쓴 시간이 예산을 넘으면 다음 주제로 넘어갑니다.
    if rule_input.topic_elapsed_sec >= config.topic_budget_sec:
        return _next_topic(rule_input, config, "topic_budget")

    # 4. 주제 이탈: 처음은 같은 질문으로 돌아가고, 다시 이탈하면 다음 주제입니다.
    if judgment.intent is Intent.OFF_TOPIC:
        if progress.off_topic_count < config.max_off_topic:
            return Decision(Action.REPEAT, "off_topic_back", off_topic=True)
        return replace(_next_topic(rule_input, config, "off_topic_again"), off_topic=True)

    # 5. 모른다고 하면 힌트를 한 번 주고, 이미 줬으면 다음 주제입니다.
    if judgment.intent is Intent.DONT_KNOW:
        if not progress.hint_given:
            return Decision(Action.HINT, "dont_know_hint")
        return _next_topic(rule_input, config, "dont_know_after_hint")

    # 6. 답변이 기대 포인트를 충분히 다루지 못했으면 아직 묻지 않은 빠진 포인트를 묻습니다.
    if ratio < config.probe_threshold:
        missing = [
            p for p in rule_input.point_ids if p not in covered and p not in progress.probed_points
        ]
        if missing:
            return Decision(Action.PROBE, "low_coverage_probe", probe_point_id=missing[0])

    # 7. 깊이가 남았으면 꼬리질문입니다. 검증된 LLM 꼬리질문이 없으면 미리 만든 꼬리질문을 씁니다.
    if progress.depth < config.max_depth:
        if judgment.followup is not None:
            return Decision(Action.FOLLOW_UP, "follow_up_llm", use_llm_followup=True)
        return _prepared_followup(rule_input, config, "follow_up_prepared")

    # 8. 그 외에는 다음 주제, 남은 주제가 없으면 마무리입니다.
    return _next_topic(rule_input, config, "topic_done")


def decide(rule_input: RuleInput, config: InterviewConfig) -> Decision:
    covered = covered_after(rule_input)
    total = len(rule_input.point_ids)
    ratio = len(covered) / total if total else 1.0
    decision = _decide(rule_input, config, ratio, set(covered))
    return replace(decision, covered_after=covered, coverage_ratio=ratio)
