# Railway API 서버 이미지 (#19). API와 분석 워커가 한 프로세스로 뜹니다 (gitinsight_worker.server).
# 웹(web/)은 Vercel이 따로 빌드하므로 넣지 않습니다. 배포 방법은 docs/deploy.md에 있습니다.
FROM python:3.12-slim

# 분석 워커가 레포를 받을 때 git을 씁니다.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.27 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 의존성 층을 먼저 만들어, 코드만 바뀐 배포는 이 층을 다시 쓰게 합니다.
COPY pyproject.toml uv.lock .python-version ./
COPY contracts/pyproject.toml contracts/pyproject.toml
COPY api/pyproject.toml api/pyproject.toml
COPY worker/pyproject.toml worker/pyproject.toml
RUN uv sync --frozen --no-dev --no-install-workspace

# 샘플 분석(fixtures/)은 contracts 소스 위치 기준으로 찾으므로 저장소 구조 그대로 복사합니다.
COPY contracts contracts
COPY api api
COPY worker worker
COPY fixtures fixtures
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# 분석 워커는 root가 아닌 사용자로 돕니다.
RUN useradd --create-home --uid 10001 app && mkdir -p /app/.data && chown app /app/.data
USER app

# Railway가 PORT를 넣습니다. 로컬에서 docker run 할 때는 8000번입니다.
CMD ["sh", "-c", "exec uvicorn --factory gitinsight_worker.server:app_factory --loop gitinsight_api.event_loop:server_loop_factory --host 0.0.0.0 --port ${PORT:-8000}"]
