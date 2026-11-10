"""계약 모델을 묶음 JSON Schema로 내보냅니다.

사용: uv run python -m gitinsight_contracts.schema_export <출력 경로>
생성된 스키마는 json2ts로 TS 타입(contracts/ts/index.d.ts)이 됩니다.
"""

import json
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from pydantic.json_schema import GenerateJsonSchema, models_json_schema

from gitinsight_contracts import analysis, api, common, interview, llm, metrics, report

JsonSchemaMode = Literal["validation", "serialization"]

# 클라이언트가 보내는 요청은 validation 모드로 내보내 기본값 있는 필드가 선택 필드가 되게 합니다.
REQUEST_MODELS: list[type[BaseModel]] = [
    interview.SessionCreate,
    interview.TurnRequest,
    api.AnalysisCreate,
    metrics.MetricEvent,
]

# 서버가 돌려주거나 모듈끼리 주고받는 모델은 serialization 모드로 내보내 모든 필드가 required가 되게 합니다.
RESPONSE_MODELS: list[type[BaseModel]] = [
    common.SymbolRef,
    analysis.AnalysisResult,
    analysis.AnalysisSummary,
    interview.BrainReply,
    interview.SessionCreated,
    interview.SessionEnded,
    interview.VoiceToken,
    api.Me,
    api.Repo,
    api.AnalysisCreated,
    api.AnalysisState,
    report.Report,
    report.ProgressEntry,
    llm.Judgment,
    llm.InterviewPlanOutput,
    llm.ReportDraft,
]


class _NoFieldTitles(GenerateJsonSchema):
    """필드마다 title이 붙으면 json2ts가 필드마다 별도 타입 별칭을 만들어서 뺍니다."""

    def field_title_should_be_set(self, schema: Any) -> bool:
        return False


def build_schema() -> dict[str, Any]:
    entries: list[tuple[type[BaseModel], JsonSchemaMode]] = [
        *((model, "validation") for model in REQUEST_MODELS),
        *((model, "serialization") for model in RESPONSE_MODELS),
    ]
    _, top = models_json_schema(
        entries, title="GitInsightContracts", schema_generator=_NoFieldTitles
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "GitInsightContracts",
        "type": "object",
        "additionalProperties": False,
        "$defs": top["$defs"],
    }


def render() -> str:
    return json.dumps(build_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("사용법: python -m gitinsight_contracts.schema_export <출력 경로>", file=sys.stderr)
        return 2
    out = Path(argv[0])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
