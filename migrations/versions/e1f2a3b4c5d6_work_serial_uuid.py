"""works.serial_uuid: one rAPI serial = one work, whatever path ingests it

Revision ID: e1f2a3b4c5d6
Revises: d8e9f0a1b2c3
Create Date: 2026-11-17 10:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'd8e9f0a1b2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    return any(c['name'] == column for c in sa.inspect(op.get_bind()).get_columns(table))


def upgrade() -> None:
    if not _has_column('works', 'serial_uuid'):
        with op.batch_alter_table('works', schema=None) as batch_op:
            batch_op.add_column(sa.Column('serial_uuid', sa.String(length=40), nullable=True))
            batch_op.create_index('ix_works_serial_uuid', ['serial_uuid'], unique=False)


def downgrade() -> None:
    if _has_column('works', 'serial_uuid'):
        with op.batch_alter_table('works', schema=None) as batch_op:
            batch_op.drop_index('ix_works_serial_uuid')
            batch_op.drop_column('serial_uuid')
