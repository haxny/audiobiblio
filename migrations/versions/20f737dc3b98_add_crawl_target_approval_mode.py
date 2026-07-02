"""add crawl_target approval_mode

Revision ID: 20f737dc3b98
Revises: 059c3c38a79a
Create Date: 2026-07-02 23:28:20.771438

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20f737dc3b98'
down_revision: Union[str, Sequence[str], None] = '059c3c38a79a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Stripped autogenerate noise (SQLite drift artifacts shared by earlier migrations):
    #   - drop/create of _alembic_tmp_episodes (leftover batch-alter temp table)
    #   - alter_column on download_jobs.status (VARCHAR->Enum type drift, no real change)
    #   - create_index ix_episodes_availability_status (index already present in DB)

    with op.batch_alter_table('crawl_targets', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'approval_mode',
            sa.Enum('AUTO', 'REVIEW', name='approvalmode'),
            server_default='REVIEW',
            nullable=False,
        ))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('crawl_targets', schema=None) as batch_op:
        batch_op.drop_column('approval_mode')
