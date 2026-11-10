"""LLM이 옮긴 인용문이 지원자 답변(자막)에 있는지 확인합니다.

- exact: 공백, 문장부호, 대소문자를 무시한 부분 문자열 비교입니다. 기본값입니다.
- fuzzy: exact가 실패하면 rapidfuzz partial_ratio로 유사도를 봅니다. 음성 인식 오차
  ("SHA" ↔ "샤", 조사 하나 차이)를 허용하려는 것입니다.
  짧은 인용문은 우연히 비슷해지기 쉬워서 유사도 비교를 하지 않고 exact만 씁니다.

기준값 근거(2026-11-10 측정, 정규화한 문자열의 partial_ratio):
자막 오차 사례 86~90점, 기대 포인트 문장·바꿔 쓴 요약 같은 지어낸 인용문 9~22점.
실제 음성 자막으로 통과율을 다시 본 뒤 조정합니다.
"""

import re
from dataclasses import dataclass
from typing import Literal

from rapidfuzz import fuzz

QuoteMatchMode = Literal["exact", "fuzzy"]

_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)


def normalize(text: str) -> str:
    """공백, 문장부호, 대소문자를 무시하고 비교하기 위한 정규화입니다."""
    return _NON_WORD.sub("", text).lower()


@dataclass(frozen=True)
class QuoteMatcher:
    mode: QuoteMatchMode = "exact"
    # fuzzy에서 통과로 보는 partial_ratio 하한(0~100)입니다.
    threshold: float = 80.0
    # fuzzy 비교를 할 인용문의 최소 길이(정규화한 글자 수)입니다. 이보다 짧으면 exact만 씁니다.
    min_fuzzy_chars: int = 6

    def matches(self, quote: str, transcript: str) -> bool:
        needle = normalize(quote)
        if not needle:
            return False
        haystack = normalize(transcript)
        if needle in haystack:
            return True
        if self.mode != "fuzzy" or len(needle) < self.min_fuzzy_chars:
            return False
        return fuzz.partial_ratio(needle, haystack, score_cutoff=self.threshold) > 0


EXACT = QuoteMatcher()
