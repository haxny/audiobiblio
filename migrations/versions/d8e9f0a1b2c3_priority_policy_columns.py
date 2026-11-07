"""priority policy columns: crawl_targets.content_class/priority_base,
episodes.playable_till

Revision ID: d8e9f0a1b2c3
Revises: c7d1e2f3a4b5
Create Date: 2026-11-07 18:00:00

Each column is added only when missing: episodes.playable_till may also be
added by a parallel branch (expiry alerts) — whichever migration runs first
creates it, the other is a no-op.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd8e9f0a1b2c3'
down_revision: Union[str, Sequence[str], None] = 'c7d1e2f3a4b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    ('crawl_targets', sa.Column('content_class', sa.String(length=20), nullable=True)),
    ('crawl_targets', sa.Column('priority_base', sa.Integer(), nullable=True)),
    ('episodes', sa.Column('playable_till', sa.DateTime(), nullable=True)),
)


def _has_column(table: str, column: str) -> bool:
    return any(c['name'] == column for c in sa.inspect(op.get_bind()).get_columns(table))


def upgrade() -> None:
    for table, column in _COLUMNS:
        if not _has_column(table, column.name):
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.add_column(column)


def downgrade() -> None:
    # playable_till is left in place: the parallel expiry-alert branch may own it
    for table, column in _COLUMNS[:2]:
        if _has_column(table, column.name):
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.drop_column(column.name)
