"""add_live_recordings

Broadcast-only slots (D-dur thematic weekend blocks, unlinked multi-part
items) recorded from the live stream by acquire/live.

Revision ID: d4e5f6a7b8c9
Revises: c7d1e2f3a4b5
Create Date: 2026-11-07 16:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c7d1e2f3a4b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STATUS = sa.Enum('PLANNED', 'BACKUP', 'RECORDING', 'FINALIZING', 'COMPLETE', 'PARTIAL',
                  'FAILED', 'CANCELLED', 'SKIPPED', name='livestatus')


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table('live_recordings'):
        return  # created by init_db's create_all on an earlier start
    op.create_table(
        'live_recordings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('station_id', sa.String(length=64), nullable=False),
        sa.Column('station_code', sa.String(length=16), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('dedupe_key', sa.String(length=400), nullable=False),
        sa.Column('title', sa.String(length=600), nullable=False),
        sa.Column('series_title', sa.String(length=600), nullable=False),
        sa.Column('program_name', sa.String(length=300), nullable=True),
        sa.Column('part', sa.Integer(), nullable=True),
        sa.Column('total_parts', sa.Integer(), nullable=True),
        sa.Column('schedule_start', sa.DateTime(), nullable=False),
        sa.Column('schedule_end', sa.DateTime(), nullable=False),
        sa.Column('window_start', sa.DateTime(), nullable=False),
        sa.Column('window_end', sa.DateTime(), nullable=False),
        sa.Column('stream_url', sa.String(length=1000), nullable=False),
        sa.Column('items', sa.JSON(), nullable=True),
        sa.Column('description', sa.String(length=8000), nullable=True),
        sa.Column('status', _STATUS, nullable=False),
        sa.Column('runs', sa.JSON(), nullable=True),
        sa.Column('work_dir', sa.String(length=2000), nullable=True),
        sa.Column('file_path', sa.String(length=2000), nullable=True),
        sa.Column('duration_s', sa.Float(), nullable=True),
        sa.Column('coverage', sa.Float(), nullable=True),
        sa.Column('episode_id', sa.Integer(), nullable=True),
        sa.Column('error', sa.String(length=2000), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['episode_id'], ['episodes.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('dedupe_key', 'schedule_start', name='uq_live_recording_airing'),
    )
    op.create_index('ix_live_recordings_station_id', 'live_recordings', ['station_id'])
    op.create_index('ix_live_recordings_dedupe_key', 'live_recordings', ['dedupe_key'])
    op.create_index('ix_live_recordings_window_start', 'live_recordings', ['window_start'])
    op.create_index('ix_live_recordings_status', 'live_recordings', ['status'])
    op.create_index('ix_live_recordings_episode_id', 'live_recordings', ['episode_id'])


def downgrade() -> None:
    for ix in ('episode_id', 'status', 'window_start', 'dedupe_key', 'station_id'):
        op.drop_index(f'ix_live_recordings_{ix}', table_name='live_recordings')
    op.drop_table('live_recordings')
