# GitInsight 2.0 프로젝트 지침

이 파일을 저장소의 기본 개발 지침으로 사용합니다. 작업을 시작하기 전에 README.md, 관련 지침, 기존 코드를 확인하고, 범위를 벗어나는 기능이나 화면을 임의로 추가하지 않습니다.

## 구성

README.md의 "전체 구조"를 따릅니다.

- 웹 화면: Next.js (Vercel)
- 서버: FastAPI (Railway)
- 분석 워커 (Railway)
- DB: Postgres (Railway)
- 음성 AI: Google Gemini Live (G1에서 결정, `docs/evals/voice-g1.md`). OpenAI Realtime은 쓰지 않습니다
- 판단·분석·리포트 LLM: Google Gemini (`LLM_PROVIDER` 기본값 `gemini`, 키는 `GEMINI_API_KEY`). `LLM_PROVIDER=openai`는 선택지로만 남아 있고 키가 없어 쓰지 않습니다

버전과 패키지 관리자는 아래와 같습니다. 첫 회의에서 바뀌면 이 절을 고칩니다.

- Python 3.12, uv workspace (멤버: `contracts`, `api`, `worker`)
- Node.js 22 이상, pnpm 10 (`corepack pnpm`으로 실행할 수 있습니다)

폴더 구조는 아래와 같습니다.

