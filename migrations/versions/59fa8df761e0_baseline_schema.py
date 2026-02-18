"""baseline schema

Revision ID: 59fa8df761e0
Revises:
Create Date: 2026-02-17 14:06:12.057007

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '59fa8df761e0'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table: str) -> bool:
    conn = op.get_bind()
    rows = conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchall()
    return len(rows) > 0


def upgrade() -> None:
    """Create core tables (idempotent — skips if tables exist)."""

    if not _has_table('stations'):
        op.create_table('stations',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('code', sa.String(16), unique=True, nullable=False),
            sa.Column('name', sa.String(200), nullable=False),
            sa.Column('website', sa.String(500), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False,
                       server_default=sa.text("(datetime('now'))")),
        )
        op.create_index('ix_stations_code', 'stations', ['code'], unique=True)

    if not _has_table('programs'):
        op.create_table('programs',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('station_id', sa.Integer(), sa.ForeignKey('stations.id'), nullable=False),
            sa.Column('ext_id', sa.String(200), nullable=True),
            sa.Column('name', sa.String(300), nullable=False),
            sa.Column('url', sa.String(1000), nullable=True),
            sa.Column('description', sa.String(4000), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False,
                       server_default=sa.text("(datetime('now'))")),
            sa.UniqueConstraint('station_id', 'name', name='uq_program_per_station_name'),
        )
        op.create_index('ix_programs_station_id', 'programs', ['station_id'])
        op.create_index('ix_programs_ext_id', 'programs', ['ext_id'])

    if not _has_table('series'):
        op.create_table('series',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('program_id', sa.Integer(), sa.ForeignKey('programs.id'), nullable=False),
            sa.Column('ext_id', sa.String(200), nullable=True),
            sa.Column('name', sa.String(400), nullable=False),
            sa.Column('url', sa.String(1000), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False,
                       server_default=sa.text("(datetime('now'))")),
            sa.UniqueConstraint('program_id', 'name', name='uq_series_per_program'),
        )
        op.create_index('ix_series_program_id', 'series', ['program_id'])
        op.create_index('ix_series_ext_id', 'series', ['ext_id'])
        op.create_index('ix_series_name', 'series', ['name'])

    if not _has_table('works'):
        op.create_table('works',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('series_id', sa.Integer(), sa.ForeignKey('series.id'), nullable=False),
            sa.Column('title', sa.String(500), nullable=False),
            sa.Column('author', sa.String(500), nullable=True),
            sa.Column('year', sa.Integer(), nullable=True),
            sa.Column('asin', sa.String(50), nullable=True),
            sa.Column('extra', sa.JSON(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False,
                       server_default=sa.text("(datetime('now'))")),
            sa.UniqueConstraint('series_id', 'title', name='uq_work_per_series'),
        )
        op.create_index('ix_works_series_id', 'works', ['series_id'])
        op.create_index('ix_works_title', 'works', ['title'])

    if not _has_table('episodes'):
        op.create_table('episodes',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('work_id', sa.Integer(), sa.ForeignKey('works.id'), nullable=False),
            sa.Column('ext_id', sa.String(200), unique=True, nullable=True),
            sa.Column('title', sa.String(600), nullable=False),
            sa.Column('episode_number', sa.Integer(), nullable=True),
            sa.Column('published_at', sa.DateTime(), nullable=True),
            sa.Column('url', sa.String(1000), nullable=True),
            sa.Column('duration_ms', sa.Integer(), nullable=True),
            sa.Column('summary', sa.String(8000), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False,
                       server_default=sa.text("(datetime('now'))")),
            sa.Column('updated_at', sa.DateTime(), nullable=False,
                       server_default=sa.text("(datetime('now'))")),
        )
        op.create_index('ix_episodes_work_id', 'episodes', ['work_id'])
        op.create_index('ix_episodes_episode_number', 'episodes', ['episode_number'])
        op.create_index('ix_episodes_published_at', 'episodes', ['published_at'])
        op.create_index('ix_episode_work_num', 'episodes', ['work_id', 'episode_number'])

    if not _has_table('assets'):
        op.create_table('assets',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('episode_id', sa.Integer(), sa.ForeignKey('episodes.id'), nullable=False),
            sa.Column('type', sa.String(20), nullable=False),
            sa.Column('status', sa.String(20), nullable=False, server_default='missing'),
            sa.Column('source_url', sa.String(1000), nullable=True),
            sa.Column('file_path', sa.String(2000), nullable=True),
            sa.Column('size_bytes', sa.BigInteger(), nullable=True),
            sa.Column('checksum', sa.String(128), nullable=True),
            sa.Column('codec', sa.String(80), nullable=True),
            sa.Column('container', sa.String(40), nullable=True),
            sa.Column('bitrate', sa.Integer(), nullable=True),
            sa.Column('channels', sa.Integer(), nullable=True),
            sa.Column('sample_rate', sa.Integer(), nullable=True),
            sa.Column('extra', sa.JSON(), nullable=True),
            sa.Column('first_seen_at', sa.DateTime(), nullable=False,
                       server_default=sa.text("(datetime('now'))")),
            sa.Column('updated_at', sa.DateTime(), nullable=False,
                       server_default=sa.text("(datetime('now'))")),
            sa.UniqueConstraint('episode_id', 'type', name='uq_asset_per_episode_type'),
        )
        op.create_index('ix_assets_episode_id', 'assets', ['episode_id'])
        op.create_index('ix_assets_type', 'assets', ['type'])
        op.create_index('ix_assets_status', 'assets', ['status'])

    if not _has_table('download_jobs'):
        op.create_table('download_jobs',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('episode_id', sa.Integer(), sa.ForeignKey('episodes.id'), nullable=False),
            sa.Column('asset_type', sa.String(20), nullable=False),
            sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
            sa.Column('reason', sa.String(500), nullable=True),
            sa.Column('command', sa.String(2000), nullable=True),
            sa.Column('error', sa.String(4000), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False,
                       server_default=sa.text("(datetime('now'))")),
            sa.Column('started_at', sa.DateTime(), nullable=True),
            sa.Column('finished_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['episode_id'], ['episodes.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_download_jobs_episode_id', 'download_jobs', ['episode_id'])
        op.create_index('ix_download_jobs_status', 'download_jobs', ['status'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('download_jobs')
    op.drop_table('assets')
    op.drop_table('episodes')
    op.drop_table('works')
    op.drop_table('series')
    op.drop_table('programs')
    op.drop_table('stations')
