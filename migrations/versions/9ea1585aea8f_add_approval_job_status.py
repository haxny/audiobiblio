"""add approval job status

Revision ID: 9ea1585aea8f
Revises: a3b7c9d1e2f4
Create Date: 2026-02-18 20:57:09.352506

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9ea1585aea8f'
down_revision: Union[str, Sequence[str], None] = 'a3b7c9d1e2f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add 'approval' value to JobStatus enum.

    SQLite stores enum values as plain VARCHAR strings, so no schema change
    is needed — the new value is accepted automatically.
    """
    pass


def downgrade() -> None:
    """Remove 'approval' jobs by converting them back to 'pending'."""
    op.execute("UPDATE download_jobs SET status = 'pending' WHERE status = 'approval'")
