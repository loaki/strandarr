"""seasonal stranding climatology per coastal segment"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "3d9e62b4c15a"
down_revision: Union[str, Sequence[str], None] = "2b8d51e7c093"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "segment_climatology",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("coastal_segment_id", sa.Integer(), nullable=False),
        sa.Column("day_of_year", sa.Integer(), nullable=False),
        sa.Column("model_version", sa.String(length=16), nullable=False),
        sa.Column("observed", sa.Float(), nullable=False),
        sa.Column("expected_per_day", sa.Float(), nullable=False),
        sa.Column("probability", sa.Float(), nullable=False),
        sa.Column("years", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["coastal_segment_id"], ["coastal_segment.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_segment_climatology_dedup",
        "segment_climatology",
        ["coastal_segment_id", "day_of_year", "model_version"],
        unique=True,
    )
    op.create_index(
        "ix_segment_climatology_day", "segment_climatology", ["day_of_year"]
    )


def downgrade() -> None:
    op.drop_index("ix_segment_climatology_day", table_name="segment_climatology")
    op.drop_index("ix_segment_climatology_dedup", table_name="segment_climatology")
    op.drop_table("segment_climatology")
