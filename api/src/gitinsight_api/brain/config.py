"""면접 진행 설정값. 기본값은 사용자 승인 결정 4(10분, 본 주제 5개 + 여분, 깊이 2, 주제당 약 2분)입니다."""

from dataclasses import dataclass


@dataclass(frozen=True)
class InterviewConfig:
    # 본 주제 수입니다. 시간이 남으면 여분 주제로 이어 갑니다.
    main_topics: int = 5
    # 여분 주제 최대 수입니다.
    spare_topics: int = 3
    # 주제 하나에서 꼬리질문(FOLLOW_UP) 깊이 최대값입니다.
    max_depth: int = 2
    # 주제 하나에 쓰는 시간(초)입니다. 넘으면 다음 주제로 넘어갑니다.
    topic_budget_sec: int = 120
    # 기대 포인트 충족률이 이 값보다 낮으면 빠진 포인트를 PROBE로 묻습니다.
    probe_threshold: float = 0.4
    # 같은 주제에서 OFF_TOPIC이 이 횟수를 넘으면 다음 주제로 넘어갑니다.
    max_off_topic: int = 1
    # 남은 시간이 이 값(초) 이하면 새 주제를 시작하지 않고 마무리합니다.
    min_sec_for_new_topic: int = 30
