"""면접관 발화 문구. 모든 발화는 BrainReply의 2문장 제한을 지키도록 한 문장짜리 조각을 이어 붙입니다."""

import re

from gitinsight_contracts.analysis import ContributionLabel

GREETING = "안녕하세요, 오늘 기술 면접을 진행하겠습니다."
NEXT_TOPIC = "좋습니다, 다음 주제로 넘어가겠습니다."
REPEAT = "다시 말씀드리겠습니다."
BACK_TO_QUESTION = "질문으로 돌아가겠습니다."
HINT_RETRY = "이 부분을 참고해서 다시 말씀해 주시겠어요?"
WRAP_UP_TIMEOUT = "시간이 다 되어 면접을 마치겠습니다. 수고하셨습니다."
WRAP_UP_DONE = "준비한 질문을 모두 마쳤습니다. 수고하셨습니다."

# 면접관이 줄 번호를 말하면 안 됩니다. 코드 위치는 분석 결과에서만 가져옵니다.
# 판단 LLM의 꼬리질문과 워커의 주제 재료가 이 패턴 하나를 같이 씁니다.
# "L12를"과 "L4를"(L4 로드밸런서)은 조사가 같아서 조사로는 구분할 수 없습니다. 숫자 크기로 구분합니다(PR #46 리뷰).
# GitHub 줄 링크(#L12), 두 자리 이상이거나 8·9인 L 번호, 범위(L3-L5)는 잡습니다.
# 한 자리 L1~L7 단독 표기는 계층·캐시 같은 기술 용어로 보고 통과시킵니다. "#" 없는 "L5를"은 놓칩니다.
# 한글도 단어 문자라 단어 경계(\b)를 쓰지 않습니다. "L10N"처럼 영문자가 이어지면 줄 번호가 아닙니다. "행렬"도 아닙니다.
LINE_NUMBER = re.compile(
    r"\d+\s*(번째\s*)?(줄|행(?!렬)|라인)"
    r"|(?<![A-Za-z])[Ll][Ii][Nn][Ee]\s*\d+"
    r"|(?<![A-Za-z0-9])(?:#L\d+|L(?:\d{2,}|[89])(?![A-Za-z0-9])|L\d+(?=\s*[-~]\s*L?\d))"
)


_FIRST_INTRO = {
    ContributionLabel.VERIFIED: "안녕하세요, 먼저 작업하신 기록이 있는 코드부터 여쭤보겠습니다.",
    ContributionLabel.SHARED: "안녕하세요, 먼저 여러 분이 함께 작업한 기록이 있는 코드부터 여쭤보겠습니다.",
    ContributionLabel.UNVERIFIED: "안녕하세요, 먼저 프로젝트의 핵심 코드부터 여쭤보겠습니다.",
}

# "직접 작성하셨네요"처럼 단정하지 않고, git 기록이 보여 주는 만큼만 말합니다.
_NEXT_INTRO = {
    ContributionLabel.VERIFIED: "다음은 작업하신 기록이 있는 다른 코드입니다.",
    ContributionLabel.SHARED: "다음은 여러 분이 함께 작업한 기록이 있는 코드입니다.",
    ContributionLabel.UNVERIFIED: "다음은 프로젝트의 다른 핵심 코드입니다.",
}


# 같은 레포로 다시 보는 면접의 첫 주제입니다 (#107). 지난 리포트의 코드 지도가 근거입니다.
FIRST_INTRO_STUCK = "안녕하세요, 지난 면접에서 설명하지 못했던 코드부터 다시 여쭤보겠습니다."
FIRST_INTRO_CHANGED = "안녕하세요, 지난 면접 뒤에 커밋이 들어온 코드부터 여쭤보겠습니다."


def topic_intro(label: ContributionLabel, *, first: bool) -> str:
    return (_FIRST_INTRO if first else _NEXT_INTRO)[label]


def join(*sentences: str) -> str:
    return " ".join(s.strip() for s in sentences if s.strip())
