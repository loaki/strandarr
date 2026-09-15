"""hold deferred jobs until not_before"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "2b8d51e7c093"
down_revision: Union[str, Sequence[str], None] = "1f6c40a9d2b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "job", sa.Column("not_before", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("job", "not_before")
