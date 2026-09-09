"""split species columns"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '6241ea33df99'
down_revision: Union[str, Sequence[str], None] = '346a1d58766f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('stranding', 'species', new_column_name='species_scientific')
    op.add_column('stranding', sa.Column('species_common', sa.String(length=255), nullable=True))
    # histocarto stored a French common name in the old single column.
    op.execute(
        "UPDATE stranding SET species_common = species_scientific, species_scientific = NULL "
        "WHERE source = 'pelagis_histocarto'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE stranding SET species_scientific = species_common "
        "WHERE species_scientific IS NULL AND species_common IS NOT NULL"
    )
    op.drop_column('stranding', 'species_common')
    op.alter_column('stranding', 'species_scientific', new_column_name='species')
