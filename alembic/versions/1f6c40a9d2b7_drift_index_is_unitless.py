"""rename drift_arrival.expected_count to drift_index"""

from typing import Sequence, Union

from alembic import op

revision: str = "1f6c40a9d2b7"
down_revision: Union[str, Sequence[str], None] = "7e2b9c4a1d50"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("drift_arrival", "expected_count", new_column_name="drift_index")


def downgrade() -> None:
    op.alter_column("drift_arrival", "drift_index", new_column_name="expected_count")
