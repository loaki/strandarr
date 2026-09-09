"""stranding individual count"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '567ec3d9d083'
down_revision: Union[str, Sequence[str], None] = '443528245912'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'stranding',
        sa.Column('individual_count', sa.Integer(), nullable=False, server_default='1'),
    )
    op.alter_column('stranding', 'individual_count', server_default=None)


def downgrade() -> None:
    op.drop_column('stranding', 'individual_count')
