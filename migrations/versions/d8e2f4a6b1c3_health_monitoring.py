"""health monitoring: crawl_runs, health_snapshots, episodes.playable_till

Revision ID: d8e2f4a6b1c3
Revises: c7d1e2f3a4b5
Create Date: 2026-11-07 18:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd8e2f4a6b1c3'
down_revision: Union[str, Sequence[str], None] = 'c7d1e2f3a4b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())

    # ALTER TABLE ADD COLUMN is O(1) in SQLite; the index scans episodes once.
    if 'playable_till' not in {c['name'] for c in insp.get_columns('episodes')}:
        op.add_column('episodes', sa.Column('playable_till', sa.DateTime(), nullable=True))
    if 'ix_episodes_playable_till' not in {i['name'] for i in insp.get_indexes('episodes')}:
        op.create_index('ix_episodes_playable_till', 'episodes', ['playable_till'])

    # init_db's create_all may have created the new tables on an earlier start
    if not insp.has_table('crawl_runs'):
        op.create_table(
            'crawl_runs',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('target_id', sa.Integer(), nullable=True),
            sa.Column('target_url', sa.String(length=1000), nullable=True),
            sa.Column('path', sa.String(length=30), nullable=False),
            sa.Column('started_at', sa.DateTime(), nullable=False),
            sa.Column('finished_at', sa.DateTime(), nullable=False),
            sa.Column('items_seen', sa.Integer(), nullable=True),
            sa.Column('new_episodes', sa.Integer(), nullable=False),
            sa.Column('jobs_queued', sa.Integer(), nullable=False),
            sa.Column('error', sa.String(length=1000), nullable=True),
            sa.ForeignKeyConstraint(['target_id'], ['crawl_targets.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_crawl_runs_finished_at', 'crawl_runs', ['finished_at'])
        op.create_index('ix_crawl_runs_target_finished', 'crawl_runs',
                        ['target_id', 'finished_at'])

    if not insp.has_table('health_snapshots'):
        op.create_table(
            'health_snapshots',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('day', sa.String(length=10), nullable=False),
            sa.Column('detector', sa.String(length=40), nullable=False),
            sa.Column('count', sa.Integer(), nullable=False),
            sa.Column('severity', sa.String(length=10), nullable=True),
            sa.Column('keys', sa.JSON(), nullable=True),
            sa.Column('details', sa.JSON(), nullable=True),
            sa.Column('error', sa.String(length=1000), nullable=True),
            sa.Column('notified', sa.Boolean(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('day', 'detector', name='uq_health_snapshot_day_detector'),
        )
        op.create_index('ix_health_snapshots_day', 'health_snapshots', ['day'])


def downgrade() -> None:
    op.drop_index('ix_health_snapshots_day', table_name='health_snapshots')
    op.drop_table('health_snapshots')
    op.drop_index('ix_crawl_runs_target_finished', table_name='crawl_runs')
    op.drop_index('ix_crawl_runs_finished_at', table_name='crawl_runs')
    op.drop_table('crawl_runs')
    op.drop_index('ix_episodes_playable_till', table_name='episodes')
    op.drop_column('episodes', 'playable_till')
