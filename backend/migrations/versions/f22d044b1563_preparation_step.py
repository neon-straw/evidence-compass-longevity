"""preparation step

Revision ID: f22d044b1563
Revises: f164f91498fb
Create Date: 2026-09-23 19:56:08.439778

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f22d044b1563"
down_revision: str | Sequence[str] | None = "f164f91498fb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STEPS = "'question', 'criteria', 'search', 'selection', '{}', 'result'"


def _rename(old: str, new: str) -> None:
    op.drop_constraint("reached_step_known", "studies", type_="check")
    op.execute(f"UPDATE studies SET reached_step = '{new}' WHERE reached_step = '{old}'")
    op.create_check_constraint(
        "reached_step_known", "studies", f"reached_step IN ({STEPS.format(new)})"
    )


def upgrade() -> None:
    """Шаг «Извлечение» переименован в «Подготовка» (#13)."""
    _rename("extraction", "preparation")


def downgrade() -> None:
    _rename("preparation", "extraction")