| 경로 | 내용 | 담당 |
|---|---|---|
| `contracts/src/gitinsight_contracts/` | 모듈 간 계약(Pydantic 모델) | B (변경 시 2명 승인) |
| `contracts/schema/contracts.json`, `contracts/ts/index.d.ts` | 계약에서 자동 생성한 JSON Schema와 TS 타입. 직접 고치지 않습니다 | 자동 생성 |
| `api/src/gitinsight_api/` | FastAPI 서버. `brain/`(면접 두뇌), `llm/`(LLMClient), `routes/` | B, D |
| `worker/src/gitinsight_worker/` | 분석 워커. 1단계(레포 내려받기, 언어 통계, tree-sitter 심볼 추출(Python, TypeScript, JavaScript, Java), git blame 기여 라벨과 최근 커밋, 이름 기준 레포 안 참조 수(Python만)), 2단계(규칙으로 주제 후보 선택, 참조 수를 서버 사실로, 주제 코드가 쓰는 다른 파일 정의 요약(`definitions`, Python만)을 신뢰하지 않는 입력으로 넣어 LLM으로 주제 재료 생성과 서버 검증, 검토 LLM과 요점 근거 문장 대조로 재료 내용 확인(`review`), 면접관이 일부러 하는 틀린 말의 방식과 부른다고 말할 함수를 규칙으로 고르기(`misstatement`)), 전체 흐름 `pipeline.run_analysis`, 분석 기록 저장소와 연결해 실행하는 `jobs`(같은 프로세스 Launcher), 분석까지 여는 서버 진입점 `server` | A |
| `web/` | Next.js 웹. 첫 화면(`/`, GitHub 로그인과 면접 시작), 실제 모드 면접 화면(`/interview/[analysisId]`, 음성·텍스트, 도중 전환, 음성 끊김 시 텍스트 자동 전환, 선택형 웹캠 시선 분석, 끝내기와 리포트 요청), 리포트 화면(`/reports/[reportId]`, 역량별 수준과 근거 인용문, 코드 지도, 주제별 내용, 시선(참고), 답변 기록, 이전 면접과 비교), 음성 토큰 라우트(`/voice-token`, 실험 모드가 아니면 로그인 필요), 실험 화면(`/lab/voice`, `/lab/interview`)이 있습니다 | C |
| `fixtures/analysis/` | 데모 레포 분석 결과 샘플(`gitinsight_agent`, 손으로 만듦)과 실제 워커로 만든 데모 스냅샷(`gitinsight_agent_live`) | B |
| `fixtures/conversations/` | 면접 두뇌 골든 대화 | B |
| `scripts/` | 계약 생성, 샘플 데이터 도구, 데모 스냅샷 넣기, 수동 LLM 평가 스크립트 | |
| `docs/` | 루브릭, 평가 기록(`docs/evals/`), 결정 기록, 배포 방법(`docs/deploy.md`) | |
| `Dockerfile`, `railway.json` | Railway API 서버 이미지와 배포 설정 (API와 분석 워커 한 프로세스, 인스턴스 1개) | D |

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
- CI(`.github/workflows/ci.yml`)는 PR과 `main` push마다 아래 "명령"의 ruff, pyright, pytest, `pnpm contracts:check`를 실행합니다.
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
uv run pytest -m network                   # 샘플 데이터 줄 범위를 고정 커밋과 대조, 워커가 같은 심볼을 뽑는지 확인, TypeScript 샘플 레포 줄 범위 대조 (네트워크 필요)
TEST_DATABASE_URL=postgresql://... uv run pytest -m postgres       # 실제 Postgres 테스트 (빈 테스트 전용 DB 사용)
uv run alembic -c api/alembic.ini upgrade head                     # DATABASE_URL의 DB에 마이그레이션 적용
uv run alembic -c api/alembic.ini revision --rev-id 0002 -m "설명"  # 새 마이그레이션 (api/src/gitinsight_api/tables.py도 같이 수정)
uv run alembic -c api/alembic.ini check                            # tables.py와 마이그레이션이 일치하는지 확인 (DB 필요)
pnpm contracts:gen                         # 계약 → JSON Schema → TS 타입 생성
pnpm contracts:check                       # 생성 파일이 커밋된 내용과 같은지 검사
uv run uvicorn --factory gitinsight_worker.server:app_factory --loop gitinsight_api.event_loop:server_loop_factory --reload   # API 서버 (기본 8000번, 분석 요청 포함)
uv run python -m gitinsight_contracts.fixtures show                # 샘플 분석 결과 요약
uv run python -m gitinsight_api.brain.cli [--real-llm]             # 터미널 텍스트 면접
uv run python scripts/llm_latency.py --n 20                        # 판단 LLM 지연 측정 (수동, 비용 발생)
uv run python scripts/eval_brain.py                                # 골든 대화 실제 LLM 평가 (수동, 비용 발생)
uv run python scripts/eval_stage2.py                               # 샘플(데모) 레포 2단계 분석 실제 LLM 평가 (수동, 비용 발생)
                                   # --repo <owner/name> --commit <SHA>: 다른 레포 평가 (동의받은 레포만, 기본 기록 이름에 레포 이름이 붙음)
                                   # --requester-email <커밋 이메일> --save-json <경로>: 그 사람 기준 기여 라벨로 분석하고 스냅샷용 JSON 저장
                                   # --out <경로>: 평가 기록 경로 (기본 docs/evals/stage2-<날짜>.md, 같은 날 기록은 덮어씀)
                                   # --no-review: 검토(#199) 없이 비교, --review-model / --review-effort: 검토만 다른 모델·추론량
