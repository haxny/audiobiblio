"""add_upgrade_candidates

Revision ID: 8e3696d70603
Revises: 20f737dc3b98
Create Date: 2026-07-03 23:04:42.988575

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8e3696d70603'
down_revision: Union[str, Sequence[str], None] = '20f737dc3b98'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Stripped autogenerate noise (SQLite drift artifacts shared by earlier migrations):
    #   - drop of _alembic_tmp_episodes (leftover batch-alter temp table — not in real DB)
    #   - alter_column on download_jobs.status (VARCHAR→Enum type drift, no real change)
    #   - create_index ix_episodes_availability_status (index already present in DB)

    op.create_table(
        'upgrade_candidates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('episode_id', sa.Integer(), nullable=False),
        sa.Column('candidate_url', sa.String(length=1000), nullable=False),
        sa.Column('candidate_duration_ms', sa.Integer(), nullable=True),
        sa.Column('owned_duration_ms', sa.Integer(), nullable=True),
        sa.Column('owned_asset_id', sa.Integer(), nullable=True),
        sa.Column(
            'status',
            sa.Enum(
                'PENDING_REVIEW', 'STAGED', 'REPLACED', 'KEPT_OLD', 'DISMISSED',
                name='upgradestatus',
            ),
            nullable=False,
        ),
        sa.Column('staged_path', sa.String(length=2000), nullable=True),
        sa.Column('note', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['episode_id'], ['episodes.id']),
        sa.ForeignKeyConstraint(['owned_asset_id'], ['assets.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('episode_id', 'candidate_url', name='uq_upgrade_candidate'),
    )
    with op.batch_alter_table('upgrade_candidates', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_upgrade_candidates_episode_id'), ['episode_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_upgrade_candidates_status'), ['status'], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('upgrade_candidates', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_upgrade_candidates_status'))
        batch_op.drop_index(batch_op.f('ix_upgrade_candidates_episode_id'))

    op.drop_table('upgrade_candidates')
