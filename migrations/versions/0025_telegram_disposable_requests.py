"""allow standalone Telegram disposable requests

Revision ID: 0025
Revises: 0024
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    with op.batch_alter_table("disposable_requests") as batch:
        batch.alter_column("proposal_id", existing_type=sa.Integer(), nullable=True)
    op.add_column("disposable_requests", sa.Column("rejected", sa.Boolean(), nullable=False, server_default="0"))
    op.add_column("disposable_requests", sa.Column("requester_cca", sa.String(length=100), nullable=True))
    op.add_column("disposable_requests", sa.Column("description", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("disposable_requests", "description")
    op.drop_column("disposable_requests", "requester_cca")
    op.drop_column("disposable_requests", "rejected")
    with op.batch_alter_table("disposable_requests") as batch:
        batch.alter_column("proposal_id", existing_type=sa.Integer(), nullable=False)
