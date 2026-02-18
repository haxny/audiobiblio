"""episode_alias table, program genre/channel_label columns

Revision ID: a3b7c9d1e2f4
Revises: 8671e505028d
Create Date: 2026-02-18 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3b7c9d1e2f4'
down_revision: Union[str, Sequence[str], None] = '8671e505028d'
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
    if not _has_column(table, col_name):
        op.get_bind().exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col_sql}")


def upgrade() -> None:
    # EpisodeAlias table
    if not _has_table('episode_aliases'):
        op.create_table('episode_aliases',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('episode_id', sa.Integer(), nullable=False),
            sa.Column('url', sa.String(length=1000), nullable=True),
            sa.Column('ext_id', sa.String(length=200), nullable=True),
            sa.Column('air_date', sa.DateTime(), nullable=True),
            sa.Column('discovery_source', sa.String(length=200), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['episode_id'], ['episodes.id']),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('episode_id', 'url', name='uq_alias_episode_url'),
        )
        with op.batch_alter_table('episode_aliases', schema=None) as batch_op:
            batch_op.create_index('ix_episode_aliases_episode_id', ['episode_id'], unique=False)
            batch_op.create_index('ix_episode_aliases_url', ['url'], unique=False)
            batch_op.create_index('ix_episode_aliases_ext_id', ['ext_id'], unique=False)

    # Program columns
    _add_column_if_missing('programs', 'genre', "genre VARCHAR(500)")
    _add_column_if_missing('programs', 'channel_label', "channel_label VARCHAR(100)")


def downgrade() -> None:
    with op.batch_alter_table('programs', schema=None) as batch_op:
        batch_op.drop_column('channel_label')
        batch_op.drop_column('genre')

    with op.batch_alter_table('episode_aliases', schema=None) as batch_op:
        batch_op.drop_index('ix_episode_aliases_ext_id')
        batch_op.drop_index('ix_episode_aliases_url')
        batch_op.drop_index('ix_episode_aliases_episode_id')
    op.drop_table('episode_aliases')
