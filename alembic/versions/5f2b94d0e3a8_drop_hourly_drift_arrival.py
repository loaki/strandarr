"""drop the hourly drift_arrival table and reclaim its storage"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "5f2b94d0e3a8"
down_revision: Union[str, Sequence[str], None] = "4e1a83c9d2f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TOLERANCE = 1e-9


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("drift_arrival"):
        hourly = float(
            bind.execute(
                sa.text("select coalesce(sum(drift_index), 0) from drift_arrival")
            ).scalar_one()
        )
        daily = float(
            bind.execute(
                sa.text("select coalesce(sum(drift_index), 0) from drift_daily")
            ).scalar_one()
        )
        if abs(hourly - daily) > TOLERANCE * max(abs(hourly), 1.0):
            raise RuntimeError(
                f"drift_daily holds {daily!r} of drift but drift_arrival holds "
                f"{hourly!r}. Re-run revision 4e1a83c9d2f7 and check the totals "
                f"before dropping the hourly table."
            )
        op.drop_table("drift_arrival")

    op.execute("analyze drift_daily")


def downgrade() -> None:
    op.create_table(
        "drift_arrival",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("release_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_version", sa.String(length=16), nullable=False),
        sa.Column("coastal_segment_id", sa.Integer(), nullable=False),
        sa.Column("arrival_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("drift_index", sa.Float(), nullable=False),
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
