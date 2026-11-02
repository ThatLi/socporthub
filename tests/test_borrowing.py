import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base
from api.models import (
    BorrowRequest,
    BorrowStatus,
    BotConversation,
    Committee,
    Portfolio,
    User,
    UserCommittee,
    UserRole,
    UserStatus,
)
from api.services import borrowing
from api.services.borrowing import app_today, availability
from bot import borrowing as flow
from bot import handlers


def run(awaitable):
    return asyncio.run(awaitable)


def cb(actor_id: int, data: str) -> dict:
    return {"id": f"cb-{actor_id}", "from": {"id": actor_id}, "data": data}


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    session = sessionmaker(bind=engine)()
    Base.metadata.create_all(engine)
    block = Committee(name="Block 2", color="#E67C73", portfolio=Portfolio.social)
    session.add(block)
    session.add(User(telegram_id=999, email="owner@example.com", display_name="Owner", role=UserRole.admin, status=UserStatus.approved))
    session.commit()
    sent = {}
    for name in (
        "send_message", "answer_callback_query", "download_file",
        "notify_admins_new_borrow_request", "notify_admins_return_proof",
        "notify_user_borrow_approved", "notify_user_borrow_rejected",
        "notify_user_return_confirmed", "notify_user_return_not_confirmed",
        "notify_user_borrow_deleted", "notify_admins_borrow_deleted",
    ):
        mock = AsyncMock()
        monkeypatch.setattr(flow, name, mock)
        sent[name] = mock
    sent["download_file"].return_value = b"jpegbytes"
    session.sent = sent
    yield session
    session.close()


def add_user(db, telegram_id=1001, status=UserStatus.approved):
    user = User(telegram_id=telegram_id, email=f"u{telegram_id}@example.com", display_name=f"User {telegram_id}",
                telegram_username=f"@u{telegram_id}", role=UserRole.user, status=status)
    db.add(user)
    db.flush()
    db.add(UserCommittee(user_id=user.id, committee_id=db.query(Committee).one().id))
    db.commit()
    return user


def add_request(db, user, tables=1, cambro=0, start=0, end=2, status=BorrowStatus.approved):
    today = app_today()
    request = BorrowRequest(
        committee_id=db.query(Committee).one().id, requested_by=user.id, activity_name="Movie night",
        tables=tables, cambro=cambro, borrow_date=today + timedelta(days=start),
        return_date=today + timedelta(days=end), status=status,
    )
    db.add(request)
    db.commit()
    return request


def place_request(db, user_id=1001, tables=2, cambro=1, start=1, end=3):
    """Drive the real conversation to submission."""
    today = app_today()
    run(flow.start_borrow(db, user_id))
    for text in ("Movie night", (today + timedelta(days=start)).isoformat(), (today + timedelta(days=end)).isoformat()):
        run(flow.handle_message(db, user_id, text))
    run(flow.handle_callback(db, cb(user_id, f"borrow:tables:{tables}")))
    if cambro is not None:
        run(flow.handle_callback(db, cb(user_id, f"borrow:cambro:{cambro}")))
    run(flow.handle_callback(db, cb(user_id, "borrow:skip-description")))
    run(flow.handle_callback(db, cb(user_id, "borrow:submit")))


# --- availability -----------------------------------------------------------


def test_availability_counts_only_out_items_and_keeps_overdue_blocking(db):
    user = add_user(db)
    today = app_today()
    add_request(db, user, tables=3, start=-5, end=-3)  # overdue, never confirmed back
    add_request(db, user, tables=1, status=BorrowStatus.pending)
    add_request(db, user, tables=1, status=BorrowStatus.returned)
    add_request(db, user, tables=0, cambro=1, start=10, end=12)
    # The overdue tables are still out today, so they block a window that includes today;
    # pending and returned requests never block.
    assert availability(db, today, today + timedelta(days=2)) == {"tables": 1, "cambro": 1}
    assert availability(db, today + timedelta(days=1), today + timedelta(days=2))["tables"] == 4
    assert availability(db, today + timedelta(days=11), today + timedelta(days=11))["cambro"] == 0


