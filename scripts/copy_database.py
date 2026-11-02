"""One-off copy of all application data from one database to another (Railway -> Supabase).

Why a script instead of pg_dump: it needs only the Python packages the app already uses.

    SOURCE_DATABASE_URL  database to read from  (never written to: read-only transaction)
    DEST_DATABASE_URL    database to copy into  (schema must already be at the same revision)

    python scripts/copy_database.py --dry-run     # show what would be copied, change nothing
    python scripts/copy_database.py --replace     # copy; replaces what is in the destination

URLs are read from the environment so passwords never appear in shell history or output.
Rows keep their ids, so foreign keys stay valid; id sequences are reset afterwards.
Run it while nobody is using the old deployment, then point the bot at the new one.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Importing the app's models builds an engine from DATABASE_URL. This script uses only its
# own two URLs, so give the app code a harmless in-memory database instead of whatever .env says.
os.environ["DATABASE_URL"] = "sqlite://"

from sqlalchemy import create_engine, func, inspect, select, text  # noqa: E402
from sqlalchemy.engine import Engine, make_url  # noqa: E402

from api import models  # noqa: E402,F401  registers every table on Base.metadata
from api.database import Base  # noqa: E402

# Tables are identical in revisions 0028 and 0029 (0029 only enables row-level security).
SUPPORTED_REVISIONS = {"0028", "0029"}
BATCH_ROWS = 500
BATCH_ROWS_WITH_BINARY = 25  # posters and return photos are large


def describe(engine: Engine) -> str:
    url = engine.url
    return f"{url.get_backend_name()} at {url.host or url.database}" + (f":{url.port}" if url.port else "")


def revision(engine: Engine) -> str | None:
    if not inspect(engine).has_table("alembic_version"):
        return None
    with engine.connect() as connection:
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()


def table_counts(engine: Engine, tables) -> dict[str, int | None]:
    present = set(inspect(engine).get_table_names())
    counts: dict[str, int | None] = {}
    with engine.connect() as connection:
        for table in tables:
            counts[table.name] = (
                connection.execute(select(func.count()).select_from(table)).scalar() if table.name in present else None
            )
    return counts


def _has_binary(table) -> bool:
    return any(type(column.type).__name__ == "LargeBinary" for column in table.columns)


def _reset_sequence(connection, table) -> None:
    """After inserting explicit ids, move the serial sequence past the largest one."""
    primary_key = list(table.primary_key.columns)
    if len(primary_key) != 1 or primary_key[0].name != "id" or primary_key[0].autoincrement is False:
        return
    connection.execute(
        text(
            "SELECT setval(pg_get_serial_sequence(:table, 'id'), "
            "COALESCE((SELECT MAX(id) FROM " + f'"{table.name}"' + "), 1), "
            "(SELECT MAX(id) FROM " + f'"{table.name}"' + ") IS NOT NULL)"
        ),
        {"table": f"public.{table.name}"},
    )


def copy_database(source: Engine, dest: Engine, *, replace: bool, log=print) -> dict[str, int]:
    """Copy every application table. Raises (and rolls the destination back) on any problem.

    Returns rows copied per table."""
    tables = list(Base.metadata.sorted_tables)  # parents before children
    source_counts = table_counts(source, tables)
    dest_counts = table_counts(dest, tables)

    missing_in_dest = [t for t, n in dest_counts.items() if n is None]
    if missing_in_dest:
        raise SystemExit(f"Destination is missing tables {missing_in_dest}. Run `alembic upgrade head` on it first.")
    occupied = {name: n for name, n in dest_counts.items() if n}
    if occupied and not replace:
        raise SystemExit(
            f"Destination already has rows: {occupied}. "
            "Migrations seed the committees table, so pass --replace to overwrite the destination's data."
        )

    copied: dict[str, int] = {}
    with source.connect() as read, dest.begin() as write:
        if source.dialect.name == "postgresql":
            read.exec_driver_sql("SET TRANSACTION READ ONLY")
        if replace:
            for table in reversed(tables):  # children before parents
                write.execute(table.delete())
        for table in tables:
            if source_counts[table.name] is None:
                log(f"  {table.name}: not in source, skipped")
                copied[table.name] = 0
                continue
            order = [column for column in table.primary_key.columns]  # parents first for self-references
            result = read.execution_options(stream_results=True).execute(select(table).order_by(*order))
            size = BATCH_ROWS_WITH_BINARY if _has_binary(table) else BATCH_ROWS
            total = 0
            while True:
                rows = result.mappings().fetchmany(size)
                if not rows:
                    break
                write.execute(table.insert(), [dict(row) for row in rows])
                total += len(rows)
            copied[table.name] = total
            log(f"  {table.name}: {total} rows")
        if dest.dialect.name == "postgresql":
            for table in tables:
                _reset_sequence(write, table)

    final = table_counts(dest, tables)
    mismatches = {
        name: (source_counts[name], final[name]) for name in final if (source_counts[name] or 0) != (final[name] or 0)
    }
    if mismatches:
        raise SystemExit(f"Row counts differ after copying (source, destination): {mismatches}")
    return copied


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="show what would be copied and stop")
    parser.add_argument("--replace", action="store_true", help="delete the destination's rows first")
    args = parser.parse_args()

    source_url, dest_url = os.environ.get("SOURCE_DATABASE_URL"), os.environ.get("DEST_DATABASE_URL")
    if not source_url or not dest_url:
        raise SystemExit("Set SOURCE_DATABASE_URL and DEST_DATABASE_URL first (see the top of this file).")
    if make_url(source_url).render_as_string(hide_password=False) == make_url(dest_url).render_as_string(hide_password=False):
        raise SystemExit("Source and destination are the same database.")

    source, dest = create_engine(source_url), create_engine(dest_url)
    print(f"Source:      {describe(source)}")
    print(f"Destination: {describe(dest)}")
    source_revision, dest_revision = revision(source), revision(dest)
    print(f"Revisions:   source {source_revision}, destination {dest_revision}")
    for name, value in (("source", source_revision), ("destination", dest_revision)):
        if value not in SUPPORTED_REVISIONS:
            raise SystemExit(
                f"The {name} database is at revision {value}; expected one of {sorted(SUPPORTED_REVISIONS)}. "
                "Bring it to head with `alembic upgrade head` first so the tables match."
            )

    tables = list(Base.metadata.sorted_tables)
    source_counts, dest_counts = table_counts(source, tables), table_counts(dest, tables)
    print("\nRows (source -> destination before copying):")
    for table in tables:
        print(f"  {table.name}: {source_counts[table.name]} -> {dest_counts[table.name]}")
    if args.dry_run:
        print("\nDry run only; nothing was changed.")
        return

    print("\nCopying (one transaction; any error leaves the destination untouched):")
    copy_database(source, dest, replace=args.replace)
    print("\nDone. Row counts match in every table.")


if __name__ == "__main__":
    main()
