from typing import Any
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from gitinsight_contracts import llm
from gitinsight_contracts.base import count_sentences
from gitinsight_contracts.common import SymbolRef
from gitinsight_contracts.interview import Action, BrainReply, SessionCreate, TurnRequest
from gitinsight_contracts.metrics import MetricEvent
from gitinsight_contracts.schema_export import REQUEST_MODELS, RESPONSE_MODELS, build_schema

SHA = "a" * 40


def make_symbol(**overrides: Any) -> SymbolRef:
    data: dict[str, Any] = {
        "symbol_id": "app/graph.py::create_graph",
        "name": "create_graph",
        "kind": "function",
        "path": "app/graph.py",
        "start_line": 10,
        "end_line": 20,
        "commit_sha": SHA,
    }
    data.update(overrides)
    return SymbolRef.model_validate(data)


def make_reply(utterance: str) -> BrainReply:
    return BrainReply(
        turn_id=None,
        action=Action.ASK,
        utterance=utterance,
        highlight=make_symbol(),
        topic_id="t1",
        depth=0,
        remaining_sec=600,
        done=False,
    )


@pytest.mark.parametrize("model", [*REQUEST_MODELS, *RESPONSE_MODELS])
def test_every_exported_model_is_registered_in_schema(model: type[BaseModel]) -> None:
    assert model.__name__ in build_schema()["$defs"]


def test_brain_reply_roundtrip() -> None:
    reply = make_reply("이 함수가 하는 일을 설명해 주시겠어요?")
    assert BrainReply.model_validate_json(reply.model_dump_json()) == reply


def test_turn_request_roundtrip_from_client_json() -> None:
    turn_id = uuid4()
    raw = f'{{"turn_id": "{turn_id}", "transcript": "네", "source": "voice"}}'
    request = TurnRequest.model_validate_json(raw)
    assert request.turn_id == turn_id
    assert TurnRequest.model_validate_json(request.model_dump_json()) == request


def test_turn_request_rejects_non_uuid_turn_id() -> None:
    with pytest.raises(ValidationError, match="turn_id"):
        TurnRequest.model_validate({"turn_id": "abc", "transcript": "", "source": "text"})


def test_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="remaining_sec"):
        SessionCreate.model_validate({"analysis_id": "a1", "mode": "text", "remaining_sec": 9999})


def test_symbol_ref_rejects_inverted_range() -> None:
    with pytest.raises(ValidationError, match="end_line"):
        make_symbol(start_line=20, end_line=10)


def test_symbol_ref_rejects_short_sha() -> None:
    with pytest.raises(ValidationError, match="commit_sha"):
        make_symbol(commit_sha="abc123")


def test_utterance_allows_two_sentences() -> None:
    make_reply("다음 주제로 넘어가겠습니다. 이 함수의 역할은 무엇인가요?")


def test_utterance_rejects_three_sentences() -> None:
    with pytest.raises(ValidationError, match="2문장 이내"):
        make_reply("좋습니다. 다음으로 가겠습니다. 이 함수의 역할은 무엇인가요?")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0),
        ("네", 1),
        ("3.5초 걸렸나요?", 1),
        ("좋습니다. 다음 질문입니다?", 2),
        ("정말요?! 그렇군요.", 2),
    ],
)
def test_count_sentences(text: str, expected: int) -> None:
    assert count_sentences(text) == expected


def test_metric_event_rejects_free_text_fields() -> None:
    with pytest.raises(ValidationError, match="transcript"):
        MetricEvent.model_validate({"name": "vad_end", "at_ms": 1, "transcript": "비밀"})


def test_metric_event_rejects_free_text_in_error_code() -> None:
    with pytest.raises(ValidationError, match="error_code"):
        MetricEvent.model_validate(
            {"name": "error", "at_ms": 1, "error_code": "def handler(): return secret"}
        )


LLM_OUTPUT_MODELS = [llm.Judgment, llm.InterviewPlanOutput, llm.ReportDraft]


def _object_schemas(schema: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if schema.get("type") == "object":
        found.append(schema)
    for definition in schema.get("$defs", {}).values():
        found.extend(_object_schemas(definition))
    return found


@pytest.mark.parametrize("model", LLM_OUTPUT_MODELS)
def test_llm_output_schema_follows_openai_strict_rules(model: type[BaseModel]) -> None:
    schema = model.model_json_schema()
    assert schema["type"] == "object"
    assert "anyOf" not in schema
    objects = _object_schemas(schema)
    assert objects
    for obj in objects:
        assert obj.get("additionalProperties") is False, obj.get("title")
        assert set(obj.get("required", [])) == set(obj["properties"]), obj.get("title")


def test_judgment_roundtrip() -> None:
    judgment = llm.Judgment(
        intent=llm.Intent.ANSWER,
        coverage=[
            llm.PointCoverage(
                point_id="p1", status=llm.CoverageStatus.COVERED, evidence_quote="캐시를 썼습니다"
            )
        ],
        followup=llm.Followup(question="왜 캐시를 썼나요?", symbol_id=None),
    )
    assert llm.Judgment.model_validate_json(judgment.model_dump_json()) == judgment
