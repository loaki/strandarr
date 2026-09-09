"""initial schema"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '65a4cc2e440a'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('current_observation',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('lat', sa.Float(), nullable=False),
    sa.Column('lon', sa.Float(), nullable=False),
    sa.Column('speed', sa.Float(), nullable=False),
    sa.Column('direction', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_current_observation_dedup', 'current_observation', ['recorded_at', 'lat', 'lon'], unique=True)
    op.create_index(op.f('ix_current_observation_recorded_at'), 'current_observation', ['recorded_at'], unique=False)
    op.create_table('job',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('kind', sa.String(length=64), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.Enum('pending', 'running', 'done', 'failed', name='jobstatus'), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_job_kind'), 'job', ['kind'], unique=False)
    op.create_index(op.f('ix_job_status'), 'job', ['status'], unique=False)
    op.create_table('stranding',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('external_id', sa.String(length=64), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('lat', sa.Float(), nullable=False),
    sa.Column('lon', sa.Float(), nullable=False),
    sa.Column('species', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_stranding_dedup', 'stranding', ['source', 'external_id'], unique=True)
    op.create_index(op.f('ix_stranding_recorded_at'), 'stranding', ['recorded_at'], unique=False)
    op.create_table('vessel_position',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('mmsi', sa.String(length=16), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('lat', sa.Float(), nullable=False),
    sa.Column('lon', sa.Float(), nullable=False),
    sa.Column('speed', sa.Float(), nullable=True),
    sa.Column('course', sa.Float(), nullable=True),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_vessel_position_dedup', 'vessel_position', ['mmsi', 'recorded_at', 'source'], unique=True)
    op.create_index(op.f('ix_vessel_position_mmsi'), 'vessel_position', ['mmsi'], unique=False)
    op.create_index(op.f('ix_vessel_position_recorded_at'), 'vessel_position', ['recorded_at'], unique=False)
    op.create_table('wind_observation',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('lat', sa.Float(), nullable=False),
    sa.Column('lon', sa.Float(), nullable=False),
    sa.Column('speed', sa.Float(), nullable=False),
    sa.Column('direction', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_wind_observation_dedup', 'wind_observation', ['recorded_at', 'lat', 'lon'], unique=True)
    op.create_index(op.f('ix_wind_observation_recorded_at'), 'wind_observation', ['recorded_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_wind_observation_recorded_at'), table_name='wind_observation')
    op.drop_index('ix_wind_observation_dedup', table_name='wind_observation')
    op.drop_table('wind_observation')
    op.drop_index(op.f('ix_vessel_position_recorded_at'), table_name='vessel_position')
    op.drop_index(op.f('ix_vessel_position_mmsi'), table_name='vessel_position')
    op.drop_index('ix_vessel_position_dedup', table_name='vessel_position')
    op.drop_table('vessel_position')
    op.drop_index(op.f('ix_stranding_recorded_at'), table_name='stranding')
    op.drop_index('ix_stranding_dedup', table_name='stranding')
    op.drop_table('stranding')
    op.drop_index(op.f('ix_job_status'), table_name='job')
    op.drop_index(op.f('ix_job_kind'), table_name='job')
    op.drop_table('job')
    op.drop_index(op.f('ix_current_observation_recorded_at'), table_name='current_observation')
    op.drop_index('ix_current_observation_dedup', table_name='current_observation')
    op.drop_table('current_observation')
