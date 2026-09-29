"""add titles to standalone disposable requests

Revision ID: 0026
Revises: 0025
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column("disposable_requests", sa.Column("request_title", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("disposable_requests", "request_title")
