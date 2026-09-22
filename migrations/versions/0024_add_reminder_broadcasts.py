"""Admin-to-CCA reminder broadcasts: direction/deadline on reminders, targeted
committees, per-recipient inbox state, and scheduled nudge delivery receipts.

Revision ID: 0024
Revises: 0023
"""
import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "reminders",
        sa.Column(
            "direction",
            sa.Enum("to_admin", "to_user", name="reminderdirection", native_enum=False),
            nullable=False,
            server_default="to_admin",
        ),
    )
    op.add_column("reminders", sa.Column("deadline", sa.DateTime(timezone=True)))

    op.create_table(
        "reminder_target_committees",
        sa.Column("reminder_id", sa.Integer(), sa.ForeignKey("reminders.id"), primary_key=True),
        sa.Column("committee_id", sa.Integer(), sa.ForeignKey("committees.id"), primary_key=True),
    )

    op.create_table(
        "reminder_recipients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reminder_id", sa.Integer(), sa.ForeignKey("reminders.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_done", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("reminder_id", "user_id"),
    )

    op.create_table(
        "reminder_nudges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reminder_recipient_id", sa.Integer(), sa.ForeignKey("reminder_recipients.id"), nullable=False),
        sa.Column("milestone", sa.String(16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("reminder_recipient_id", "milestone"),
    )


def downgrade():
    op.drop_table("reminder_nudges")
    op.drop_table("reminder_recipients")
    op.drop_table("reminder_target_committees")
    op.drop_column("reminders", "deadline")
    op.drop_column("reminders", "direction")
