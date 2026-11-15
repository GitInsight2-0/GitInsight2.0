"""Alembic 실행 환경. 접속 주소는 DATABASE_URL 환경 변수에서만 읽습니다.

- 온라인(기본): DATABASE_URL의 DB에 연결해 마이그레이션을 적용합니다.
- 오프라인(--sql): DB에 연결하지 않고 SQL만 출력합니다. DATABASE_URL이 없어도 됩니다.
"""

from alembic import context
from sqlalchemy import create_engine, pool

import gitinsight_api.tables  # noqa: F401  테이블을 metadata에 등록합니다.
from gitinsight_api.db import metadata, normalize_url
from gitinsight_api.settings import Settings

config = context.config


def database_url() -> str | None:
    # 테스트는 config.attributes로 주소를 넘깁니다. 그 밖에는 Settings(DATABASE_URL, .env)에서 읽습니다.
    url = config.attributes.get("database_url")
    if url:
        return url
    secret = Settings().database_url
    return secret.get_secret_value() if secret is not None else None


def run_offline() -> None:
    url = database_url()
    context.configure(
        url=normalize_url(url) if url else None,
        dialect_name=None if url else "postgresql",
        target_metadata=metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    url = database_url()
    if not url:
        raise SystemExit("DATABASE_URL이 없습니다. .env 또는 환경 변수에 Postgres 주소를 넣으세요.")
    engine = create_engine(normalize_url(url), poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=metadata, compare_type=True)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
