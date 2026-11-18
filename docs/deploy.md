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
   1. New Project → Deploy from GitHub repo → 이 저장소, 브랜치 `main`
   2. 같은 프로젝트에 Postgres 추가 (New → Database → PostgreSQL)
   3. API 서비스 Settings → Networking → Generate Domain. 이 주소가 `API_ORIGIN`입니다
   4. 빌드와 시작은 `railway.json`이 정합니다. 대시보드의 Build·Start 명령은 비워 둡니다
2. **Railway API 서비스 Variables**: 아래 "환경 변수" 표대로 넣습니다. `WEB_ORIGIN`은 3번 뒤에 넣어도 됩니다
3. **Vercel 프로젝트**
   1. Add New → Project → 이 저장소
   2. Root Directory: `web`. Framework: Next.js. Node.js 버전: 22
   3. Install·Build 명령은 기본값(pnpm 자동 감지)을 씁니다. 루트 밖 파일 포함(Include files outside the root directory)은 켜 둡니다 (`contracts/ts`를 workspace로 씀)
   4. 아래 "환경 변수" 표의 Vercel 변수를 넣고 배포합니다
4. **Railway `WEB_ORIGIN`**: Vercel 주소(`https://<프로젝트>.vercel.app`)를 넣고 재배포합니다
5. **GitHub App Callback URL**: App 주인(B)이 `https://<Vercel 주소>/api/auth/github/callback`을 추가합니다. 로컬 주소는 지우지 않습니다
6. **데모 스냅샷**: `docs/runbook-demo.md`대로 `scripts/seed_demo_analysis.py --user-id <id>`를 Railway Postgres 주소(Postgres → Connect → Public URL)로 실행합니다
7. **확인**: 아래 "배포 확인"

이후 배포는 `main`에 머지하면 Railway와 Vercel이 각자 자동으로 합니다.

## 환경 변수

키 값은 Railway·Vercel 변수에만 넣습니다. git, 메신저, 이슈에 올리지 않습니다.

### Railway API 서비스

| 변수 | 값 | 비고 |
|---|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` | Railway 참조 변수. 없으면 측정 이벤트가 컨테이너 디스크에 쌓였다 재배포 때 사라지고, 분석 요청은 503 |
| `LAB_ENABLED` | `false` | 기본값과 같지만 명시합니다 |
| `GEMINI_API_KEY` | 결제 등록 키 | 판단·분석·리포트. 무료 등급 키는 429·503으로 거의 실패 (#69) |
| `GITHUB_CLIENT_ID` | GitHub App Client ID | B에게 직접 받음 |
| `GITHUB_CLIENT_SECRET` | GitHub App Client secret | B에게 직접 받음 |
| `SESSION_SECRET` | 배포 전용 무작위 값 | `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. 로컬 값과 따로 만들고 공유하지 않음. 바꾸면 모두 로그아웃됨 |
| `WEB_ORIGIN` | `https://<Vercel 주소>` | 끝에 `/` 없이. GitHub App Callback URL과 맞아야 함 |

넣지 **않는** 변수:

- `PORT`: Railway가 넣습니다
- `LLM_PROVIDER`, `LLM_JUDGE_MODEL`, `LLM_JUDGE_REASONING_EFFORT`, `JUDGE_TIMEOUT_S`: 비워 두면 새 기본값(Gemini)을 씁니다 (#69)
- `OPENAI_API_KEY`, `CORS_ORIGINS`: 쓰지 않습니다
- `COOKIE_SECURE`: 기본값(켬)을 씁니다. 배포 주소가 https라 끌 이유가 없습니다

### Vercel 웹

| 변수 | 값 | 비고 |
|---|---|---|
| `API_ORIGIN` | `https://<Railway API 주소>` | 끝에 `/` 없이. **빌드할 때 정해지므로** 바꾸면 다시 배포해야 함 |
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
4. 데모 레포로 텍스트 면접 몇 턴 → 끝내기 → 리포트
5. Railway 로그 첫 줄들에 오류가 없고, 토큰·code·키가 찍히지 않음
6. 시간이 있으면 `uv run python scripts/demo_check.py --real-llm --from-db --user-id <id>` (DB 주소는 Railway Public URL)

## 롤백

- **서버**: Railway API 서비스 → Deployments → 이전 성공 배포 → Redeploy
- **웹**: Vercel → Deployments → 이전 배포 → Promote to Production (Instant Rollback)
- **DB 마이그레이션은 되돌리지 않습니다.** 지금까지의 마이그레이션(0001~0005)은 새 테이블이나 null 허용 열만 더해서, 이전 서버 버전도 그대로 돕니다.
  새 마이그레이션도 이 방식을 지키면 서버만 롤백하면 됩니다.
  꼭 되돌려야 하면 Railway Public URL로 `uv run alembic -c api/alembic.ini downgrade -1`을 직접 실행하고, 그 전에 Railway Postgres의 Backups에서 백업을 만듭니다.
- 환경 변수만 잘못 넣었으면 롤백 대신 변수를 고치고 재배포합니다 (약 1~2분).

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
