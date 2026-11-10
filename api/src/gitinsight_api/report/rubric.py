"""리포트 프롬프트에 넣는 루브릭. docs/rubric.md와 문장이 같아야 합니다 (test_report.py가 검사)."""

from gitinsight_contracts.llm import COMPETENCY_TEXT, Competency

RUBRIC: dict[Competency, tuple[str, str, str, str]] = {
    Competency.CODE_UNDERSTANDING: (
        "함수가 하는 일을 설명하지 못하거나 코드와 다른 동작을 말합니다.",
        "함수의 큰 역할은 말하지만 입력, 출력, 분기 같은 흐름이 빠지거나 일부 틀립니다.",
        "주요 흐름과 분기를 코드와 맞게 설명합니다.",
        "흐름에 더해 경계 상황(예외, 빈 입력, 캐시 미스)과 그때의 동작까지 정확히 설명합니다.",
    ),
    Competency.DESIGN_JUDGMENT: (
        "왜 이렇게 만들었는지 말하지 못합니다.",
        "선택한 방식은 말하지만 다른 방법과 비교하지 못합니다.",
        "대안 하나 이상과 비교해 선택 이유를 말합니다.",
        "대안과의 트레이드오프(성능, 유지보수, 비용)를 구체적으로 말하고, 지금 다시 한다면 무엇을 바꿀지 근거와 함께 말합니다.",
    ),
    Competency.PROBLEM_SOLVING: (
        "실패 상황을 떠올리지 못합니다.",
        "실패 상황은 말하지만 원인 추적이나 대응 방법이 막연합니다.",
        "실패 원인을 찾는 순서와 대응 방법을 구체적으로 말합니다.",
        "대응에 더해 재발 방지(테스트, 모니터링, 제한 시간, 대비책)까지 말합니다.",
    ),
    Competency.SCALABILITY_OPERATIONS: (
        "트래픽이나 데이터가 늘었을 때의 영향을 말하지 못합니다.",
        "병목 후보는 말하지만 근거나 해결 방법이 없습니다.",
        "병목을 근거와 함께 짚고 해결 방법을 하나 이상 말합니다.",
        "해결 방법의 비용과 측정 방법(지표, 부하 테스트)까지 말합니다.",
    ),
    Competency.COMMUNICATION: (
        "질문과 관계없는 답을 하거나 요점을 알기 어렵습니다.",
        "질문에 답하지만 순서가 뒤섞이거나 용어를 부정확하게 씁니다.",
        "결론을 먼저 말하고 근거를 순서대로 설명합니다.",
        "결론과 근거가 분명하고, 상대 수준에 맞춰 예시나 비유로 설명합니다.",
    ),
    Competency.ROLE_DECISION: (
        "자신이 맡은 부분과 결정에 대해 말하지 못합니다.",
        "맡은 부분은 말하지만 무엇을 결정했는지 막연합니다.",
        "맡은 부분과 내린 결정, 그 이유를 구체적으로 말합니다.",
        "결정 과정에서 고려한 제약과 팀원과의 조율, 결과를 구체적으로 말합니다.",
    ),
}


def rubric_text() -> str:
    lines: list[str] = []
    for competency, levels in RUBRIC.items():
        lines.append(f"- {competency.value} ({COMPETENCY_TEXT[competency]})")
        lines.extend(f"  {level}점: {text}" for level, text in enumerate(levels, start=1))
    return "\n".join(lines)
