"""질문과 관계있는 줄 범위(focus)의 계약 (#156)."""

from typing import Any

import pytest
from pydantic import ValidationError

from gitinsight_contracts.analysis import Topic
from gitinsight_contracts.common import LineSpan
from gitinsight_contracts.fixtures import load_analysis
from gitinsight_contracts.interview import Action, BrainReply


def topic_data() -> dict[str, Any]:
    return load_analysis().topics[0].model_dump(mode="json")


def with_focus(start_line: int, end_line: int) -> dict[str, Any]:
    """첫 요점에 focus를 붙인 주제입니다."""
    data = topic_data()
    data["expected_points"][0]["focus"] = {"start_line": start_line, "end_line": end_line}
    return data


def reply_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "turn_id": None,
        "action": Action.PROBE,
        "utterance": "이 부분은 어떻게 처리하나요?",
        "highlight": topic_data()["symbol"],
        "topic_id": "t1",
        "depth": 0,
        "remaining_sec": 600,
        "done": False,
    }
    data.update(overrides)
    return data


def test_line_span_rejects_reversed_and_zero_lines() -> None:
    assert LineSpan(start_line=3, end_line=3).end_line == 3
    with pytest.raises(ValidationError):
        LineSpan(start_line=5, end_line=4)
    with pytest.raises(ValidationError):
        LineSpan(start_line=0, end_line=4)


def test_stored_topics_without_focus_still_load() -> None:
    data = topic_data()
    for point in data["expected_points"]:
        point.pop("focus", None)

    topic = Topic.model_validate(data)

    assert all(p.focus is None for p in topic.expected_points)


def test_topic_accepts_focus_inside_its_symbol() -> None:
    symbol = topic_data()["symbol"]

    topic = Topic.model_validate(with_focus(symbol["start_line"], symbol["end_line"]))

    assert topic.expected_points[0].focus == LineSpan(
        start_line=symbol["start_line"], end_line=symbol["end_line"]
    )


def test_topic_rejects_focus_outside_its_symbol() -> None:
    symbol = topic_data()["symbol"]

    with pytest.raises(ValidationError, match="focus"):
        Topic.model_validate(with_focus(symbol["start_line"], symbol["end_line"] + 1))
    if symbol["start_line"] > 1:
        with pytest.raises(ValidationError, match="focus"):
            Topic.model_validate(with_focus(symbol["start_line"] - 1, symbol["end_line"]))


def test_stored_replies_without_focus_still_load() -> None:
    assert BrainReply.model_validate(reply_data()).focus is None


def test_reply_focus_must_be_inside_highlight() -> None:
    symbol = topic_data()["symbol"]
    inside = {"start_line": symbol["start_line"], "end_line": symbol["start_line"]}
    outside = {"start_line": symbol["end_line"], "end_line": symbol["end_line"] + 1}

    reply = BrainReply.model_validate(reply_data(focus=inside))

    assert reply.focus == LineSpan.model_validate(inside)
    assert BrainReply.model_validate_json(reply.model_dump_json()) == reply
    with pytest.raises(ValidationError, match="focus"):
        BrainReply.model_validate(reply_data(focus=outside))
    with pytest.raises(ValidationError, match="focus"):
        BrainReply.model_validate(reply_data(focus=inside, highlight=None))
