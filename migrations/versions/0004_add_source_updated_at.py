"""add source updated_at

Revision ID: 0004_add_source_updated_at
Revises: 0003_add_chunk_char_offsets
Create Date: 2025-02-01 00:00:00

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0004_add_source_updated_at"
down_revision = "0003_add_chunk_char_offsets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("sources", "updated_at")