# --- borrower flow ----------------------------------------------------------


def test_borrow_flow_creates_pending_request_and_alerts_admins(db):
    add_user(db)
    place_request(db, tables=2, cambro=1)
    request = db.query(BorrowRequest).one()
    assert (request.tables, request.cambro, request.status) == (2, 1, BorrowStatus.pending)
    assert db.query(BotConversation).count() == 0
    db.sent["notify_admins_new_borrow_request"].assert_awaited_once()


def test_borrow_flow_refuses_more_tables_than_free(db):
    user = add_user(db)
    add_request(db, user, tables=3, start=1, end=3)
    today = app_today()
    run(flow.start_borrow(db, 1001))
    for text in ("Party", (today + timedelta(days=1)).isoformat(), (today + timedelta(days=2)).isoformat()):
        run(flow.handle_message(db, 1001, text))
    run(flow.handle_message(db, 1001, "3"))
    assert db.query(BotConversation).one().step == "tables"
    assert "0 to 1" in db.sent["send_message"].await_args.args[1]
    run(flow.handle_message(db, 1001, "1"))
    assert db.query(BotConversation).one().step == "cambro"


def test_cambro_is_skipped_when_unavailable(db):
    user = add_user(db)
    add_request(db, user, tables=0, cambro=1, start=1, end=3)
    today = app_today()
    run(flow.start_borrow(db, 1001))
    for text in ("Party", (today + timedelta(days=1)).isoformat(), (today + timedelta(days=3)).isoformat()):
        run(flow.handle_message(db, 1001, text))
    run(flow.handle_callback(db, cb(1001, "borrow:tables:1")))
    conversation = db.query(BotConversation).one()
    assert conversation.step == "description"  # cambro step skipped
    assert '"cambro": 0' in conversation.payload


def test_pending_unapproved_user_cannot_borrow(db):
    add_user(db, status=UserStatus.pending)
    run(flow.start_borrow(db, 1001))
    assert db.query(BotConversation).count() == 0


def test_return_date_cannot_precede_borrow_date(db):
    add_user(db)
    today = app_today()
    run(flow.start_borrow(db, 1001))
    run(flow.handle_message(db, 1001, "Party"))
    run(flow.handle_message(db, 1001, (today + timedelta(days=5)).isoformat()))
    run(flow.handle_message(db, 1001, (today + timedelta(days=2)).isoformat()))
    assert db.query(BotConversation).one().step == "return_date"


# --- admin decisions --------------------------------------------------------


def test_admin_approves_and_borrower_is_told(db):
    add_user(db)
    place_request(db)
    request = db.query(BorrowRequest).one()
    run(flow.handle_admin_callback(db, 999, f"admin:borrow:approve:{request.id}"))
    db.refresh(request)
    assert request.status == BorrowStatus.approved and request.decided_by is not None
    db.sent["notify_user_borrow_approved"].assert_awaited_once()


def test_approval_blocked_when_items_no_longer_free(db):
    add_user(db)
    place_request(db, tables=3, cambro=0)
    first = db.query(BorrowRequest).one()
    other = add_request(db, db.query(User).filter_by(telegram_id=1001).one(), tables=3, status=BorrowStatus.pending,
                        start=1, end=3)
    run(flow.handle_admin_callback(db, 999, f"admin:borrow:approve:{first.id}"))
    run(flow.handle_admin_callback(db, 999, f"admin:borrow:approve:{other.id}"))
    db.refresh(other)
    assert other.status == BorrowStatus.pending
    assert "tables are free" in db.sent["send_message"].await_args.args[1]


