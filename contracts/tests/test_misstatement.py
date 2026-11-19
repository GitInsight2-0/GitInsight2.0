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


def other_symbol(name: str = "save_report") -> dict[str, Any]:
    """주제 코드가 아닌 다른 함수입니다. 위치는 주제 코드의 것을 빌려 이름과 ID만 바꿉니다."""
    symbol = topic_data()["symbol"]
    return {**symbol, "symbol_id": f"app/report.py::{name}", "name": name}


def absent_call(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "kind": "absent_call",
        "statement": STATEMENT,
        "correction": CORRECTION,
        "point_id": None,
        "absent_symbol": other_symbol(),
    }
    data.update(overrides)
    return data


def inverted_point(point_id: str) -> dict[str, Any]:
    return absent_call(kind="inverted_point", point_id=point_id, absent_symbol=None)


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
    inverted = inverted_point(point_id)
    assert Topic.model_validate({**data, "misstatement": inverted}).misstatement is not None

    unknown = inverted_point("no-such-point")
    with pytest.raises(ValidationError, match="요점에 없습니다"):
        Topic.model_validate({**data, "misstatement": unknown})


def test_absent_symbol_cannot_be_the_topic_code_itself() -> None:
    data = topic_data()
    name = data["symbol"]["name"]
    own = absent_call(
        statement=f"여기서 직접 {name}을 호출해서 저장하죠?", absent_symbol=data["symbol"]
    )
    with pytest.raises(ValidationError, match="주제 코드 자신"):
        Topic.model_validate({**data, "misstatement": own})


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": "inverted_point"},
        {"point_id": "p1"},
        {"absent_symbol": None},
        {"kind": "inverted_point", "point_id": "p1"},
        {"absent_symbol": other_symbol("load_report")},
        {"statement": "이 함수는 저장합니다. 그리고 다시 읽죠?"},
        {"correction": "아닙니다. 저장하지 않습니다."},
        {"statement": "가" * 151},
    ],
)
def test_misstatement_rejects_bad_shapes(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Misstatement.model_validate(absent_call(**overrides))
