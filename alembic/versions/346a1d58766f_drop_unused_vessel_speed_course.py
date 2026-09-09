"""drop unused vessel speed course"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '346a1d58766f'
down_revision: Union[str, Sequence[str], None] = '567ec3d9d083'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('vessel_position', 'speed')
    op.drop_column('vessel_position', 'course')


def downgrade() -> None:
    op.add_column('vessel_position', sa.Column('course', sa.DOUBLE_PRECISION(precision=53), autoincrement=False, nullable=True))
    op.add_column('vessel_position', sa.Column('speed', sa.DOUBLE_PRECISION(precision=53), autoincrement=False, nullable=True))
