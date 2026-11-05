"""track deleted proposal comments without removing reply targets

Revision ID: 0031
Revises: 0030
"""

from alembic import op
import sqlalchemy as sa


revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("proposal_comments", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("proposal_comments", "deleted_at")
