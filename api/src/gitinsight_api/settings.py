"""환경 변수 설정. 비밀 값은 코드에 두지 않고 환경 변수로만 받습니다."""

from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from gitinsight_api.brain.quotes import QuoteMatcher, QuoteMatchMode

LLMProvider = Literal["openai", "gemini"]

# 제공사별 기본 모델입니다. 기본 제공사는 Gemini입니다 (2026-11-17 결정, #63).
# Gemini 판단 모델은 지연 측정(docs/evals/latency.md, 2026-11-17)에서 가장 빨랐던 3.6 Flash를 씁니다.
# 분석·리포트는 2단계 평가(docs/evals/stage2-*.md)를 해 온 3.8 Flash를 그대로 씁니다.
# openai는 키가 생기면 LLM_PROVIDER=openai만으로 쓸 수 있게 남겨 둡니다. 지금은 키가 없고 측정한 적도 없습니다.
DEFAULT_PROVIDER = "gemini"
PROVIDER_MODELS: dict[str, dict[str, str]] = {
    "openai": {
        "llm_judge_model": "openai/gpt-6.1-terra",
        "llm_analysis_model": "openai/gpt-6.1-sol",
        "llm_report_model": "openai/gpt-6.1-sol",
    },
    "gemini": {
        "llm_judge_model": "gemini/gemini-3.6-flash",
        "llm_analysis_model": "gemini/gemini-3.8-flash",
        "llm_report_model": "gemini/gemini-3.8-flash",
    },
}


