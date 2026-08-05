"""Add document archiving columns.

Revision ID: 002
Revises: 001
Create Date: 2026-08-05 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "documents",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "archived_at")
    op.drop_column("documents", "is_archived")