def test_admin_rejects_with_reason(db):
    add_user(db)
    place_request(db)
    request = db.query(BorrowRequest).one()
    run(flow.handle_admin_callback(db, 999, f"admin:borrow:reject:{request.id}"))
    run(flow.handle_message(db, 999, "Needed that day"))
    db.refresh(request)
    assert request.status == BorrowStatus.rejected and request.rejection_reason == "Needed that day"
    db.sent["notify_user_borrow_rejected"].assert_awaited_once()


def test_non_admin_cannot_decide(db):
    add_user(db)
    add_user(db, telegram_id=1002)
    place_request(db)
    request = db.query(BorrowRequest).one()
    run(flow.handle_admin_callback(db, 1002, f"admin:borrow:approve:{request.id}"))
    db.refresh(request)
    assert request.status == BorrowStatus.pending


# --- return proof -----------------------------------------------------------


def approved_request(db):
    add_user(db)
    place_request(db)
    request = db.query(BorrowRequest).one()
    run(flow.handle_admin_callback(db, 999, f"admin:borrow:approve:{request.id}"))
    return request


def send_photo(db, user_id=1001):
    run(flow.handle_photo(db, user_id, {"photo": [{"file_id": "small", "file_size": 10}, {"file_id": "big", "file_size": 20}]}))


def test_return_photo_is_stored_and_forwarded_then_confirmed(db):
    request = approved_request(db)
    run(flow.start_return(db, 1001))
    send_photo(db)
    db.refresh(request)
    assert request.status == BorrowStatus.return_submitted
    assert request.return_proof_file_id == "big"  # largest size
    assert request.return_proof_data == b"jpegbytes"
    db.sent["notify_admins_return_proof"].assert_awaited_once()
    # items still count as out until the owner confirms
    assert borrowing.summary(db, app_today() + timedelta(days=1))["items"][0]["out"] == 2
    run(flow.handle_admin_callback(db, 999, f"admin:borrow:confirm:{request.id}"))
    db.refresh(request)
    assert request.status == BorrowStatus.returned and request.returned_at
    assert borrowing.summary(db, app_today() + timedelta(days=1))["items"][0]["out"] == 0


def test_not_yet_reopens_the_request_and_clears_proof(db):
    request = approved_request(db)
    run(flow.start_return(db, 1001))
    send_photo(db)
    run(flow.handle_admin_callback(db, 999, f"admin:borrow:notyet:{request.id}"))
    db.refresh(request)
    assert request.status == BorrowStatus.approved and request.return_proof_file_id is None
    db.sent["notify_user_return_not_confirmed"].assert_awaited_once()


def test_text_is_not_accepted_as_return_proof(db):
    request = approved_request(db)
    run(flow.start_return(db, 1001))
    run(flow.handle_message(db, 1001, "i returned it"))
    db.refresh(request)
    assert request.status == BorrowStatus.approved


def test_photo_without_return_flow_is_ignored(db):
    request = approved_request(db)
    send_photo(db)
    db.refresh(request)
    assert request.status == BorrowStatus.approved


def test_someone_elses_request_cannot_be_returned(db):
    request = approved_request(db)
    add_user(db, telegram_id=1002)
    run(flow.start_return(db, 1002, request.id))
    assert db.query(BotConversation).count() == 0


# --- /borrowed and reminders ------------------------------------------------


def test_borrowed_summary_for_admin_only(db):
    request = approved_request(db)
    request.borrow_date = app_today() - timedelta(days=4)
    request.return_date = app_today() - timedelta(days=1)
    db.commit()
    run(flow.handle_borrowed(db, 999))
    text = db.sent["send_message"].await_args.args[1]
    assert "Foldable tables: 2 out, 2 in store" in text and "overdue 1d" in text
    run(flow.handle_borrowed(db, 1001))
    assert "not authorized" in db.sent["send_message"].await_args.args[1]


