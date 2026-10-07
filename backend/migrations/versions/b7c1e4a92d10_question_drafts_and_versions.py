"""question drafts and versions

Revision ID: b7c1e4a92d10
Revises: 782a8bc20dde
Create Date: 2026-09-23 09:50:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7c1e4a92d10"
down_revision: str | Sequence[str] | None = "782a8bc20dde"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "studies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("reached_step", sa.String(length=32), nullable=False),
        sa.Column("draft_text", sa.Text(), nullable=False),
        sa.Column("draft_saved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "reached_step IN ('question', 'criteria', 'search', 'selection', "
            "'extraction', 'result')",
            name="reached_step_known",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id"),
    )
    op.create_table(
        "question_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("study_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["study_id"], ["studies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("study_id", "number"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("question_versions")
    op.drop_table("studies")
