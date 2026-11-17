"""analyses 테이블 (분석 기록과 결과, #12)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analyses",
        sa.Column("analysis_id", sa.Text(), nullable=False),
        sa.Column("repo_full_name", sa.Text(), nullable=False),
        sa.Column("commit_sha", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("progress", sa.Double(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'stage1', 'stage2', 'done', 'failed')",
            name=op.f("ck_analyses_status_values"),
        ),
        sa.PrimaryKeyConstraint("analysis_id", name=op.f("pk_analyses")),
    )
    op.create_index(
        "uq_analyses_reusable_key",
        "analyses",
        ["repo_full_name", "commit_sha", "user_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'failed'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_analyses_reusable_key",
        table_name="analyses",
        postgresql_where=sa.text("status <> 'failed'"),
    )
    op.drop_table("analyses")
