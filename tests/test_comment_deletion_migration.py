import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_comment_deletion_migration_preserves_existing_comments(monkeypatch):
    migration = importlib.import_module("migrations.versions.0031_add_comment_deletion")
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE proposal_comments (id INTEGER PRIMARY KEY, body TEXT NOT NULL)"))
        connection.execute(text("INSERT INTO proposal_comments (id, body) VALUES (1, 'Existing comment')"))
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        assert connection.execute(text("SELECT body, deleted_at FROM proposal_comments")).one() == ("Existing comment", None)
        migration.downgrade()
        assert "deleted_at" not in {column["name"] for column in inspect(connection).get_columns("proposal_comments")}
        assert connection.execute(text("SELECT body FROM proposal_comments")).scalar_one() == "Existing comment"
