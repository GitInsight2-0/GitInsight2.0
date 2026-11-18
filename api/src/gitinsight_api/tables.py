"""Postgres 테이블 정의 (SQLAlchemy Core).

테이블을 바꾸면 같은 변경을 api/migrations/versions에 마이그레이션으로 추가합니다.
원본 코드를 저장하는 컬럼은 만들지 않습니다 (README 핵심 설계 원칙 4).
"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

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

# 분석 기록입니다 (#12). 기록 하나가 분석 요청 하나이고, 끝나면 결과(AnalysisResult JSON)를 가집니다.
# 원본 코드는 저장하지 않습니다. 결과에는 계약에 정의된 값(심볼 위치, 요약, 질문)만 들어갑니다.
analyses = Table(
    "analyses",
    metadata,
    Column("analysis_id", Text, primary_key=True),
    # 캐시 키입니다. 기여 라벨이 요청자마다 달라서 요청자(GitHub 숫자 id)까지 넣습니다.
    Column("repo_full_name", Text, nullable=False),
    Column("commit_sha", Text, nullable=False),
    Column("user_id", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("progress", Double, nullable=False),
    # failed일 때 예외 종류 이름만 넣습니다.
    Column("error", Text, nullable=True),
    # done일 때만 채웁니다. 읽을 때 AnalysisResult로 다시 검증합니다.
    Column("result", JSONB, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    # 오래됨(워커가 죽음) 판단에 씁니다. 운영에서는 DB 시계(now())로만 씁니다.
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "status IN ('queued', 'stage1', 'stage2', 'done', 'failed')", name="status_values"
    ),
    # 키당 재사용할 기록(실패하지 않은 기록)은 하나뿐입니다.
    # 같은 키의 동시 요청은 이 인덱스와 INSERT … ON CONFLICT DO NOTHING으로 기록 하나만 만듭니다.
    Index(
        "uq_analyses_reusable_key",
        "repo_full_name",
        "commit_sha",
        "user_id",
        unique=True,
        postgresql_where=text("status <> 'failed'"),
    ),
)

# 면접 세션입니다 (#53). 상태(brain/state.py의 SessionState)를 JSON으로 저장하고, 읽을 때 다시 검증합니다.
# 원본 코드와 JD 원문은 들어가지 않습니다.
interview_sessions = Table(
    "interview_sessions",
    metadata,
    Column("session_id", Uuid, primary_key=True),
    Column("state", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# 면접 턴 기록입니다 (#53). 기록(TurnRecord)에는 질문, 답변 자막, 판단, 응답이 들어갑니다.
interview_turns = Table(
    "interview_turns",
    metadata,
    Column(
        "session_id",
        Uuid,
        ForeignKey("interview_sessions.session_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    # 기본 키 (session_id, turn_id)가 같은 턴을 두 번 저장하지 못하게 막습니다.
    Column("turn_id", Uuid, primary_key=True),
    # 세션 안의 순서(TurnRecord.index)입니다.
    Column("turn_index", Integer, nullable=False),
    Column("record", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("session_id", "turn_index"),
)

# 면접 리포트입니다 (#75). 리포트(계약 Report)를 JSON으로 저장하고, 읽을 때 다시 검증합니다.
# 리포트에는 점수, 근거 인용문, 코드 위치, 턴 표가 들어갑니다. 원본 코드는 들어가지 않습니다.
reports = Table(
    "reports",
    metadata,
    Column("report_id", Uuid, primary_key=True),
    Column(
        "session_id",
        Uuid,
        ForeignKey("interview_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("report", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    # 세션 소유자(GitHub 숫자 id)입니다 (#77). 이전 면접 비교에서 본인 리포트만 고르는 데 씁니다.
    # 소유자 없이 만든 세션(로그인 없는 실행)의 리포트는 비워 둡니다.
    Column("owner_id", Text, nullable=True),
    # 세션 하나에 리포트는 하나입니다. 같은 세션을 동시에 끝내도 먼저 저장된 것만 남습니다.
    UniqueConstraint("session_id"),
    Index("ix_reports_owner_id_created_at", "owner_id", "created_at"),
)
