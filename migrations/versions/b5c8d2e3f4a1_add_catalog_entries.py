"""add catalog_entries table

Revision ID: b5c8d2e3f4a1
Revises: 9ea1585aea8f
Create Date: 2026-02-18 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5c8d2e3f4a1'
down_revision: Union[str, Sequence[str], None] = '9ea1585aea8f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'catalog_entries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('program_id', sa.Integer(), sa.ForeignKey('programs.id'), nullable=False),
        sa.Column('episode_number', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(600), nullable=False),
        sa.Column('author', sa.String(500), nullable=True),
        sa.Column('year', sa.Integer(), nullable=True),
        sa.Column('air_date', sa.DateTime(), nullable=True),
        sa.Column('source', sa.String(100), nullable=False),
        sa.Column('source_url', sa.String(1000), nullable=True),
        sa.Column('episode_id', sa.Integer(), sa.ForeignKey('episodes.id'), nullable=True),
        sa.Column('local_file', sa.String(2000), nullable=True),
        sa.Column('status', sa.String(50), nullable=False, server_default='missing'),
        sa.Column('notes', sa.String(4000), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('program_id', 'episode_number', 'title', name='uq_catalog_entry'),
    )
    op.create_index('ix_catalog_entries_program_id', 'catalog_entries', ['program_id'])
    op.create_index('ix_catalog_entries_episode_id', 'catalog_entries', ['episode_id'])
    op.create_index('ix_catalog_entries_status', 'catalog_entries', ['status'])


def downgrade() -> None:
    op.drop_table('catalog_entries')
