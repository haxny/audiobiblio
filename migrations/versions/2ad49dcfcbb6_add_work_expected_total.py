"""add_work_expected_total

Revision ID: 2ad49dcfcbb6
Revises: eb491e6892f5
Create Date: 2026-07-08 00:30:59.827407

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2ad49dcfcbb6'
down_revision: Union[str, Sequence[str], None] = 'eb491e6892f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add expected_total and expected_source to the works table.

    Stripped autogenerate noise:
      - drop of _alembic_tmp_episodes (Alembic leftover temp table)
      - alter_column on download_jobs.status (VARCHAR→Enum drift, no real change)
      - create_index ix_episodes_availability_status (already present in DB)
    """
    with op.batch_alter_table('works', schema=None) as batch_op:
        batch_op.add_column(sa.Column('expected_total', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('expected_source', sa.String(length=50), nullable=True))


def downgrade() -> None:
    """Remove expected_total and expected_source from the works table."""
    with op.batch_alter_table('works', schema=None) as batch_op:
        batch_op.drop_column('expected_source')
        batch_op.drop_column('expected_total')
