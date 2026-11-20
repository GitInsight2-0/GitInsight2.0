# 배포 (Railway·Vercel)

이 문서는 #19 기준 배포 방법, 환경 변수, 롤백을 적습니다.
시연 당일 대응은 `docs/runbook-demo.md`를 봅니다.

## 구성

| 대상 | 어디에 | 무엇으로 |
|---|---|---|
| API 서버 + 분석 워커 | Railway 서비스 1개 | 루트 `Dockerfile`, `railway.json` |
| DB | Railway Postgres | Railway 템플릿 |
| 웹 | Vercel | `web/` (Next.js) |

- 분석 워커는 따로 서비스를 두지 않고 API 서버 프로세스 안에서 돕니다 (`gitinsight_worker.server:app_factory`, 같은 프로세스 Launcher).
- 그래서 Railway API 서비스는 인스턴스를 **1개로 둡니다** (`railway.json`의 `numReplicas`). 늘리면 진행 중인 분석을 다른 인스턴스가 모릅니다.
- 브라우저는 웹 주소의 `/api/*`만 부르고, Vercel이 Railway로 넘깁니다 (`web/next.config.ts`). 그래서 서버 `CORS_ORIGINS`는 비워 두고, 로그인 쿠키도 웹 도메인에 붙습니다.

## 순서

처음 배포할 때는 아래 순서로 합니다. 주소가 서로 필요해서 Railway → Vercel → Railway 순서입니다.

