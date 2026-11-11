"""LLM이 옮긴 인용문이 지원자 답변(자막)에 있는지 확인합니다.

- exact: 공백, 문장부호, 대소문자를 무시한 부분 문자열 비교입니다. 기본값입니다.
- fuzzy: exact가 실패하면 rapidfuzz partial_ratio로 유사도를 봅니다. 음성 인식 오차
  ("SHA" ↔ "샤", 조사 하나 차이)를 허용하려는 것입니다.
  짧은 인용문은 우연히 비슷해지기 쉬워서 유사도 비교를 하지 않고 exact만 씁니다.
  자막 앞뒤에 NUL 문자를 붙여 비교해서, 인용문이 자막 바깥으로 넘어간 부분은 틀린 글자로 셉니다.
  partial_ratio는 짧은 쪽을 긴 쪽 안에서 찾고 양 끝은 겹치는 부분만 비교합니다.
  그래서 그대로 쓰면 자막 전체에 지어낸 문장을 덧붙인 인용문도 100점이 나옵니다.
  유사도만 보면 인용문 길이의 20%까지는 자막 바깥 글자를 붙여도 통과합니다.
  그래서 정렬 위치에서 자막 바깥 글자 수를 따로 세고, 조사 하나 정도(2자)까지만 허용합니다.

기준값 근거(정규화한 문자열, NUL 문자를 붙인 자막과 비교):
- 자막 오차 사례: 86~94점, 자막 바깥 0자 (2026-11-10, 11-12 측정)
- 기대 포인트 문장·바꿔 쓴 요약 같은 지어낸 인용문: 9~22점 (2026-11-10 측정)
- 자막 전체(또는 긴 인용문)에 지어낸 문장을 덧붙인 인용문: 78~88점이지만 자막 바깥 4~17자
  (2026-11-12 측정). 유사도로는 자막 오차 사례와 겹쳐서 바깥 글자 수로만 거부됩니다.
실제 음성 자막으로 통과율을 다시 본 뒤 조정합니다.
"""

import logging
import re
from dataclasses import dataclass
from typing import Literal

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

QuoteMatchMode = Literal["exact", "fuzzy"]

_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)
# 정규화한 문자열에는 나오지 않는 글자입니다. 자막 앞뒤를 채우는 데 씁니다.
_PAD = "\0"


def normalize(text: str) -> str:
    """공백, 문장부호, 대소문자를 무시하고 비교하기 위한 정규화입니다."""
    return _NON_WORD.sub("", text).lower()


def fuzzy_alignment(needle: str, haystack: str) -> tuple[float, int]:
    """정규화한 인용문과 자막의 (유사도, 자막 바깥으로 넘어간 글자 수)를 돌려줍니다."""
    padding = _PAD * len(needle)
    padded = f"{padding}{haystack}{padding}"
    alignment = fuzz.partial_ratio_alignment(needle, padded)
    if alignment is None:
        return 0.0, len(needle)
    start = len(padding)
    end = start + len(haystack)
    outside = max(0, start - alignment.dest_start) + max(0, alignment.dest_end - end)
    return alignment.score, outside


@dataclass(frozen=True)
class QuoteMatcher:
    mode: QuoteMatchMode = "exact"
    # fuzzy에서 통과로 보는 partial_ratio 하한(0~100)입니다.
    threshold: float = 80.0
    # fuzzy 비교를 할 인용문의 최소 길이(정규화한 글자 수)입니다. 이보다 짧으면 exact만 씁니다.
    min_fuzzy_chars: int = 6
    # fuzzy에서 인용문이 자막 앞뒤 바깥으로 넘어가도 되는 글자 수입니다. 조사 하나 정도입니다.
    max_outside_chars: int = 2

    def matches(self, quote: str, transcript: str) -> bool:
        needle = normalize(quote)
        if not needle:
            return False
        haystack = normalize(transcript)
        if needle in haystack:
            return True
        if self.mode != "fuzzy" or len(needle) < self.min_fuzzy_chars:
            return False
        score, outside = fuzzy_alignment(needle, haystack)
        if score >= self.threshold and outside <= self.max_outside_chars:
            return True
        # 인용문과 자막 원문은 남기지 않습니다.
        logger.info(
            "quote_rejected score=%.1f outside=%d quote_chars=%d", score, outside, len(needle)
        )
        return False


EXACT = QuoteMatcher()
