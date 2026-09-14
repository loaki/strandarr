"""store coastal segment geometry and forward drift arrivals"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2a7c31b8e05"
down_revision: Union[str, Sequence[str], None] = "d5e91c73a02f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "coastal_segment", sa.Column("path", postgresql.JSONB(astext_type=sa.Text()))
    )

    op.create_table(
        "drift_release",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("release_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_version", sa.String(length=16), nullable=False),
        sa.Column("seeds", sa.Integer(), nullable=False),
        sa.Column("particles", sa.Integer(), nullable=False),
        sa.Column("released_weight", sa.Float(), nullable=False),
        sa.Column("stranded_weight", sa.Float(), nullable=False),
        sa.Column("forcing_hours", sa.Integer(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_drift_release_dedup",
        "drift_release",
        ["release_at", "model_version"],
        unique=True,
    )
    op.create_index("ix_drift_release_release_at", "drift_release", ["release_at"])

    op.create_table(
        "drift_arrival",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("release_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_version", sa.String(length=16), nullable=False),
        sa.Column("coastal_segment_id", sa.Integer(), nullable=False),
        sa.Column("arrival_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expected_count", sa.Float(), nullable=False),
        sa.Column("particles", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["coastal_segment_id"], ["coastal_segment.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_drift_arrival_dedup",
        "drift_arrival",
        ["release_at", "coastal_segment_id", "arrival_at", "model_version"],
        unique=True,
    )
    op.create_index("ix_drift_arrival_arrival_at", "drift_arrival", ["arrival_at"])
    op.create_index(
        "ix_drift_arrival_segment",
        "drift_arrival",
        ["coastal_segment_id", "arrival_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_drift_arrival_segment", table_name="drift_arrival")
    op.drop_index("ix_drift_arrival_arrival_at", table_name="drift_arrival")
    op.drop_index("ix_drift_arrival_dedup", table_name="drift_arrival")
    op.drop_table("drift_arrival")

    op.drop_index("ix_drift_release_release_at", table_name="drift_release")
    op.drop_index("ix_drift_release_dedup", table_name="drift_release")
    op.drop_table("drift_release")

    op.drop_column("coastal_segment", "path")
