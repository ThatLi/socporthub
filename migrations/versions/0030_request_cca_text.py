"""store the CCA of disposable and borrow requests as text; committee_id becomes optional

Revision ID: 0030
Revises: 0029
"""

from alembic import op
import sqlalchemy as sa


revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

TABLES = ("disposable_requests", "borrow_requests")


def upgrade() -> None:
    for table in TABLES:
        # Add as nullable first so existing rows can be filled before NOT NULL applies.
        op.add_column(table, sa.Column("requester_cca", sa.String(255), nullable=True))
        # Snapshot of the committee name at migration time. committee_id is kept on old rows.
        op.execute(
            f"UPDATE {table} SET requester_cca = "
            f"(SELECT c.name FROM committees AS c WHERE c.id = {table}.committee_id)"
        )
        op.execute(f"UPDATE {table} SET requester_cca = 'personal' WHERE requester_cca IS NULL")
        with op.batch_alter_table(table) as batch:
            batch.alter_column("requester_cca", existing_type=sa.String(255), nullable=False)
            batch.alter_column("committee_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    # Requests filed without a committee have nothing to point to, so they cannot be restored.
    for table in TABLES:
        op.execute(f"DELETE FROM {table} WHERE committee_id IS NULL")
        with op.batch_alter_table(table) as batch:
            batch.alter_column("committee_id", existing_type=sa.Integer(), nullable=False)
            batch.drop_column("requester_cca")