def test_reminders_fire_once_per_stage_and_overdue_daily(db, monkeypatch):
    request = approved_request(db)
    today = app_today()
    request.borrow_date = today - timedelta(days=1)
    request.return_date = today + timedelta(days=1)
    db.commit()
    user_notice, admin_notice = AsyncMock(), AsyncMock()
    import bot.notifications as notifications
    monkeypatch.setattr(notifications, "notify_user_borrow_reminder", user_notice)
    monkeypatch.setattr(notifications, "notify_admins_overdue_borrows", admin_notice)

    run(borrowing.process_borrow_reminders(db, today))
    run(borrowing.process_borrow_reminders(db, today))
    assert user_notice.await_count == 1 and user_notice.await_args.args[-1] == "day_before"
    run(borrowing.process_borrow_reminders(db, today + timedelta(days=1)))
    assert user_notice.await_args.args[-1] == "due"
    run(borrowing.process_borrow_reminders(db, today + timedelta(days=2)))
    run(borrowing.process_borrow_reminders(db, today + timedelta(days=2)))
    assert user_notice.await_args.args[-1] == "overdue" and user_notice.await_count == 3
    admin_notice.assert_awaited_once()


def test_help_lists_borrow_commands():
    assert "/borrow" in handlers.HELP_TEXT and "/return" in handlers.HELP_TEXT and "/borrowed" in handlers.HELP_TEXT


# --- deleting ---------------------------------------------------------------


def test_admin_deletes_approved_request_with_reason(db, monkeypatch):
    request = approved_request(db)
    request_id = request.id
    run(flow.handle_admin_callback(db, 999, f"admin:borrow:delete:{request_id}"))
    assert db.query(BotConversation).one().flow == "borrow_delete"
    run(flow.handle_message(db, 999, "Event cancelled"))
    assert db.query(BorrowRequest).count() == 0
    assert db.query(BotConversation).count() == 0
    user_notice = db.sent["notify_user_borrow_deleted"]
    user_notice.assert_awaited_once()
    assert user_notice.await_args.args[0] == 1001 and user_notice.await_args.args[-1] == "Event cancelled"
    admin_notice = db.sent["notify_admins_borrow_deleted"]
    admin_notice.assert_awaited_once()
    assert admin_notice.await_args.kwargs["exclude_telegram_id"] == 999
    # the reminder loop has nothing left to remind about
    reminders = AsyncMock()
    import bot.notifications as notifications
    monkeypatch.setattr(notifications, "notify_user_borrow_reminder", reminders)
    run(borrowing.process_borrow_reminders(db, app_today() + timedelta(days=4)))
    reminders.assert_not_awaited()


def test_delete_without_reason_button(db):
    approved_request(db)
    request = db.query(BorrowRequest).one()
    run(flow.handle_admin_callback(db, 999, f"admin:borrow:delete:{request.id}"))
    run(flow.handle_admin_callback(db, 999, f"admin:borrow:delnoreason:{request.id}"))
    assert db.query(BorrowRequest).count() == 0
    assert db.sent["notify_user_borrow_deleted"].await_args.args[-1] is None


def test_deleting_returned_history_does_not_message_the_borrower(db):
    user = add_user(db)
    old = add_request(db, user, status=BorrowStatus.returned, start=-9, end=-7)
    run(flow.start_delete(db, 999, old.id))
    run(flow.handle_admin_callback(db, 999, f"admin:borrow:delnoreason:{old.id}"))
    assert db.query(BorrowRequest).count() == 0
    db.sent["notify_user_borrow_deleted"].assert_not_awaited()
    db.sent["notify_admins_borrow_deleted"].assert_awaited_once()


def test_delete_clears_the_borrowers_open_return_flow_and_stale_buttons(db):
    request = approved_request(db)
    request_id = request.id
    run(flow.start_return(db, 1001))
    assert db.query(BotConversation).filter_by(flow="return").count() == 1
    admin = db.query(User).filter_by(telegram_id=999).one()
    borrowing.delete_borrow(db, admin, request_id)
    assert db.query(BotConversation).count() == 0
    run(flow.handle_admin_callback(db, 999, f"admin:borrow:approve:{request_id}"))
    assert "not found" in db.sent["send_message"].await_args.args[1]