class Settings(BaseSettings):
    # 검증 오류 메시지에 입력값(비밀 값일 수 있음)을 넣지 않습니다.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    # 음성 PoC 전용 기능(대본 면접 두뇌, 인위적 지연)을 켭니다. 데모·배포 빌드에서는 꺼야 합니다.
    lab_enabled: bool = False
    # 쉼표로 구분한 CORS 허용 출처입니다. 같은 도메인(Next.js rewrites)으로 붙으면 비워 둡니다.
    cors_origins: str = ""
    # Postgres 접속 주소입니다 (postgresql://user:password@host:5432/dbname).
    # 비워 두면 DB를 쓰지 않고 /api/metrics 이벤트를 metrics_path(JSONL)에 쌓습니다.
    # 비밀번호가 들어 있으므로 SecretStr로 받아 repr과 로그에 나오지 않게 합니다.
    database_url: SecretStr | None = None
    # DATABASE_URL이 없을 때 /api/metrics 이벤트를 쌓는 로컬 JSONL 파일입니다.
    metrics_path: Path = Path(".data/metrics.jsonl")
    # GitHub App의 Client ID와 Client secret입니다 (#18). 둘 중 하나라도 없으면 로그인 시작이 503입니다.
    github_client_id: str | None = None
    github_client_secret: SecretStr | None = None
    # 로그인 쿠키를 암호화하는 키입니다. 32자 이상 무작위 문자열을 씁니다.
    # 없으면 로그인 쿠키를 만들거나 읽지 못해 실제 모드 요청이 모두 401입니다.
    # 바꾸면 기존 로그인 쿠키는 모두 무효가 됩니다.
    session_secret: SecretStr | None = None
    # 브라우저가 여는 웹 주소입니다. GitHub 콜백 주소(<web_origin>/api/auth/github/callback)와
    # 로그인 뒤 돌아갈 주소를 여기서 만듭니다. GitHub App 설정의 Callback URL과 같아야 합니다.
    web_origin: str = "http://localhost:3000"
    # 로그인 쿠키에 Secure를 붙입니다. 배포에서는 켜 둡니다.
    # Chrome은 http://localhost에도 Secure 쿠키를 저장합니다. 그렇지 않은 브라우저로 로컬 개발할 때만 끕니다.
    cookie_secure: bool = True
    # 면접 재료로 쓸 샘플 분석 파일 이름입니다 (fixtures/analysis/<이름>.json).
    fixture_analysis: str = "gitinsight_agent"

    # 판단·분석·리포트 LLM 제공사입니다. 이 값 하나로 기본 모델을 모두 바꿉니다.
    # 제공사 API 키는 LiteLLM 규칙대로 GEMINI_API_KEY(openai면 OPENAI_API_KEY) 환경 변수로 받습니다.
    llm_provider: LLMProvider = DEFAULT_PROVIDER
    # LiteLLM 모델 이름입니다. 비워 두면 llm_provider의 기본값(PROVIDER_MODELS)을 씁니다.
    llm_judge_model: str = PROVIDER_MODELS[DEFAULT_PROVIDER]["llm_judge_model"]
    llm_analysis_model: str = PROVIDER_MODELS[DEFAULT_PROVIDER]["llm_analysis_model"]
    llm_report_model: str = PROVIDER_MODELS[DEFAULT_PROVIDER]["llm_report_model"]
    # true면 판단·계획·리포트에서 LLM을 부르지 않고 미리 만든 질문과 턴별 판단 표로 진행합니다.
    brain_force_prepared: bool = False
    # 판단 모델의 reasoning_effort입니다. 비우면 모델 기본값을 씁니다.
    # 기본값 low 근거: Gemini 3.6 Flash 판단 호출의 중앙값이 4.6초에서 2.3초로 줄었습니다 (docs/evals/latency.md).
    llm_judge_reasoning_effort: str | None = "low"
    # 분석(2단계 주제 생성) 모델의 reasoning_effort입니다. 비우면 모델 기본값을 씁니다.
    # 기본값 low 근거: Gemini 3.8 Flash에서 호출당 44~57초가 7~9초로 줄고 품질 차이는 없었습니다
    # (docs/evals/stage2-2026-11-12.md, PR #36).
    llm_analysis_reasoning_effort: str | None = "low"
    # 리포트 모델의 reasoning_effort입니다. 비우면 모델 기본값을 씁니다.
    # 기본값 low 근거: Gemini 3.8 Flash 리포트 생성이 23~28초에서 12초 안팎으로 줄었고,
    # 같은 면접 기록에서 역량 점수는 같았습니다 (2026-11-18, PR #83). 제한 시간(report_timeout_s)은 30초입니다.
    llm_report_reasoning_effort: str | None = "low"
    # 답변 판단 LLM 호출 제한 시간(초)입니다. 넘으면 미리 만든 질문으로 진행합니다.
    # 4초 근거: Gemini 3.6 Flash(low)가 10번 중 4번 2.5초를 넘었고 최대 3.8초였습니다 (docs/evals/latency.md).
    # 웹은 턴 첫 시도를 6초까지 기다립니다 (web/lib/voice/api.ts).
    judge_timeout_s: float = 4.0
    # 면접 계획(JD 반영) LLM 호출 제한 시간(초)입니다.
    plan_timeout_s: float = 8.0
    # 리포트 생성 LLM 호출 제한 시간(초)입니다. 넘으면 턴별 판단 표를 돌려줍니다.
    report_timeout_s: float = 30.0
    # 리포트 생성 시 빈 판단을 다시 판단할 때의 제한 시간(초)입니다.
    rejudge_timeout_s: float = 10.0
    # 인용문 비교 방식입니다. fuzzy는 음성 인식 오차를 유사도로 허용합니다 (brain/quotes.py).
    quote_match: QuoteMatchMode = "exact"
    # fuzzy에서 통과로 보는 유사도 하한(0~100)입니다.
    quote_fuzzy_threshold: float = Field(default=80.0, ge=50.0, le=100.0)

    def quote_matcher(self) -> QuoteMatcher:
        return QuoteMatcher(mode=self.quote_match, threshold=self.quote_fuzzy_threshold)

    @model_validator(mode="before")
    @classmethod
    def _provider_defaults(cls, data: object) -> object:
        # 모델 이름을 따로 주지 않은 항목만 제공사 기본값으로 채웁니다.
        if not isinstance(data, dict):
            return data
        values: dict[str, Any] = dict(data)
        provider = str(values.get("llm_provider") or DEFAULT_PROVIDER).strip().lower()
        profile = PROVIDER_MODELS.get(provider)
        if profile is None:
            return values
        for field, model in profile.items():
            current = values.get(field)
            if current is None or (isinstance(current, str) and not current.strip()):
                values[field] = model
        values["llm_provider"] = provider
        return values

    @field_validator(
        "llm_judge_reasoning_effort",
        "llm_analysis_reasoning_effort",
        "llm_report_reasoning_effort",
        "database_url",
        "github_client_id",
        "github_client_secret",
        "session_secret",
        mode="before",
    )
    @classmethod
    def _empty_as_none(cls, value: object) -> object:
        # .env에 `LLM_JUDGE_REASONING_EFFORT=`, `DATABASE_URL=`처럼 비워 두면 설정하지 않은 것으로 봅니다.
        text = value.get_secret_value() if isinstance(value, SecretStr) else value
        return None if isinstance(text, str) and not text.strip() else value

    @field_validator("session_secret")
    @classmethod
    def _session_secret_length(cls, value: SecretStr | None) -> SecretStr | None:
        # 짧은 키는 추측될 수 있어 받지 않습니다. 값은 오류 메시지에 넣지 않습니다.
        if value is not None and len(value.get_secret_value()) < 32:
            raise ValueError("SESSION_SECRET은 32자 이상이어야 합니다")
        return value

    @field_validator("web_origin")
    @classmethod
    def _web_origin_shape(cls, value: str) -> str:
        # 콜백 주소와 로그인 뒤 이동 주소를 만들므로 경로 없는 http(s) 출처만 받습니다.
        origin = value.strip().rstrip("/")
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("WEB_ORIGIN은 https://example.com 같은 출처여야 합니다")
        return origin

    @model_validator(mode="after")
    def _secure_cookie_needs_https(self) -> "Settings":
        # Secure 쿠키는 https(또는 localhost)에서만 저장되므로, 그 밖의 http 주소면 로그인이 항상 실패합니다.
        parsed = urlsplit(self.web_origin)
        local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
        if self.cookie_secure and parsed.scheme == "http" and not local:
            raise ValueError(
                "COOKIE_SECURE=true이면 WEB_ORIGIN은 https여야 합니다 (localhost 제외)"
            )
        return self

    def github_callback_url(self) -> str:
        return f"{self.web_origin}/api/auth/github/callback"

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]
