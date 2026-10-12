"""add_dup_candidates

Revision ID: c7d1e2f3a4b5
Revises: 545d050ea843
Create Date: 2026-10-12 19:40:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c7d1e2f3a4b5'
down_revision: Union[str, Sequence[str], None] = '545d050ea843'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table('dup_candidates'):
        return  # created by init_db's create_all on an earlier start
    op.create_table(
        'dup_candidates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('temp_path', sa.String(length=2000), nullable=False),
        sa.Column('shelf_path', sa.String(length=2000), nullable=False),
        sa.Column('mb_temp', sa.Float(), nullable=False),
        sa.Column('files_temp', sa.Integer(), nullable=False),
        sa.Column('mb_shelf', sa.Float(), nullable=False),
        sa.Column('files_shelf', sa.Integer(), nullable=False),
        sa.Column('minutes', sa.Float(), nullable=True),
        sa.Column('verdict', sa.String(length=20), nullable=False),
        sa.Column('proposal', sa.String(length=20), nullable=True),
        sa.Column('decision', sa.String(length=20), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('result', sa.String(length=500), nullable=True),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
        sa.Column('executed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('temp_path'),
    )
    op.create_index('ix_dup_candidates_status', 'dup_candidates', ['status'])


def downgrade() -> None:
    op.drop_index('ix_dup_candidates_status', table_name='dup_candidates')
    op.drop_table('dup_candidates')
