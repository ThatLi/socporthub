import importlib.util
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from alembic.migration import MigrationContext
from alembic.operations import Operations

from api.config import Settings
from api.database import engine_options, is_external_pooler

POOLER = "postgresql://postgres.abc:pw@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres?sslmode=require"
SESSION = "postgresql://postgres.abc:pw@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres?sslmode=require"


def test_transaction_pooler_gets_nullpool_and_everything_else_keeps_default_pooling():
    assert is_external_pooler(POOLER)
    assert engine_options(POOLER) == {"poolclass": NullPool}
    assert engine_options(SESSION) == {}
    assert engine_options("postgresql://user:pw@localhost:5432/app") == {}
    assert engine_options("postgresql://user:pw@localhost/app") == {}


def test_sqlite_keeps_its_thread_flag():
    assert engine_options("sqlite:///./dev.db") == {"connect_args": {"check_same_thread": False}}


def test_migrations_prefer_the_direct_url_when_set():
    assert Settings(database_url=POOLER, database_url_direct=SESSION).migration_database_url == SESSION
    assert Settings(database_url=POOLER, database_url_direct="").migration_database_url == POOLER


def test_rls_migration_is_chained_and_a_noop_off_postgres():
    path = Path(__file__).resolve().parent.parent / "migrations" / "versions" / "0029_enable_row_level_security.py"
    spec = importlib.util.spec_from_file_location("migration_0029", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == ("0029", "0028")

    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            module.upgrade()
            module.downgrade()


def test_migration_chain_has_a_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parent.parent
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    assert ScriptDirectory.from_config(config).get_heads() == ["0029"]


def test_rls_migration_sql_has_no_percent_signs_and_targets_the_public_schema():
    import io

    path = Path(__file__).resolve().parent.parent / "migrations" / "versions" / "0029_enable_row_level_security.py"
    spec = importlib.util.spec_from_file_location("migration_0029_sql", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    buffer = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": buffer})
    with Operations.context(context):
        module.upgrade()
    sql = buffer.getvalue()
    assert "%" not in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql and "schemaname = 'public'" in sql and "quote_ident" in sql
    buffer.seek(0)
    buffer.truncate()
    with Operations.context(context):
        module.downgrade()
    assert "DISABLE ROW LEVEL SECURITY" in buffer.getvalue()
