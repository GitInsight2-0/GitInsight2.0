"""데모 스냅샷: 샘플 분석 결과(fixtures/analysis/)를 DATABASE_URL의 DB에 완료 기록으로 넣습니다 (#12).

현장에서 분석이 실패해도 이 기록으로 면접을 이어 갑니다 (docs/runbook-demo.md 분석 행).

사용:
  uv run python scripts/seed_demo_analysis.py --user-id <데모 계정 id>
  uv run python scripts/seed_demo_analysis.py --user-id <id> --fixture gitinsight_agent

- 마이그레이션을 먼저 적용합니다 (uv run alembic -c api/alembic.ini upgrade head).
- 기록의 analysis_id는 샘플 파일의 값 그대로라서, 여러 번 실행해도 기록 하나만 남습니다.
- 같은 (레포, 커밋, 요청자)의 다른 기록은 failed(ReplacedBySnapshot)로 닫아, 그 키를 다시 요청하면 스냅샷을 재사용합니다.
- 기여 라벨은 요청자마다 다르므로 데모에 로그인할 계정 id를 --user-id로 줍니다.
"""

import argparse
import asyncio
import sys

from gitinsight_api.analyses.postgres import PostgresAnalysisStore
from gitinsight_api.db import create_engine
from gitinsight_api.event_loop import server_loop_factory
from gitinsight_api.settings import Settings
from gitinsight_contracts.fixtures import load_analysis


async def seed(database_url: str, fixture: str, user_id: str) -> None:
    engine = create_engine(database_url)
    try:
        record = await PostgresAnalysisStore(engine).save_snapshot(load_analysis(fixture), user_id)
    finally:
        await engine.dispose()
    print(f"넣음: analysis_id={record.analysis_id}")
    print(f"레포 {record.key.repo_full_name} @ {record.key.commit_sha[:12]}, 요청자 {user_id}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    settings = Settings()
    parser = argparse.ArgumentParser(description="데모 분석 스냅샷을 DB에 넣습니다")
    parser.add_argument("--user-id", required=True, help="데모에 로그인할 계정 id")
    parser.add_argument(
        "--fixture",
        default=settings.fixture_analysis,
        help="fixtures/analysis/<이름>.json의 이름 (기본: FIXTURE_ANALYSIS)",
    )
    args = parser.parse_args()
    if settings.database_url is None:
        print("DATABASE_URL이 없습니다", file=sys.stderr)
        return 2
    # psycopg 3 비동기 연결은 Windows 기본 루프를 지원하지 않아 서버와 같은 루프를 씁니다.
    asyncio.run(
        seed(settings.database_url.get_secret_value(), args.fixture, args.user_id),
        loop_factory=server_loop_factory,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
