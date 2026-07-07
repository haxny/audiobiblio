"""add_import_findings

Revision ID: eb491e6892f5
Revises: 8e3696d70603
Create Date: 2026-07-07 11:32:30.903934

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'eb491e6892f5'
down_revision: Union[str, Sequence[str], None] = '8e3696d70603'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Stripped autogenerate noise (SQLite drift artifacts shared by earlier migrations):
    #   - drop of _alembic_tmp_episodes (leftover batch-alter temp table — not in real DB)
    #   - alter_column on download_jobs.status (VARCHAR→Enum type drift, no real change)
    #   - create_index ix_episodes_availability_status (index already present in DB)

    op.create_table(
        'import_findings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('scan_id', sa.String(length=36), nullable=False),
        sa.Column('path', sa.String(length=2000), nullable=False),
        sa.Column(
            'bucket',
            # SAEnum with a Python Enum class stores the member NAMES (uppercase),
            # not the member values.  Empirically verified: INSERT of
            # ImportBucket.MATCHED stores "MATCHED" in SQLite, not "matched".
            # The uppercase strings here are therefore correct.
            sa.Enum('MATCHED', 'DUPLICATE', 'UNKNOWN', 'CONFLICT', name='importbucket'),
            nullable=False,
        ),
        sa.Column('episode_id', sa.Integer(), nullable=True),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['episode_id'], ['episodes.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('path'),
    )
    with op.batch_alter_table('import_findings', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_import_findings_bucket'), ['bucket'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_import_findings_episode_id'), ['episode_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_import_findings_scan_id'), ['scan_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_import_findings_status'), ['status'], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('import_findings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_import_findings_status'))
        batch_op.drop_index(batch_op.f('ix_import_findings_scan_id'))
        batch_op.drop_index(batch_op.f('ix_import_findings_episode_id'))
        batch_op.drop_index(batch_op.f('ix_import_findings_bucket'))

    op.drop_table('import_findings')
