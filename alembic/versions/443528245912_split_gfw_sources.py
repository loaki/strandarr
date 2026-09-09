"""split gfw sources"""

from typing import Sequence, Union

from alembic import op

revision: str = '443528245912'
down_revision: Union[str, Sequence[str], None] = 'e94286c2233a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE vessel_position SET source = 'gfw_fishing' WHERE source = 'gfw'")


def downgrade() -> None:
    op.execute("UPDATE vessel_position SET source = 'gfw' WHERE source = 'gfw_fishing'")
