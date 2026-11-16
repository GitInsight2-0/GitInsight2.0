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
# "L2 캐시", "L7 로드밸런서" 같은 기술 용어와 구분하려고, "L12"는 조사나 범위가 붙을 때만 잡습니다(PR #36 리뷰).
# 한글도 단어 문자라 "L12를"에는 단어 경계가 생기지 않으므로 뒤에 오는 글자로 판단합니다. "행렬"도 줄 번호가 아닙니다.
LINE_NUMBER = re.compile(
    r"\d+\s*(번째\s*)?(줄|행(?!렬)|라인)"
    r"|(?<![A-Za-z])[Ll][Ii][Nn][Ee]\s*\d+"
    r"|(?<![A-Za-z0-9])L\d+(?=\s*[-~]\s*L?\d|을|를|부터|까지)"
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


def topic_intro(label: ContributionLabel, *, first: bool) -> str:
    return (_FIRST_INTRO if first else _NEXT_INTRO)[label]


def join(*sentences: str) -> str:
    return " ".join(s.strip() for s in sentences if s.strip())
