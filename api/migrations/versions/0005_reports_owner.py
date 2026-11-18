"""reports.owner_id 컬럼 (이전 면접 비교, #77)

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("owner_id", sa.Text(), nullable=True))
    # 이미 저장된 리포트는 세션 상태에 적힌 소유자로 채웁니다.
    op.execute(
        "UPDATE reports SET owner_id = s.state->>'owner_id' "
        "FROM interview_sessions s WHERE s.session_id = reports.session_id"
    )
    op.create_index(
        "ix_reports_owner_id_created_at", "reports", ["owner_id", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_reports_owner_id_created_at", table_name="reports")
    op.drop_column("reports", "owner_id")
