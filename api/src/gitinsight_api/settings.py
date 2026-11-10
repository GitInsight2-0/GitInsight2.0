"""환경 변수 설정. 비밀 값은 코드에 두지 않고 환경 변수로만 받습니다."""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 음성 PoC 전용 기능(대본 면접 두뇌, 인위적 지연)을 켭니다. 데모·배포 빌드에서는 꺼야 합니다.
    lab_enabled: bool = False
    # 쉼표로 구분한 CORS 허용 출처입니다. 같은 도메인(Next.js rewrites)으로 붙으면 비워 둡니다.
    cors_origins: str = ""
    # /api/metrics 이벤트를 쌓는 로컬 JSONL 파일입니다. Postgres 저장(Task 9) 전까지 씁니다.
    metrics_path: Path = Path(".data/metrics.jsonl")
    # 면접 재료로 쓸 샘플 분석 파일 이름입니다 (fixtures/analysis/<이름>.json).
    fixture_analysis: str = "gitinsight_agent"

    # LiteLLM 모델 이름입니다. 제공사 API 키는 LiteLLM 규칙대로 OPENAI_API_KEY 등 환경 변수로 받습니다.
    llm_judge_model: str = "openai/gpt-6.1-terra"
    llm_analysis_model: str = "openai/gpt-6.1-sol"
    llm_report_model: str = "openai/gpt-6.1-sol"
    # 판단 모델의 reasoning_effort입니다. 비우면 모델 기본값을 씁니다. 지연 측정 결과로 정합니다.
    llm_judge_reasoning_effort: str | None = None
    # 답변 판단 LLM 호출 제한 시간(초)입니다. 넘으면 미리 만든 질문으로 진행합니다. PoC 결과로 조정합니다.
    judge_timeout_s: float = 2.5
    # 면접 계획(JD 반영) LLM 호출 제한 시간(초)입니다.
    plan_timeout_s: float = 8.0

    @field_validator("llm_judge_reasoning_effort", mode="before")
    @classmethod
    def _empty_as_none(cls, value: object) -> object:
        # .env에 `LLM_JUDGE_REASONING_EFFORT=`처럼 비워 두면 모델 기본값을 씁니다.
        return None if isinstance(value, str) and not value.strip() else value

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]
