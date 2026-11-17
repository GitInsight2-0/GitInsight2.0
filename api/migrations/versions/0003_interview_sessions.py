"""interview_sessions, interview_turns 테이블 (면접 세션과 턴 기록, #53)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "interview_sessions",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.PrimaryKeyConstraint("session_id", name=op.f("pk_interview_sessions")),
    )
    op.create_table(
        "interview_turns",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("record", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["interview_sessions.session_id"],
            name=op.f("fk_interview_turns_session_id_interview_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", "turn_id", name=op.f("pk_interview_turns")),
        sa.UniqueConstraint(
            "session_id", "turn_index", name=op.f("uq_interview_turns_session_id_turn_index")
        ),
    )


def downgrade() -> None:
    op.drop_table("interview_turns")
    op.drop_table("interview_sessions")
