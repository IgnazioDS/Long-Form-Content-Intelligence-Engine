"""add source error column

Revision ID: 0002_add_source_error
Revises: 0001_init
Create Date: 2024-01-02 00:00:00

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002_add_source_error"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "error")