1. **Railway 프로젝트**
   1. New Project → Deploy from GitHub repo → 이 저장소, 브랜치 `main`. pnpm workspace를 보고 `@gitinsight/web`, `@gitinsight/contracts` 서비스로 나눠 만들면 둘 다 지우고, Empty Service를 만든 뒤 Settings → Source에서 이 저장소를 연결합니다 (Root Directory는 비움)
   2. 같은 프로젝트에 Postgres 추가 (New → Database → PostgreSQL)
   3. API 서비스 Settings → Networking → Generate Domain. 이 주소가 `API_ORIGIN`입니다
   4. 빌드와 시작은 `railway.json`이 정합니다. 대시보드의 Build·Start 명령은 비워 둡니다
   5. **Settings → Deploy의 Pre-deploy Command가 `alembic -c api/alembic.ini upgrade head`인지 꼭 확인합니다.**
      빈 서비스(Empty Service)에 저장소를 연결해 만들었을 때 이 값이 비어 있었고, 마이그레이션 없이 떠서 분석 API가 500·503이었습니다 (#19).
      비어 있으면 직접 넣고 재배포합니다. 서버 시작 로그에 `schema_not_migrated`나 `schema_outdated`가 보이면 이 경우입니다 (#101)
2. **Railway API 서비스 Variables**: 아래 "환경 변수" 표대로 넣습니다. `WEB_ORIGIN`은 3번 뒤에 넣어도 됩니다
3. **Vercel 프로젝트**
   1. Add New → Project → 이 저장소
   2. Root Directory: `web`. Framework: Next.js. Node.js 버전: 22
   3. Install·Build 명령은 기본값(pnpm 자동 감지)을 씁니다. 루트 밖 파일 포함(Include files outside the root directory)은 켜 둡니다 (`contracts/ts`를 workspace로 씀)
   4. 아래 "환경 변수" 표의 Vercel 변수를 넣고 배포합니다
4. **Railway `WEB_ORIGIN`**: Vercel 주소(`https://<프로젝트>.vercel.app`)를 넣고 재배포합니다
5. **GitHub App Callback URL**: App 주인(B)이 `https://<Vercel 주소>/api/auth/github/callback`을 추가합니다. 로컬 주소는 지우지 않습니다
6. **시연용 레포 미리 분석**: 시연 계정으로 로그인해 시연에 쓸 레포를 한 번 분석해 둡니다 (`docs/runbook-demo.md`). 배포 DB에는 데모 분석 스냅샷을 넣지 않습니다 (#19)
7. **확인**: 아래 "배포 확인"

이후 배포는 `main`에 머지하면 Railway와 Vercel이 각자 자동으로 합니다.

## 환경 변수

키 값은 Railway·Vercel 변수에만 넣습니다. git, 메신저, 이슈에 올리지 않습니다.

### Railway API 서비스

| 변수 | 값 | 비고 |
|---|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` | Railway 참조 변수. 없으면 측정 이벤트가 컨테이너 디스크에 쌓였다 재배포 때 사라지고, 분석 요청은 503 |
| `LAB_ENABLED` | `false` | 기본값과 같지만 명시합니다 |
| `GEMINI_API_KEY` | 결제 등록 키 | 판단·리포트. 무료 등급 키는 429·503으로 거의 실패 (#69) |
| `OPENAI_API_KEY` | BizRouter 키 | 분석(출제, 검토, 면접 계획, #202). 분석 중 사용자 레포 코드가 BizRouter를 거쳐 OpenAI로 감 |
| `OPENAI_API_BASE` | `https://api.bizrouter.ai/v1` | `openai/`로 시작하는 모델 호출을 BizRouter로 보냄. 판단·리포트(`gemini/`)는 영향 없음 |
| `LLM_ANALYSIS_MODEL` | `openai/openai/gpt-6.2-sol` | BizRouter의 모델 이름(`openai/gpt-6.2-sol`)에 LiteLLM 접두사 `openai/`를 붙인 값. OpenAI에 직접 붙이면 넣지 않음(코드 기본값 `openai/gpt-6.2-sol`) |
| `GITHUB_CLIENT_ID` | GitHub App Client ID | B에게 직접 받음 |
| `GITHUB_CLIENT_SECRET` | GitHub App Client secret | B에게 직접 받음 |
| `SESSION_SECRET` | 배포 전용 무작위 값 | `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. 로컬 값과 따로 만들고 공유하지 않음. 바꾸면 모두 로그아웃됨 |
| `WEB_ORIGIN` | `https://<Vercel 주소>` | 끝에 `/` 없이. GitHub App Callback URL과 맞아야 함 |

넣지 **않는** 변수:

- `PORT`: Railway가 넣습니다
- `LLM_PROVIDER`, `LLM_JUDGE_MODEL`, `LLM_JUDGE_REASONING_EFFORT`, `JUDGE_TIMEOUT_S`: 비워 두면 새 기본값(Gemini)을 씁니다 (#69)
- `CORS_ORIGINS`: 쓰지 않습니다
- `COOKIE_SECURE`: 기본값(켬)을 씁니다. 배포 주소가 https라 끌 이유가 없습니다

### Vercel 웹

| 변수 | 값 | 비고 |
|---|---|---|
| `API_ORIGIN` | `https://<Railway API 주소>` | 끝에 `/` 없이. **빌드할 때 정해지므로** 바꾸면 다시 배포해야 함 |
| `NEXT_PUBLIC_GITHUB_APP_SLUG` | 넣지 않음 | 첫 화면의 "GitHub에서 레포 연결하기" 버튼이 갈 App 이름 (#150). 비우면 코드 기본값 `gitinsight-sclp`를 씀 (#159). 다른 App으로 시험할 때만 넣고, 빌드할 때 정해지므로 바꾸면 다시 배포 |
| `GEMINI_API_KEY` | 결제 등록 키 | 음성 토큰(`/voice-token`) |
| `LAB_ENABLED` | 넣지 않음 | 넣지 않으면 실험 화면(`/lab/*`)이 닫히고 음성 토큰에 로그인이 필요 |

`GEMINI_LIVE_MODEL`, `GEMINI_VOICE`는 기본값을 씁니다.

## Railway가 하는 일 (`railway.json`)

- 빌드: 루트 `Dockerfile` (Python 3.12, git, `uv sync --frozen --no-dev`)
- **배포 전 명령**: `alembic -c api/alembic.ini upgrade head`. 새 버전이 뜨기 전에 한 번만 돌고, 실패하면 배포가 멈추고 이전 버전이 계속 돕니다. 서버 시작 때는 마이그레이션을 돌리지 않습니다
- 시작: `uvicorn --factory gitinsight_worker.server:app_factory ... --port $PORT` (Dockerfile `CMD`)
- 헬스체크: `GET /api/health`. 프로세스 상태만 보고 DB는 확인하지 않습니다. DB 장애는 재시작으로 풀리지 않기 때문입니다

## 배포 확인

배포 뒤 아래를 차례로 봅니다.

1. `curl https://<Railway API 주소>/api/health` → `{"status":"ok"}`
2. `curl -i https://<Vercel 주소>/api/me` → 401 (웹이 서버로 넘기고 있음)
3. 브라우저로 Vercel 주소 → GitHub 로그인 → 첫 화면에 사용자 이름
4. 레포 목록에서 Python 레포를 골라 분석 → 텍스트 면접 몇 턴 → 끝내기 → 리포트
5. Railway 로그 첫 줄들에 `schema_current revision=<마지막 번호>`가 있고 오류가 없으며, 토큰·code·키가 찍히지 않음

## 롤백

- **서버**: Railway API 서비스 → Deployments → 이전 성공 배포 → Redeploy
- **웹**: Vercel → Deployments → 이전 배포 → Promote to Production (Instant Rollback)
- **DB 마이그레이션은 되돌리지 않습니다.** 마이그레이션 0001~0005는 새 테이블이나 null 허용 열만 더해서, 이전 서버 버전도 그대로 돕니다.
  새 마이그레이션도 이 방식을 지키면 서버만 롤백하면 됩니다.
- **예외: 0006(분석기 버전, #171) 이전 서버로 롤백할 때는 DB도 0005로 되돌립니다.**
  0006은 분석 재사용 키(`uq_analyses_reusable_key`)에 `analyzer_version`을 더합니다. 이전 서버는 예전 키로 `INSERT … ON CONFLICT`를 하므로 맞는 인덱스가 없어 분석 요청이 실패합니다.
  되돌리기: 백업을 만든 뒤 `uv run alembic -c api/alembic.ini downgrade 0005`. 키마다 가장 최근 기록만 남기고 나머지 재사용 기록은 failed(`AnalyzerVersionDowngraded`)로 닫습니다.
  닫힌 기록의 분석으로 하던 면접 이어 가기와 "부족했던 질문만 다시 면접 보기"는 분석 결과를 읽지 못해 "분석 결과를 찾을 수 없습니다"가 됩니다. 리포트 열기는 `reports` 테이블을 읽어서 그대로 됩니다.
  꼭 되돌려야 하면 Railway Public URL로 `uv run alembic -c api/alembic.ini downgrade -1`을 직접 실행하고, 그 전에 Railway Postgres의 Backups에서 백업을 만듭니다.
- 환경 변수만 잘못 넣었으면 롤백 대신 변수를 고치고 재배포합니다 (약 1~2분).
- **계약에 필드를 더한 PR 이전으로는 서버만 롤백하면 안 됩니다** (#162).
  계약 모델은 모르는 필드를 거부하고(`extra="forbid"`), 분석 결과·세션 상태·턴 기록·리포트는 값이 null인 필드도 키째 저장합니다.
  그래서 이전 서버 버전은 새 필드가 생긴 뒤 저장된 기록을 검증 오류로 읽지 못합니다. 면접 시작, 코드 보기, 면접 이어 가기, 리포트 열기가 서버 오류(500)로 실패합니다.
  분석 상태 보기(`GET /api/analyses/{id}`)는 결과 JSON을 읽지 않아 그대로 됩니다.
  예: 일부러 틀린 말 필드(#137), 질문과 관계있는 줄 범위(#161).
  - 롤백하기 전에 되돌아갈 배포와 지금 사이에 계약 변경이 있는지 봅니다: `git log --oneline <되돌아갈 커밋>..main -- contracts/src`
  - 있으면 롤백 대신 문제를 고친 PR을 머지해 앞으로 배포합니다. 급하면 그 계약 변경이 들어간 뒤의 배포 중에서 고릅니다.

## 로컬에서 이미지 확인

배포 전에 Railway와 같은 이미지를 로컬에서 띄워 볼 수 있습니다.

```text
docker build -t gitinsight-api:local .
docker run --rm -e DATABASE_URL=postgresql://... gitinsight-api:local alembic -c api/alembic.ini upgrade head
docker run --rm -p 8000:8000 -e DATABASE_URL=postgresql://... -e SESSION_SECRET=... -e WEB_ORIGIN=http://localhost:3000 -e COOKIE_SECURE=false gitinsight-api:local
```

## 알려진 제약

- Vercel이 `/api/*`를 넘길 때 시간 제한이 있어, 긴 리포트 요청은 웹이 다시 시도합니다 (#78).
- 분석 레포를 받는 몇 초 사이에 로그아웃하면 토큰이 폐기돼 그 분석은 실패합니다 (#92). 다시 로그인해 분석하면 됩니다.