def test_non_admin_cannot_delete(db):
    request = approved_request(db)
    run(flow.start_delete(db, 1001, request.id))
    run(flow.handle_admin_callback(db, 1001, f"admin:borrow:delete:{request.id}"))
    assert db.query(BorrowRequest).count() == 1 and db.query(BotConversation).count() == 0


def test_cancel_aborts_the_delete(db):
    request = approved_request(db)
    run(flow.start_delete(db, 999, request.id))
    from bot.conversations import cancel_conversation
    run(cancel_conversation(db, 999))
    assert db.query(BorrowRequest).count() == 1


# --- admin API --------------------------------------------------------------


def test_borrow_api_is_admin_only_and_serves_proof(client):
    from api.main import app
    from api.database import get_db
    from tests.conftest import auth_header

    db = next(app.dependency_overrides[get_db]())
    db.add(User(telegram_id=999, email="owner@example.com", display_name="Owner", role=UserRole.admin, status=UserStatus.approved))
    member = User(telegram_id=1001, email="m@example.com", display_name="Member", role=UserRole.user, status=UserStatus.approved)
    db.add(member)
    db.flush()
    committee = db.query(Committee).first()
    request = BorrowRequest(
        committee_id=committee.id, requested_by=member.id, activity_name="Quiz", tables=2, cambro=1,
        borrow_date=app_today(), return_date=app_today() + timedelta(days=1), status=BorrowStatus.return_submitted,
        return_proof_file_id="f", return_proof_content_type="image/jpeg", return_proof_data=b"\xff\xd8photo",
    )
    db.add(request)
    db.commit()
    request_id = request.id
    db.close()

    assert client.get("/api/borrow", headers=auth_header(1001)).status_code == 403
    listing = client.get("/api/borrow", headers=auth_header(999)).json()
    assert listing[0]["has_proof"] is True and listing[0]["tables"] == 2
    summary = client.get("/api/borrow/summary", headers=auth_header(999)).json()
    assert summary["awaiting_confirmation"] == 1
    assert summary["items"][0]["out"] == 2
    proof = client.get(f"/api/borrow/{request_id}/proof", headers=auth_header(999))
    assert proof.status_code == 200 and proof.content == b"\xff\xd8photo"
    assert client.get(f"/api/borrow/{request_id}/proof", headers=auth_header(1001)).status_code == 403
    done = client.post(f"/api/borrow/{request_id}/return", json={"confirmed": True}, headers=auth_header(999))
    assert done.status_code == 200 and done.json()["status"] == "returned"


def test_borrow_api_delete_stops_the_request(client):
    from api.main import app
    from api.database import get_db
    from tests.conftest import auth_header

    db = next(app.dependency_overrides[get_db]())
    db.add(User(telegram_id=999, email="owner@example.com", display_name="Owner", role=UserRole.admin, status=UserStatus.approved))
    member = User(telegram_id=1001, email="m@example.com", display_name="Member", role=UserRole.user, status=UserStatus.approved)
    db.add(member)
    db.flush()
    request = BorrowRequest(
        committee_id=db.query(Committee).first().id, requested_by=member.id, activity_name="Quiz", tables=1,
        borrow_date=app_today(), return_date=app_today() + timedelta(days=1), status=BorrowStatus.approved,
    )
    db.add(request)
    db.commit()
    request_id = request.id
    db.close()

    assert client.post(f"/api/borrow/{request_id}/delete", json={"reason": "x"}, headers=auth_header(1001)).status_code == 403
    assert client.post(f"/api/borrow/{request_id}/delete", json={"reason": "Wrong dates"}, headers=auth_header(999)).status_code == 204
    assert client.get("/api/borrow", headers=auth_header(999)).json() == []
    assert client.post(f"/api/borrow/{request_id}/delete", json={}, headers=auth_header(999)).status_code == 404