uv run python scripts/demo_check.py [--block-llm | --real-llm] [--from-db --user-id <id>]   # 데모 완주 점검 (docs/runbook-demo.md). --from-db는 DB의 데모 스냅샷을 그 주인으로 사용
uv run python scripts/seed_demo_analysis.py --user-id <id> [--fixture gitinsight_agent_live | --file <json>] [--analysis-id <id>]   # 분석 결과를 DATABASE_URL의 DB에 완료 기록으로 넣기 (데모 스냅샷)
pnpm --filter @gitinsight/web dev                                  # 웹 (기본 3000번, /api/*는 API_ORIGIN으로 넘김)
pnpm --filter @gitinsight/web typecheck                            # 웹 타입 검사
```

음성 PoC용 가짜 백엔드는 `LAB_ENABLED=true`로 서버를 띄우면 켜집니다. 이때 `POST /api/sessions/{id}/turns?lab_delay_ms=800`처럼 인위적 지연을 줄 수 있습니다. 데모와 배포에서는 끕니다.
웹의 음성 측정 화면(`/lab/voice`)도 `web/.env.local`의 `LAB_ENABLED=true`일 때만 열립니다. 방법과 기록은 `docs/evals/voice-g1.md`에 있습니다.

`LAB_ENABLED=false`(기본)이면 서버는 진짜 면접 두뇌와 리포트 API(`POST /api/sessions/{id}/end`, `GET /api/reports/{id}`)를 씁니다.
이 모드의 요청은 GitHub 로그인(#18) 쿠키가 있어야 하고, 없으면 401입니다.
로그인은 GitHub App 사용자 인증(state + PKCE)입니다. `GET /api/auth/github/login` → GitHub → `GET /api/auth/github/callback` → 웹 첫 화면 순서이고, 실패하면 `/?login_error=denied|invalid_state|github`로 돌아갑니다. `POST /api/auth/logout`, `GET /api/me`도 있습니다.
로그인 정보는 DB 없이 암호화한 쿠키(`gi_session`, 최대 8시간)에 담습니다 (`api/src/gitinsight_api/login_cookie.py`). 필요한 환경 변수는 `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `SESSION_SECRET`, `WEB_ORIGIN`이며 `.env.example`에 설명이 있습니다. GitHub 사용자 토큰은 로그와 DB에 남기지 않습니다. 로그아웃하면 쿠키 속 토큰을 GitHub에서 폐기하고, 폐기에 실패해도 쿠키는 지웁니다.
테스트는 `app.dependency_overrides`로 `get_current_user`를 바꿔 로그인 사용자를 넣습니다. 로그인 흐름 자체는 `api/tests/test_auth.py`가 가짜 GitHub(`httpx.MockTransport`)로 확인합니다.
로그인 쿠키의 사용자 토큰을 쓰는 라우트는 `GET /api/repos`(App을 설치한 레포 중 사용자가 읽을 수 있는 것, 계약 `Repo` 배열)와 `GET /api/code?analysis_id=&path=`(본인 분석의 주제 심볼 파일만, `text/plain`)입니다 (`routes/github.py`, GitHub 호출은 `github_api.py`). 실험 모드에서는 둘 다 404입니다.

