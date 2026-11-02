"""borrow requests for the shared tables and cambro

Revision ID: 0028
Revises: 0027
"""

from alembic import op
import sqlalchemy as sa


revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "borrow_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("committee_id", sa.Integer(), sa.ForeignKey("committees.id"), nullable=False),
        sa.Column("requested_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("activity_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tables", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cambro", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("borrow_date", sa.Date(), nullable=False),
        sa.Column("return_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("return_proof_file_id", sa.String(255), nullable=True),
        sa.Column("return_proof_content_type", sa.String(100), nullable=True),
        sa.Column("return_proof_data", sa.LargeBinary(), nullable=True),
        sa.Column("return_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("returned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reminder_kind", sa.String(20), nullable=True),
        sa.Column("last_reminded_on", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_borrow_requests_status", "borrow_requests", ["status"])


def downgrade() -> None:
    op.drop_index("ix_borrow_requests_status", table_name="borrow_requests")
    op.drop_table("borrow_requests")
