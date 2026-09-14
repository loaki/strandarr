"""drop drift columns nothing reads back"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "3c81de4a09b7"
down_revision: Union[str, Sequence[str], None] = "f2a7c31b8e05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_drift_arrival_segment", table_name="drift_arrival")
    op.drop_column("drift_arrival", "particles")
    op.drop_column("drift_release", "seeds")
    op.drop_column("drift_release", "particles")
    op.drop_column("drift_release", "released_weight")
    op.drop_column("drift_release", "stranded_weight")
    op.drop_column("drift_release", "forcing_hours")


def downgrade() -> None:
    op.add_column(
        "drift_release",
        sa.Column("forcing_hours", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "drift_release",
        sa.Column("stranded_weight", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "drift_release",
        sa.Column("released_weight", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "drift_release",
        sa.Column("particles", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "drift_release",
        sa.Column("seeds", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "drift_arrival",
        sa.Column("particles", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_drift_arrival_segment",
        "drift_arrival",
        ["coastal_segment_id", "arrival_at"],
    )
