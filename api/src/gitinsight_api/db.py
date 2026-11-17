"""Postgres 연결과 테이블 정의의 기반 (#17). 서버와 분석 워커가 같이 씁니다.

- 라이브러리는 SQLAlchemy 2(Core), 드라이버는 psycopg 3, 마이그레이션은 Alembic(api/migrations)입니다.
- 접속 주소는 DATABASE_URL 환경 변수로만 받고, 오류 메시지와 로그에 넣지 않습니다.
- 테이블은 이 모듈의 metadata에 정의합니다. 새 테이블은 gitinsight_api.tables에 추가하고
  같은 변경을 Alembic 마이그레이션으로 만듭니다.
"""

from sqlalchemy import URL, MetaData, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

DRIVER = "postgresql+psycopg"
# DB 호스트가 응답하지 않을 때 연결을 기다리는 최대 시간(초)입니다.
# 없으면 OS 기본값(Windows에서 약 130초)만큼 요청과 `alembic upgrade`가 멈춥니다.
CONNECT_TIMEOUT_S = 5
CONNECT_ARGS = {"connect_timeout": CONNECT_TIMEOUT_S}

# 제약 조건 이름을 고정해 두면 마이그레이션에서 이름으로 지우거나 바꿀 수 있습니다.
metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


class DatabaseUrlError(ValueError):
    """DATABASE_URL 형식이 잘못되었습니다. 메시지에 주소(비밀번호)를 넣지 않습니다."""


def normalize_url(raw: str) -> URL:
    """Postgres 주소를 psycopg 3 드라이버 주소로 바꿉니다.

    Railway 같은 호스팅은 드라이버 없는 `postgres://`, `postgresql://` 주소를 줍니다.
    """
    try:
        url = make_url(raw.strip())
    except ArgumentError:
        raise DatabaseUrlError("DATABASE_URL을 해석할 수 없습니다") from None
    if url.drivername in {"postgres", "postgresql", DRIVER}:
        return url.set(drivername=DRIVER)
    raise DatabaseUrlError(
        f"DATABASE_URL은 postgresql:// 주소여야 합니다 (받은 형식: {url.drivername})"
    )


def create_engine(raw_url: str) -> AsyncEngine:
    """비동기 엔진을 만듭니다. 만들 때는 연결하지 않고, 처음 쿼리할 때 연결합니다."""
    # pool_pre_ping: 호스팅 쪽에서 끊은 연결을 쓰기 전에 걸러 냅니다.
    return create_async_engine(
        normalize_url(raw_url), pool_pre_ping=True, connect_args=CONNECT_ARGS
    )
