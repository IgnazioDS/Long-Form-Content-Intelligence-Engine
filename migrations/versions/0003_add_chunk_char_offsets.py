"""add chunk char offsets

Revision ID: 0003_add_chunk_char_offsets
Revises: 0002_add_source_error
Create Date: 2025-12-28 00:00:00

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0003_add_chunk_char_offsets"
down_revision = "0002_add_source_error"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chunks", sa.Column("char_start", sa.Integer(), nullable=True))
    op.add_column("chunks", sa.Column("char_end", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("chunks", "char_end")
    op.drop_column("chunks", "char_start")
