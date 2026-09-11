"""drop columns no connector ever populates"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5e91c73a02f"
down_revision: Union[str, Sequence[str], None] = "c4f8a2b19d63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("stranding", "is_live")
    op.drop_column("coastal_segment", "beach_length_km")
    op.drop_column("coastal_segment", "accessibility_score")
    op.drop_column("coastal_segment", "habitat_type")


def downgrade() -> None:
    op.add_column("coastal_segment", sa.Column("habitat_type", sa.String(32)))
    op.add_column("coastal_segment", sa.Column("accessibility_score", sa.Float()))
    op.add_column("coastal_segment", sa.Column("beach_length_km", sa.Float()))
    op.add_column("stranding", sa.Column("is_live", sa.Boolean()))
