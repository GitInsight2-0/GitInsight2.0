"""Postgres 테이블 정의 (SQLAlchemy Core).

테이블을 바꾸면 같은 변경을 api/migrations/versions에 마이그레이션으로 추가합니다.
원본 코드를 저장하는 컬럼은 만들지 않습니다 (README 핵심 설계 원칙 4).
"""

from sqlalchemy import BigInteger, Column, DateTime, Double, Identity, Table, Text, Uuid, func

from gitinsight_api.db import metadata

# /api/metrics 지연 측정 이벤트입니다. 컬럼은 계약(MetricEvent)의 필드와 같습니다.
metric_events = Table(
    "metric_events",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    # 서버가 받은 시각입니다. 서버와 워커의 시계가 달라도 DB 시계 하나로 맞춥니다.
    Column("received_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("name", Text, nullable=False),
    Column("session_id", Uuid, nullable=True),
    Column("turn_id", Uuid, nullable=True),
    # 클라이언트 epoch 밀리초입니다.
    Column("at_ms", BigInteger, nullable=False),
    Column("value_ms", Double, nullable=True),
    Column("provider", Text, nullable=True),
    Column("scenario", Text, nullable=True),
    Column("error_code", Text, nullable=True),
)
