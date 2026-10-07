"""extraction operation lifecycle

Revision ID: f49c6c64fc32
Revises: f22d044b1563
Create Date: 2026-09-23 21:09:17.792510

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f49c6c64fc32"
down_revision: str | Sequence[str] | None = "f22d044b1563"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


KINDS = "'criteria_preparation', 'search'"


def upgrade() -> None:
    """Операция извлечения: элементы-работы, журнал и ключ идемпотентности запуска (#14)."""
    op.drop_constraint("kind_known", "operations", type_="check")
    op.create_check_constraint("kind_known", "operations", f"kind IN ({KINDS}, 'extraction')")
    op.create_table(
        "extraction_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.Integer(), nullable=False),
        sa.Column("publication_id", sa.Integer(), nullable=False),
        sa.Column("document_versions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("gaps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("execution", sa.String(length=16), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column("measure_count", sa.Integer(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "execution IN ('queued', 'running', 'done', 'failed', 'skipped')",
            name="execution_known",
        ),
        sa.CheckConstraint(
            "outcome IN ('measures_extracted', 'no_measures', 'unresolved', "
            "'unavailable', 'error')",
            name="outcome_known",
        ),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["publication_id"], ["publications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id", "publication_id"),
    )
    op.create_table(
        "operation_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.add_column("operations", sa.Column("idempotency_key", sa.String(length=200), nullable=True))
    op.create_unique_constraint("operations_idempotency_key_key", "operations", ["idempotency_key"])


def downgrade() -> None:
    op.drop_constraint("operations_idempotency_key_key", "operations", type_="unique")
    op.drop_column("operations", "idempotency_key")
    op.drop_table("operation_events")
    op.drop_table("extraction_items")
    op.execute("DELETE FROM operations WHERE kind = 'extraction'")
    op.drop_constraint("kind_known", "operations", type_="check")
    op.create_check_constraint("kind_known", "operations", f"kind IN ({KINDS})")
