"""일부러 틀린 말 질문의 계약 (#133, #136)."""

from typing import Any

import pytest
from pydantic import ValidationError

from gitinsight_contracts.analysis import Misstatement, MisstatementKind, Topic
from gitinsight_contracts.fixtures import load_analysis

STATEMENT = "여기서 직접 save_report를 호출해서 저장하죠?"
CORRECTION = "이 함수는 save_report를 직접 호출하지 않습니다."


def topic_data() -> dict[str, Any]:
    return load_analysis().topics[0].model_dump(mode="json")


def absent_call(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "kind": "absent_call",
        "statement": STATEMENT,
        "correction": CORRECTION,
        "point_id": None,
    }
    data.update(overrides)
    return data


def test_stored_topics_without_the_field_still_load() -> None:
    data = topic_data()
    del data["misstatement"]
    assert Topic.model_validate(data).misstatement is None


def test_topic_roundtrips_with_misstatement() -> None:
    topic = Topic.model_validate({**topic_data(), "misstatement": absent_call()})
    assert topic.misstatement is not None
    assert topic.misstatement.kind is MisstatementKind.ABSENT_CALL
    assert Topic.model_validate_json(topic.model_dump_json()) == topic


def test_inverted_point_must_name_a_point_of_the_topic() -> None:
    data = topic_data()
    point_id = data["expected_points"][0]["point_id"]
    inverted = absent_call(kind="inverted_point", point_id=point_id)
    assert Topic.model_validate({**data, "misstatement": inverted}).misstatement is not None

    unknown = absent_call(kind="inverted_point", point_id="no-such-point")
    with pytest.raises(ValidationError, match="요점에 없습니다"):
        Topic.model_validate({**data, "misstatement": unknown})


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": "inverted_point"},
        {"point_id": "p1"},
        {"statement": "이 함수는 저장합니다. 그리고 다시 읽죠?"},
        {"correction": "아닙니다. 저장하지 않습니다."},
        {"statement": "가" * 151},
    ],
)
def test_misstatement_rejects_bad_shapes(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Misstatement.model_validate(absent_call(**overrides))
