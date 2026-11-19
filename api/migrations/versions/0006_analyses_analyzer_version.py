"""analyses.analyzer_version 컬럼과 재사용 키 (#171)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REUSABLE = sa.text("status <> 'failed'")


def upgrade() -> None:
    # 이미 있는 기록은 버전이 생기기 전의 값("0")으로 채웁니다. 지금 워커 버전과 달라서 다시 요청하면 새로 분석합니다.
    op.add_column(
        "analyses",
        sa.Column("analyzer_version", sa.Text(), nullable=False, server_default="0"),
    )
    op.drop_index("uq_analyses_reusable_key", table_name="analyses", postgresql_where=_REUSABLE)
    op.create_index(
        "uq_analyses_reusable_key",
        "analyses",
        ["repo_full_name", "commit_sha", "user_id", "analyzer_version"],
        unique=True,
        postgresql_where=_REUSABLE,
    )


def downgrade() -> None:
    # 버전만 다른 재사용 기록이 여럿 있으면 이전 인덱스를 만들 수 없으므로, 키마다 가장 최근 것만 남기고 닫습니다.
    op.execute(
        "UPDATE analyses SET status = 'failed', error = 'AnalyzerVersionDowngraded' "
        "WHERE status <> 'failed' AND analysis_id NOT IN ("
        "SELECT DISTINCT ON (repo_full_name, commit_sha, user_id) analysis_id FROM analyses "
        "WHERE status <> 'failed' "
        "ORDER BY repo_full_name, commit_sha, user_id, updated_at DESC)"
    )
    op.drop_index("uq_analyses_reusable_key", table_name="analyses", postgresql_where=_REUSABLE)
    op.create_index(
        "uq_analyses_reusable_key",
        "analyses",
        ["repo_full_name", "commit_sha", "user_id"],
        unique=True,
        postgresql_where=_REUSABLE,
    )
    op.drop_column("analyses", "analyzer_version")
