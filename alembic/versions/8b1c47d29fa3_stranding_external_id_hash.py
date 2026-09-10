"""stranding external_id widened and content-hashed"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '8b1c47d29fa3'
down_revision: Union[str, Sequence[str], None] = '6241ea33df99'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'stranding',
        'external_id',
        existing_type=sa.String(length=64),
        type_=sa.String(length=255),
        existing_nullable=False,
    )
    # histo-carto ids are now content hashes, so rows carrying the old
    # "day|species|commune|index" ids would never match an upsert and would double up on the
    # next ingest. They hold nothing that is not re-fetchable, so drop them and let the next
    # `strandarr ingest` rebuild them under the new ids.
    op.execute("DELETE FROM stranding WHERE source = 'pelagis_histocarto'")


def downgrade() -> None:
    op.execute("DELETE FROM stranding WHERE source = 'pelagis_histocarto'")
    op.alter_column(
        'stranding',
        'external_id',
        existing_type=sa.String(length=255),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
