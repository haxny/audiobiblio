"""crawl_targets paired_url — dual-source pairing

Revision ID: 545d050ea843
Revises: 2ad49dcfcbb6
Create Date: 2026-07-23 17:17:45.905696

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '545d050ea843'
down_revision: Union[str, Sequence[str], None] = '2ad49dcfcbb6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "crawl_targets",
        sa.Column("paired_url", sa.String(length=1000), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("crawl_targets", "paired_url")
