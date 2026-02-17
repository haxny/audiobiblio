"""add availability tracking, crawl targets

Revision ID: 8671e505028d
Revises: 59fa8df761e0
Create Date: 2026-02-17 14:08:08.144952

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8671e505028d'
down_revision: Union[str, Sequence[str], None] = '59fa8df761e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    conn = op.get_bind()
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _has_table(table: str) -> bool:
    conn = op.get_bind()
    rows = conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchall()
    return len(rows) > 0


def _add_column_if_missing(table: str, col_name: str, col_sql: str):
    """Add a column via raw DDL if it doesn't exist (avoids batch mode issues)."""
    if not _has_column(table, col_name):
        op.get_bind().exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col_sql}")


def upgrade() -> None:
    """Upgrade schema."""
    # New tables
    if not _has_table('crawl_targets'):
        op.create_table('crawl_targets',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('url', sa.String(length=1000), nullable=False),
            sa.Column('kind', sa.Enum('STATION', 'PROGRAM', 'SERIES', name='crawltargetkind'), nullable=False),
            sa.Column('name', sa.String(length=300), nullable=True),
            sa.Column('active', sa.Boolean(), nullable=False),
            sa.Column('interval_hours', sa.Integer(), nullable=False),
            sa.Column('last_crawled_at', sa.DateTime(), nullable=True),
            sa.Column('next_crawl_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('url')
        )
        with op.batch_alter_table('crawl_targets', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_crawl_targets_active'), ['active'], unique=False)
            batch_op.create_index(batch_op.f('ix_crawl_targets_kind'), ['kind'], unique=False)
            batch_op.create_index(batch_op.f('ix_crawl_targets_next_crawl_at'), ['next_crawl_at'], unique=False)

    if not _has_table('availability_log'):
        op.create_table('availability_log',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('episode_id', sa.Integer(), nullable=False),
            sa.Column('checked_at', sa.DateTime(), nullable=False),
            sa.Column('was_available', sa.Boolean(), nullable=False),
            sa.Column('http_status', sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(['episode_id'], ['episodes.id'], ),
            sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('availability_log', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_availability_log_checked_at'), ['checked_at'], unique=False)
            batch_op.create_index(batch_op.f('ix_availability_log_episode_id'), ['episode_id'], unique=False)

    # Episodes — use raw ALTER TABLE to avoid batch mode table recreation issues
    _add_column_if_missing('episodes', 'availability_status', "availability_status VARCHAR(11)")
    _add_column_if_missing('episodes', 'first_seen_at', "first_seen_at DATETIME")
    _add_column_if_missing('episodes', 'last_seen_at', "last_seen_at DATETIME")
    _add_column_if_missing('episodes', 'last_checked_at', "last_checked_at DATETIME")
    _add_column_if_missing('episodes', 'auto_download', "auto_download BOOLEAN NOT NULL DEFAULT 0")
    _add_column_if_missing('episodes', 'priority', "priority INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing('episodes', 'discovery_source', "discovery_source VARCHAR(200)")

    # Programs
    _add_column_if_missing('programs', 'auto_crawl', "auto_crawl BOOLEAN NOT NULL DEFAULT 0")
    _add_column_if_missing('programs', 'crawl_interval_hours', "crawl_interval_hours INTEGER")
    _add_column_if_missing('programs', 'last_crawled_at', "last_crawled_at DATETIME")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('programs', schema=None) as batch_op:
        batch_op.drop_column('last_crawled_at')
        batch_op.drop_column('crawl_interval_hours')
        batch_op.drop_column('auto_crawl')

    with op.batch_alter_table('episodes', schema=None) as batch_op:
        batch_op.drop_column('discovery_source')
        batch_op.drop_column('priority')
        batch_op.drop_column('auto_download')
        batch_op.drop_column('last_checked_at')
        batch_op.drop_column('last_seen_at')
        batch_op.drop_column('first_seen_at')
        batch_op.drop_column('availability_status')

    with op.batch_alter_table('availability_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_availability_log_episode_id'))
        batch_op.drop_index(batch_op.f('ix_availability_log_checked_at'))
    op.drop_table('availability_log')

    with op.batch_alter_table('crawl_targets', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_crawl_targets_next_crawl_at'))
        batch_op.drop_index(batch_op.f('ix_crawl_targets_kind'))
        batch_op.drop_index(batch_op.f('ix_crawl_targets_active'))
    op.drop_table('crawl_targets')
