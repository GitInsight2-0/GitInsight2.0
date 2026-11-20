"""꼬리질문의 심볼 id 길이 (#183). 심볼 id와 같은 200자까지 받습니다."""

from typing import Any

import pytest
from pydantic import ValidationError

from gitinsight_contracts.analysis import PreparedFollowup, Topic
from gitinsight_contracts.fixtures import load_analysis

# Java처럼 패키지 경로가 긴 심볼 id입니다 (107자).
LONG_ID = (
    "src/main/java/org/springframework/samples/petclinic/owner/PetController.java"
    "::PetController.processCreationForm"
)
QUESTION = "이 메서드에서 입력값 검증이 실패하면 어떻게 되나요?"


def topic_with_related(symbol_id: str) -> dict[str, Any]:
    """첫 주제에 symbol_id인 관련 심볼과 그것을 가리키는 꼬리질문을 붙입니다."""
    data = load_analysis().topics[0].model_dump(mode="json")
    related = {**data["symbol"], "symbol_id": symbol_id, "name": "processCreationForm"}
    data["related_symbols"] = [*data["related_symbols"], related]
    data["prepared_followups"] = [{"question": QUESTION, "symbol_id": symbol_id}]
    return data


def test_long_symbol_id_over_100_chars_is_accepted() -> None:
    assert 100 < len(LONG_ID) <= 200

    topic = Topic.model_validate(topic_with_related(LONG_ID))

    assert topic.prepared_followups[0].symbol_id == LONG_ID
    assert topic.find_symbol(LONG_ID) is not None


def test_symbol_id_over_200_chars_is_rejected() -> None:
    with pytest.raises(ValidationError, match="symbol_id"):
        PreparedFollowup(question=QUESTION, symbol_id="a" * 201)


def test_followup_must_still_point_at_a_related_symbol() -> None:
    data = topic_with_related(LONG_ID)
    data["prepared_followups"] = [{"question": QUESTION, "symbol_id": LONG_ID + "X"}]

    with pytest.raises(ValidationError, match="허용 목록 밖"):
        Topic.model_validate(data)
