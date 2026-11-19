"""서버 시작 때 DB 마이그레이션이 적용됐는지 확인합니다 (#101).

- 마이그레이션은 배포 전 명령(`alembic upgrade head`)으로만 돌리고, 여기서는 확인해서 로그만 남깁니다.
- 결과와 상관없이 서버는 뜹니다. DB가 아직 준비되지 않았을 수 있기 때문입니다.
- 로그에는 리비전 번호와 예외 종류만 남깁니다. 예외 메시지에는 접속 주소가 섞일 수 있습니다.
"""

import logging
from enum import StrEnum
from pathlib import Path

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# api/src/gitinsight_api/schema_check.py → api/migrations
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

UPGRADE_HINT = "alembic -c api/alembic.ini upgrade head"


class SchemaStatus(StrEnum):
    CURRENT = "current"
    NOT_MIGRATED = "not_migrated"
    OUTDATED = "outdated"
    UNKNOWN = "unknown"


def head_revision(migrations_dir: Path = MIGRATIONS_DIR) -> str | None:
    """코드에 들어 있는 마지막 마이그레이션 번호입니다. 마이그레이션 폴더가 없으면 None입니다."""
    if not migrations_dir.is_dir():
        return None
    return ScriptDirectory(str(migrations_dir)).get_current_head()


def _current_revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


async def check_schema(engine: AsyncEngine, migrations_dir: Path = MIGRATIONS_DIR) -> SchemaStatus:
    """DB의 마이그레이션 번호를 코드의 마지막 번호와 비교해 로그를 남깁니다."""
    head = head_revision(migrations_dir)
    if head is None:
        logger.warning("schema_check_skipped reason=no_migrations_dir")
        return SchemaStatus.UNKNOWN
    try:
        async with engine.connect() as connection:
            current = await connection.run_sync(_current_revision)
    except SQLAlchemyError as error:
        logger.warning("schema_check_failed error=%s", type(error).__name__)
        return SchemaStatus.UNKNOWN
    if current is None:
        logger.error(
            "schema_not_migrated head=%s 마이그레이션이 적용되지 않았습니다 (%s)",
            head,
            UPGRADE_HINT,
        )
        return SchemaStatus.NOT_MIGRATED
    if current != head:
        logger.error(
            "schema_outdated current=%s head=%s 마이그레이션이 최신이 아닙니다 (%s)",
            current,
            head,
            UPGRADE_HINT,
        )
        return SchemaStatus.OUTDATED
    logger.info("schema_current revision=%s", current)
    return SchemaStatus.CURRENT
