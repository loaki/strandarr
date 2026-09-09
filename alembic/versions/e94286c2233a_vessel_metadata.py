"""vessel metadata"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e94286c2233a'
down_revision: Union[str, Sequence[str], None] = '65a4cc2e440a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('vessel_position', sa.Column('ship_name', sa.String(length=128), nullable=True))
    op.add_column('vessel_position', sa.Column('flag', sa.String(length=8), nullable=True))
    op.add_column('vessel_position', sa.Column('gear_type', sa.String(length=64), nullable=True))
    op.add_column('vessel_position', sa.Column('vessel_type', sa.String(length=64), nullable=True))
    op.add_column('vessel_position', sa.Column('effort_hours', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('vessel_position', 'effort_hours')
    op.drop_column('vessel_position', 'vessel_type')
    op.drop_column('vessel_position', 'gear_type')
    op.drop_column('vessel_position', 'flag')
    op.drop_column('vessel_position', 'ship_name')
