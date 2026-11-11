# GitInsight 2.0 프로젝트 지침

이 파일을 저장소의 기본 개발 지침으로 사용합니다. 작업을 시작하기 전에 README.md, 관련 지침, 기존 코드를 확인하고, 범위를 벗어나는 기능이나 화면을 임의로 추가하지 않습니다.

## 구성

README.md의 "전체 구조"를 따릅니다.

- 웹 화면: Next.js (Vercel)
- 서버: FastAPI (Railway)
- 분석 워커 (Railway)
- DB: Postgres (Railway)
- 음성 AI: Google Gemini Live (G1에서 결정, `docs/evals/voice-g1.md`). OpenAI Realtime 어댑터는 대비용으로 남김

버전과 패키지 관리자는 아래와 같습니다. 첫 회의에서 바뀌면 이 절을 고칩니다.

- Python 3.12, uv workspace (멤버: `contracts`, `api`, `worker`)
- Node.js 22 이상, pnpm 10 (`corepack pnpm`으로 실행할 수 있습니다)

폴더 구조는 아래와 같습니다.

| 경로 | 내용 | 담당 |
|---|---|---|
| `contracts/src/gitinsight_contracts/` | 모듈 간 계약(Pydantic 모델) | B (변경 시 2명 승인) |
| `contracts/schema/contracts.json`, `contracts/ts/index.d.ts` | 계약에서 자동 생성한 JSON Schema와 TS 타입. 직접 고치지 않습니다 | 자동 생성 |
| `api/src/gitinsight_api/` | FastAPI 서버. `brain/`(면접 두뇌), `llm/`(LLMClient), `routes/` | B, D |
| `worker/src/gitinsight_worker/` | 분석 워커. 지금은 1단계(레포 내려받기, 언어 통계, tree-sitter 심볼 추출, git blame 기여 라벨과 최근 커밋) | A |
| `web/` | Next.js 웹. 지금은 음성 PoC(`/lab/voice`)와 임시 토큰 라우트(`/lab/voice-token`)만 있습니다 | C |
| `fixtures/analysis/` | 데모 레포 분석 결과 샘플 | B |
| `fixtures/conversations/` | 면접 두뇌 골든 대화 | B |
| `scripts/` | 계약 생성, 샘플 데이터 도구, 수동 LLM 평가 스크립트 | |
| `docs/` | 루브릭, 평가 기록(`docs/evals/`), 결정 기록 | |

## 설계 원칙

README.md의 "핵심 설계 원칙"을 지킵니다. 코드에서 특히 주의할 점은 아래와 같습니다.

- 음성 AI는 대화만 맡고, 다음 질문, 점수, 상태 변경은 서버 규칙이 정합니다.
- 코드 위치와 줄 번호는 LLM이 만들지 않고 분석 결과에서 가져옵니다.
- 분석이 끝난 원본 코드는 저장하지 않습니다.
- 기여 분석 결과는 점수에 쓰지 않습니다.
- 모듈끼리 주고받는 데이터 형식(계약)을 바꿀 때는 2명의 승인을 받습니다.
- 요구사항에 없는 API, 로그인, 사용자 데이터 구조는 임의로 만들지 않습니다.

## 코드 규칙

- 한 줄에는 한 문장만 작성하고, 읽기 쉬운 줄바꿈을 유지합니다.
- Python은 ruff(포맷·린트)와 pyright(standard 모드)를 씁니다. 설정은 루트 `pyproject.toml`에 있습니다.
- 계약 모델은 `Contract`(추가 필드 거부)를, LLM 출력 모델은 `LLMOutput`(기본값 없는 필드만)을 상속합니다.
- 계약을 바꾸면 `pnpm contracts:gen`으로 생성 파일을 함께 커밋합니다. CI는 `pnpm contracts:check`로 검사합니다.
- 모든 LLM 호출은 `gitinsight_api.llm.LLMClient`를 거칩니다. 코드, README, 자막, JD는 `untrusted_block()` 안에만 넣습니다.
- 테스트는 FakeLLM만 씁니다. 실제 LLM 호출 테스트는 `live_llm`, 네트워크 테스트는 `network` 표시를 붙이고 기본 실행에서 뺍니다.

## 작업 흐름

- 기능 작업은 담당자와 라벨을 지정한 GitHub 이슈를 먼저 준비하고, 이슈 단위 브랜치에서 작업합니다.
- `main` 브랜치에 직접 push하지 않고, PR로 최소 1명의 리뷰를 받습니다.
- 커밋 메시지는 이모지 없이 "작업 유형: 요약" 형식으로 작성합니다. 예: Feat: 꼬리질문 생성 추가, Fix: 코드 하이라이트 위치 수정, Docs: 개발 안내 보완.
- PR에는 변경 요약, 확인한 항목, UI 변경 시 화면 캡처를 포함합니다. 적절한 라벨과 담당자, 리뷰어를 지정합니다.
- PR을 열기 전에 CONTRIBUTING.md의 "PR 전 자기 검토 체크리스트"를 확인합니다.

## 명령

```text
uv sync                                    # Python 의존성 설치
pnpm install                               # Node 의존성 설치 (pnpm이 없으면 corepack pnpm install)
uv run ruff check; uv run ruff format --check; uv run pyright
uv run pytest                              # 기본 테스트 (network, live_llm 제외)
uv run pytest -m network                   # 샘플 데이터 줄 범위를 고정 커밋과 대조, 워커가 같은 심볼을 뽑는지 확인 (네트워크 필요)
pnpm contracts:gen                         # 계약 → JSON Schema → TS 타입 생성
pnpm contracts:check                       # 생성 파일이 커밋된 내용과 같은지 검사
uv run uvicorn --factory gitinsight_api.main:app_factory --reload   # API 서버 (기본 8000번)
uv run python -m gitinsight_contracts.fixtures show                # 샘플 분석 결과 요약
uv run python -m gitinsight_api.brain.cli [--real-llm]             # 터미널 텍스트 면접
uv run python scripts/llm_latency.py --n 20                        # 판단 LLM 지연 측정 (수동, 비용 발생)
uv run python scripts/eval_brain.py                                # 골든 대화 실제 LLM 평가 (수동, 비용 발생)
uv run python scripts/demo_check.py [--block-llm | --real-llm]     # 데모 완주 점검 (docs/runbook-demo.md)
pnpm --filter @gitinsight/web dev                                  # 웹 (기본 3000번, /api/*는 API_ORIGIN으로 넘김)
pnpm --filter @gitinsight/web typecheck                            # 웹 타입 검사
```

음성 PoC용 가짜 백엔드는 `LAB_ENABLED=true`로 서버를 띄우면 켜집니다. 이때 `POST /api/sessions/{id}/turns?lab_delay_ms=800`처럼 인위적 지연을 줄 수 있습니다. 데모와 배포에서는 끕니다.
웹의 음성 비교 화면(`/lab/voice`)도 `web/.env.local`의 `LAB_ENABLED=true`일 때만 열립니다. 방법과 기록은 `docs/evals/voice-g1.md`에 있습니다.
