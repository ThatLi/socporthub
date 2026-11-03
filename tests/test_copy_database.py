import importlib.util
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import StaticPool, create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from api.database import Base
from api.models import (
    BorrowRequest,
    BorrowStatus,
    Committee,
    Proposal,
    ProposalComment,
    ProposalStatus,
    User,
    UserCommittee,
    UserRole,
    UserStatus,
)

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "copy_database.py"
spec = importlib.util.spec_from_file_location("copy_database", SCRIPT)
copy_database = importlib.util.module_from_spec(spec)
spec.loader.exec_module(copy_database)


def memory_engine():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def pair():
    source, dest = memory_engine(), memory_engine()
    db = sessionmaker(bind=source)()
    block = Committee(name="Block 2", color="#E67C73", rf_name="RF", rf_email="rf@example.com")
    soccom = Committee(name="Soccom", color="#6366F1")
    user = User(telegram_id=1001, email="a@example.com", display_name="A", role=UserRole.user, status=UserStatus.approved)
    db.add_all([block, soccom, user])
    db.flush()
    db.add(UserCommittee(user_id=user.id, committee_id=block.id))
    proposal = Proposal(
        committee_id=block.id, submitted_by=user.id, title="Movie night", status=ProposalStatus.in_review,
        event_date=date(2026, 12, 1), poster_filename="p.png", poster_content_type="image/png", poster_data=b"\x89PNG-bytes",
    )
    db.add(proposal)
    db.flush()
    first = ProposalComment(proposal_id=proposal.id, author_id=user.id, body="first")
    db.add(first)
    db.flush()
    db.add(ProposalComment(proposal_id=proposal.id, author_id=user.id, body="reply", reply_to_comment_id=first.id))
    db.add(BorrowRequest(requester_cca="Test CCA", 
        committee_id=block.id, requested_by=user.id, activity_name="Quiz", tables=2, cambro=1,
        borrow_date=date(2026, 12, 2), return_date=date(2026, 12, 3), status=BorrowStatus.return_submitted,
        return_proof_file_id="f", return_proof_data=b"photo",
    ))
    db.commit()
    db.close()
    # the destination already holds seeded committees, like a freshly migrated Supabase database
    seed = sessionmaker(bind=dest)()
    seed.add(Committee(name="Block 2", color="#000000"))
    seed.commit()
    seed.close()
    return source, dest


def count(engine, table):
    with engine.connect() as connection:
        return connection.execute(select(func.count()).select_from(table)).scalar()


def test_refuses_to_overwrite_without_replace_and_changes_nothing(pair):
    source, dest = pair
    with pytest.raises(SystemExit, match="--replace"):
        copy_database.copy_database(source, dest, replace=False, log=lambda *_: None)
    assert count(dest, Committee.__table__) == 1 and count(dest, User.__table__) == 0


def test_copy_replaces_seed_rows_and_preserves_ids_and_binary_data(pair):
    source, dest = pair
    copied = copy_database.copy_database(source, dest, replace=True, log=lambda *_: None)
    assert copied["committees"] == 2 and copied["proposal_comments"] == 2 and copied["borrow_requests"] == 1
    with dest.connect() as connection:
        assert connection.execute(select(Committee.name, Committee.rf_email).order_by(Committee.id)).all() == [
            ("Block 2", "rf@example.com"), ("Soccom", None)
        ]
        assert connection.execute(select(Proposal.poster_data)).scalar() == b"\x89PNG-bytes"
        assert connection.execute(select(BorrowRequest.return_proof_data)).scalar() == b"photo"
        replies = connection.execute(select(ProposalComment.id, ProposalComment.reply_to_comment_id).order_by(ProposalComment.id)).all()
        assert replies[1][1] == replies[0][0]
    for table in Base.metadata.sorted_tables:
        assert count(source, table) == count(dest, table), table.name


def test_source_is_left_untouched(pair):
    source, dest = pair
    before = {t.name: count(source, t) for t in Base.metadata.sorted_tables}
    copy_database.copy_database(source, dest, replace=True, log=lambda *_: None)
    assert {t.name: count(source, t) for t in Base.metadata.sorted_tables} == before


def test_destination_without_tables_is_rejected(pair):
    source, _ = pair
    bare = create_engine("sqlite:///:memory:")
    with pytest.raises(SystemExit, match="alembic upgrade head"):
        copy_database.copy_database(source, bare, replace=True, log=lambda *_: None)


def test_revision_check_reads_alembic_version(pair):
    source, _ = pair
    assert copy_database.revision(source) is None
    with source.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('0029')"))
    assert copy_database.revision(source) == "0029"
    assert "0029" in copy_database.SUPPORTED_REVISIONS and "0028" in copy_database.SUPPORTED_REVISIONS


def test_unreachable_database_gives_a_readable_message_with_a_railway_hint():
    unreachable = create_engine("sqlite:////no/such/directory/x.db")
    with pytest.raises(SystemExit, match="Could not connect to the source database"):
        copy_database.check_connection(unreachable, "source")
    assert "DATABASE_PUBLIC_URL" in copy_database.connection_hint("postgres.railway.internal")
    assert "DATABASE_PUBLIC_URL" not in copy_database.connection_hint("db.example.com")
