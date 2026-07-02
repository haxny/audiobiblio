"""add metadata_values

Revision ID: 059c3c38a79a
Revises: 584f34ff6085
Create Date: 2026-07-02 22:09:00.822935

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '059c3c38a79a'
down_revision: Union[str, Sequence[str], None] = '584f34ff6085'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # NOTE: autogenerate also proposed:
    #   - op.drop_table('_alembic_tmp_episodes')  — leftover temp table from prior batch migration; safe to ignore
    #   - alter_column download_jobs.status VARCHAR->Enum  — SQLite type drift; no actual schema change needed
    #   - create_index ix_episodes_availability_status  — drift between live DB and model; not part of this task
    # All three removed; this migration creates exactly metadata_values.

    op.create_table('metadata_values',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('entity_type', sa.String(length=20), nullable=False),
    sa.Column('entity_id', sa.Integer(), nullable=False),
    sa.Column('field', sa.String(length=50), nullable=False),
    sa.Column('value', sa.String(length=4000), nullable=True),
    sa.Column('origin', sa.Enum('SCRAPED', 'FILE', 'ENRICHED', 'MANUAL', name='fieldorigin'), nullable=False),
    sa.Column('source', sa.String(length=100), nullable=False),
    sa.Column('observed_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('entity_type', 'entity_id', 'field', 'origin', 'source', name='uq_metadata_value_provenance')
    )
    with op.batch_alter_table('metadata_values', schema=None) as batch_op:
        batch_op.create_index('ix_metadata_values_entity_field', ['entity_type', 'entity_id', 'field'], unique=False)
        batch_op.create_index(batch_op.f('ix_metadata_values_entity_id'), ['entity_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_metadata_values_entity_type'), ['entity_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_metadata_values_origin'), ['origin'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('metadata_values', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_metadata_values_origin'))
        batch_op.drop_index(batch_op.f('ix_metadata_values_entity_type'))
        batch_op.drop_index(batch_op.f('ix_metadata_values_entity_id'))
        batch_op.drop_index('ix_metadata_values_entity_field')

    op.drop_table('metadata_values')
