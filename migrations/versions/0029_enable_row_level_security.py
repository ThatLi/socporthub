"""enable row-level security on every table in the public schema

Supabase exposes the public schema through its REST API to anyone holding the (public)
anon key. This app never uses that API: it connects directly as the database owner,
which bypasses row-level security. Enabling RLS with no policies therefore blocks every
other role without affecting the app.

Postgres only; a no-op elsewhere. New tables created by later migrations must enable
RLS themselves (see CLAUDE.md), because this migration only covers existing tables.

Revision ID: 0029
Revises: 0028
"""

from alembic import op


revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def _for_each_public_table(action: str) -> None:
    # No `%` anywhere in this SQL: SQLAlchemy escapes it for the driver, which would risk
    # sending a literal "%%" to Postgres. quote_ident keeps unusual table names safe.
    op.execute(
        f"""
        DO $$
        DECLARE table_record record;
        BEGIN
            FOR table_record IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
                EXECUTE 'ALTER TABLE public.' || quote_ident(table_record.tablename) || ' {action} ROW LEVEL SECURITY';
            END LOOP;
        END $$
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        _for_each_public_table("ENABLE")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        _for_each_public_table("DISABLE")