`DATABASE_URL`이 있으면 서버는 Postgres를 쓰고(`/api/metrics` → `metric_events` 테이블, 실험 모드가 아니면 로그인 필요), 없으면 DB 없이 돌며 측정 이벤트를 `METRICS_PATH`(JSONL)에 쌓습니다.
분석 기록과 결과는 `analyses` 테이블에 둡니다(`gitinsight_api.analyses.postgres.PostgresAnalysisStore`, 규칙은 `analyses/store.py`의 `AnalysisStore`).
`DATABASE_URL`이 있고 `LAB_ENABLED=false`이면 면접 두뇌와 리포트도 샘플 파일 대신 이 테이블의 분석 결과를 읽습니다(`analyses/source.py`의 `StoredAnalysisSource`).
이때 `POST /api/sessions`는 분석을 요청한 본인만 쓸 수 있고, 다른 사람의 분석과 없는 분석은 같은 404입니다. 샘플 분석도 `scripts/seed_demo_analysis.py`로 넣은 계정만 씁니다.
분석 결과를 읽다가 DB에 연결하지 못하면 503을 돌려줍니다.
분석 요청은 `POST /api/analyses`(본문 `repo_id`), 진행 상태는 `GET /api/analyses/{id}`입니다(`routes/analyses.py`). 상태는 요청한 본인만 보고, 다른 사람의 분석은 404입니다.
완료된 분석은 (레포, 커밋, 요청자, 분석기 버전)이 같으면 다시 씁니다(`cached=True`). 분석기 버전은 `gitinsight_worker/version.py`의 `ANALYZER_VERSION`이고(#171), **분석 결과를 바꾸는 워커 변경(1·2단계 규칙, 프롬프트, 결과에 채우는 계약 필드)을 머지할 때 하나 올립니다.** 그래야 배포 뒤 같은 커밋도 새로 분석합니다.
서버는 로그인 쿠키의 GitHub 사용자 토큰으로 레포 이름과 기본 브랜치의 최신 커밋을 확인하고(`github_repos.py`), 같은 토큰으로 레포를 내려받습니다. 토큰은 분석 작업의 메모리에만 있고, git에는 환경 변수로만 넘깁니다.
분석 실행기는 워커 패키지에 있어서 `gitinsight_worker.server:app_factory`로 띄울 때만 붙습니다. `DATABASE_URL`이 없거나 실험 모드이거나 `gitinsight_api.main:app_factory`로 띄우면 분석 요청 API는 503입니다.
같은 조건에서 면접 세션과 턴 기록도 메모리 대신 `interview_sessions`, `interview_turns` 테이블에 둡니다(`brain/postgres.py`의 `PostgresSessionRepository`). 서버를 다시 시작해도 면접이 이어집니다.
같은 조건에서 리포트도 `reports` 테이블에 둡니다(`report/postgres.py`의 `PostgresReportStore`, #75). 세션 하나에 리포트는 하나이고, 서버를 다시 시작해도 같은 `report_id`로 다시 엽니다.
이전 면접 비교는 `GET /api/me/progress`입니다(#77). 로그인한 사용자의 리포트에 적힌 역량 수준을 면접별로, 오래된 것부터 최근 20건까지 돌려줍니다. 역량 평가가 없는 리포트(턴별 판단 표)는 빠집니다.
리포트에는 질문받은 함수별 상태(설명함, 힌트 받고 설명함, 막힘)를 담은 코드 지도(`Report.code_map`)가 들어갑니다(#107, `report/code_map.py`). LLM 없이 턴 기록으로 집계하고, 누적 숫자는 같은 사용자가 같은 레포로 본 리포트를 합쳐 냅니다.
다시 보기 면접은 `POST /api/sessions`에 `review_only: true`를 보내 시작합니다(리포트 화면의 "부족했던 질문만 다시 면접 보기", 주소 `?review=1`). 지난 코드 지도에서 가장 최근 상태가 막힘이거나 힌트 받고 설명함인 함수의 주제만 규칙 순서로 묻고, 그런 주제가 없으면 409입니다. `review_only`를 보내지 않은 면접은 지난 기록을 읽지 않고 늘 같은 순서로 묻습니다. 새 API나 테이블은 없고 `reports` 테이블의 리포트를 읽습니다.
면접관이 일부러 틀린 말을 하는 질문(#133)은 주제에 `Topic.misstatement`가 있을 때 면접 한 번에 최대 한 번 나옵니다(`brain/misstatement.py`, 언제 할지는 `brain/rules.py`).
주제의 질문을 다 한 뒤 분석 결과의 문장을 그대로 읽고, 다음 답변은 짚었는지만 판단한 뒤 인정 또는 정정을 다음 질문 앞에 붙여 말합니다.
결과는 `Report.misstatement`에만 적고 역량 점수와 코드 지도에는 쓰지 않습니다. LLM 없이 진행하는 면접과 다시 보기 면접에서는 하지 않고, `BRAIN_MISSTATEMENT=false`로 끌 수 있습니다.
면접자 시선 요약(#177)은 `POST /api/sessions/{id}/end` 본문(`SessionEnd.gaze`, 계약 `GazeSummary`)으로 받습니다. 본문 없이 보내도 됩니다.
서버는 범위만 검사해 `Report.gaze`에 그대로 담고, 역량 점수, 코드 지도, 성장 비교에는 쓰지 않습니다. 리포트를 처음 만들 때의 값만 남습니다.
웹에서는 면접 시작 화면에서 면접자가 켰을 때만 시선을 잽니다(기본 꺼짐).
`web/lib/gaze/`가 MediaPipe FaceLandmarker로 웹캠 영상을 브라우저 안에서만 처리하고, 3초 동안 카메라를 보게 해 기준을 잡은 뒤 장면마다 카메라 / 화면 / 화면 밖 / 얼굴 없음으로 나눕니다.
영상과 랜드마크는 서버로 보내거나 저장하지 않고, 면접을 끝낼 때 요약 숫자만 보냅니다.
시선 분석 프로그램(wasm)과 모델은 우리 웹이 내려 줍니다. wasm은 `dev`·`build` 전에 패키지에서 `web/public/mediapipe/wasm`으로 복사하고(`web/scripts/copy-mediapipe-wasm.mjs`, 커밋하지 않음), 모델과 라이선스는 `web/public/mediapipe`에 있습니다.
다만 MediaPipe가 60초마다 사용 통계(작업 종류, 실행 모드, OS 종류, 라이브러리 버전, 초기화·처리 시간 같은 숫자)를 `odml.pa.googleapis.com`으로 보냅니다. 영상, 랜드마크, 시선 결과는 들어 있지 않고, 끄는 공개 옵션은 없습니다. 보내기에 실패해도 측정은 멈추지 않습니다.
면접 화면 주소에 `?gaze_debug=1`을 붙이면 상단 칩에 지금 장면의 판정이 보입니다. 실제 웹캠으로 기준값(`web/lib/gaze/classify.ts`)을 맞출 때 씁니다.
틀린 말은 2단계 분석이 만듭니다(#142). 주제 파일에 이름이 한 번도 나오지 않는 레포 함수를 "직접 부르죠?"라고 말하고(absent_call), 고를 함수가 없으면 요점 하나를 반대로 말합니다(inverted_point). 틀린 말만 검증에 실패하면 주제는 그대로 두고 틀린 말만 비웁니다.
세션 API가 DB에 연결하지 못하면 503을 돌려줍니다. 턴은 `turn_id`로 한 번만 처리하므로 같은 요청을 다시 보내면 됩니다.
연결이 아닌 DB 오류(테이블 없음, 쿼리와 스키마 불일치)는 다시 보내도 같으므로 사유가 있는 500(`database_error`)입니다 (#101). 로그에는 예외 종류만 남깁니다.
서버는 시작할 때 DB의 마이그레이션 번호를 코드의 마지막 번호와 비교해, 다르면 `schema_not_migrated`·`schema_outdated` 오류 로그를 남깁니다(`schema_check.py`). 마이그레이션을 직접 돌리지는 않고, 서버는 그대로 뜹니다.
DB 연결과 테이블 정의는 `api/src/gitinsight_api/db.py`, `tables.py`, 마이그레이션은 `api/migrations/versions/`에 있습니다. 새 테이블은 두 곳을 같이 바꾸고 `alembic check`로 맞는지 확인합니다.
기본 `pytest`는 DB 없이 돕니다(`api/tests/conftest.py`가 `DATABASE_URL`을 비웁니다). 실제 Postgres 테스트는 `postgres` 표시로 따로 돌립니다. 공용 픽스처 `test_database_url`, `pg_engine`(마이그레이션 적용 후 모든 테이블을 비움)도 `conftest.py`에 있습니다.
서버는 `--loop gitinsight_api.event_loop:server_loop_factory`로 띄웁니다. psycopg 3 비동기 연결이 Windows 기본 루프(Proactor)를 지원하지 않아 Windows에서는 Selector 루프를 씁니다(다른 OS는 uvicorn 기본값). 테스트는 `conftest.py`가 같은 루프를 씁니다. 이 옵션 없이 Windows에서 `DATABASE_URL`을 넣으면 `/api/metrics`가 계속 503입니다.
`api/alembic.ini`는 ASCII만 씁니다. 한국어 Windows에서는 configparser가 cp949로 읽기 때문입니다.
