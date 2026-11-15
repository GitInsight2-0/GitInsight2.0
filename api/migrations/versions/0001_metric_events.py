"""metric_events 테이블 (/api/metrics 지연 측정 이벤트)

Revision ID: 0001
Revises:
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "metric_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("turn_id", sa.Uuid(), nullable=True),
        sa.Column("at_ms", sa.BigInteger(), nullable=False),
        sa.Column("value_ms", sa.Double(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("scenario", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metric_events")),
    )


def downgrade() -> None:
    op.drop_table("metric_events")
