"""persistent bot conversations and generalized disposable decisions

Revision ID: 0027
Revises: 0026
"""

from alembic import op
import sqlalchemy as sa


revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("disposable_requests", sa.Column("committee_id", sa.Integer(), nullable=True))
    op.add_column("disposable_requests", sa.Column("activity_name", sa.String(255), nullable=True))
    op.add_column("disposable_requests", sa.Column("status", sa.String(20), nullable=True))
    op.add_column("disposable_requests", sa.Column("rejection_reason", sa.Text(), nullable=True))

    op.execute(
        """
        UPDATE disposable_requests AS d
        SET committee_id = COALESCE(
                (SELECT p.committee_id FROM proposals AS p WHERE p.id = d.proposal_id),
                (SELECT c.id FROM committees AS c WHERE c.name = d.requester_cca LIMIT 1),
                (SELECT uc.committee_id FROM user_committees AS uc
                 WHERE uc.user_id = d.requested_by ORDER BY uc.committee_id LIMIT 1)
            ),
            activity_name = COALESCE(
                (SELECT p.title FROM proposals AS p WHERE p.id = d.proposal_id),
                d.request_title,
                'Disposable request'
            ),
            status = CASE
                WHEN d.rejected THEN 'rejected'
                WHEN d.approved THEN 'approved'
                ELSE 'pending'
            END
        """
    )

    op.alter_column("disposable_requests", "committee_id", existing_type=sa.Integer(), nullable=False)
    op.alter_column("disposable_requests", "activity_name", existing_type=sa.String(255), nullable=False)
    op.alter_column("disposable_requests", "status", existing_type=sa.String(20), nullable=False)
    op.create_foreign_key(
        "fk_disposable_requests_committee_id",
        "disposable_requests",
        "committees",
        ["committee_id"],
        ["id"],
    )
    for column in ("approved", "rejected", "requester_cca", "request_title"):
        op.drop_column("disposable_requests", column)

    op.create_table(
        "bot_conversations",
        sa.Column("telegram_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("flow", sa.String(32), nullable=False),
        sa.Column("step", sa.String(64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("bot_conversations")
    op.add_column(
        "disposable_requests",
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "disposable_requests",
        sa.Column("rejected", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("disposable_requests", sa.Column("requester_cca", sa.String(100), nullable=True))
    op.add_column("disposable_requests", sa.Column("request_title", sa.String(255), nullable=True))
    op.execute(
        """
        UPDATE disposable_requests AS d
        SET approved = (d.status = 'approved'),
            rejected = (d.status = 'rejected'),
            requester_cca = (SELECT c.name FROM committees AS c WHERE c.id = d.committee_id),
            request_title = d.activity_name
        """
    )
    op.drop_constraint("fk_disposable_requests_committee_id", "disposable_requests", type_="foreignkey")
    for column in ("rejection_reason", "status", "activity_name", "committee_id"):
        op.drop_column("disposable_requests", column)
