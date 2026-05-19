"""Add cdwifi_downloads table

Revision ID: fb8a20de49ae
Revises: b5c8d2e3f4a1
Create Date: 2026-03-18 20:15:11.755662

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fb8a20de49ae'
down_revision: Union[str, Sequence[str], None] = 'b5c8d2e3f4a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('cdwifi_downloads',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=50), nullable=False),
    sa.Column('source_id', sa.String(length=200), nullable=False),
    sa.Column('title', sa.String(length=600), nullable=False),
    sa.Column('author', sa.String(length=500), nullable=True),
    sa.Column('track_number', sa.Integer(), nullable=True),
    sa.Column('track_title', sa.String(length=600), nullable=True),
    sa.Column('source_url', sa.String(length=2000), nullable=False),
    sa.Column('file_path', sa.String(length=2000), nullable=True),
    sa.Column('size_bytes', sa.BigInteger(), nullable=True),
    sa.Column('status', sa.String(length=50), nullable=False),
    sa.Column('extra', sa.JSON(), nullable=True),
    sa.Column('downloaded_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source', 'source_id', 'source_url', name='uq_cdwifi_download')
    )
    with op.batch_alter_table('cdwifi_downloads', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_cdwifi_downloads_source'), ['source'], unique=False)
        batch_op.create_index(batch_op.f('ix_cdwifi_downloads_status'), ['status'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('cdwifi_downloads', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_cdwifi_downloads_status'))
        batch_op.drop_index(batch_op.f('ix_cdwifi_downloads_source'))

    op.drop_table('cdwifi_downloads')
