import asyncio
import json
from datetime import date, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import sessionmaker

from api.config import get_settings
from api.database import Base
from api.models import (
    BotConversation,
    Committee,
    DisposableRequest,
    DisposableStatus,
    Portfolio,
    User,
    UserCommittee,
    UserRole,
    UserStatus,
)
from api.services.disposables import decide_disposable
from bot import conversations


def run(awaitable):
    return asyncio.run(awaitable)


def callback(actor_id: int, data: str) -> dict:
    return {"id": f"callback-{actor_id}", "from": {"id": actor_id}, "data": data}


@pytest.fixture()
def bot_db(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Session = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    db = Session()
    social = Committee(name="Block 2", color="#E67C73", portfolio=Portfolio.social)
    welfare = Committee(name="Welfare Comm", color="#607D8B", portfolio=Portfolio.welfare)
    admin = User(
        telegram_id=999,
        email="admin@example.com",
        display_name="Admin",
        role=UserRole.admin,
        status=UserStatus.approved,
    )
    db.add_all([social, welfare, admin])
    db.commit()

    for name in (
        "send_message",
        "answer_callback_query",
        "notify_admins_registration_request",
        "notify_admins_new_disposable_request",
        "notify_user_registration_approved",
        "notify_user_registration_rejected_with_reason",
        "notify_user_disposable_approved",
        "notify_user_disposable_rejected",
    ):
        monkeypatch.setattr(conversations, name, AsyncMock())
    yield db
    db.close()


def add_approved_user(db, telegram_id=1001, committee_name="Block 2"):
    committee = db.query(Committee).filter_by(name=committee_name).one()
    user = User(
        telegram_id=telegram_id,
        email=f"user{telegram_id}@example.com",
        display_name=f"User {telegram_id}",
        role=UserRole.user,
        status=UserStatus.approved,
    )
    db.add(user)
    db.flush()
    db.add(UserCommittee(user_id=user.id, committee_id=committee.id))
    db.commit()
    return user, committee


def test_registration_conversation_and_admin_approval(bot_db):
    db = bot_db
    committee = db.query(Committee).filter_by(name="Block 2").one()

    run(conversations.start_registration(db, 1001, "new_user"))
    assert db.get(BotConversation, 1001).step == "name"
    run(conversations.handle_conversation_message(db, 1001, "New User"))
    assert db.get(BotConversation, 1001).step == "email"
    run(conversations.handle_conversation_message(db, 1001, "new@example.com"))
    assert db.get(BotConversation, 1001).step == "committee"
    run(conversations.handle_callback(db, callback(1001, f"reg:committee:{committee.id}")))
    assert db.get(BotConversation, 1001).step == "confirm"

    run(conversations.handle_callback(db, callback(1001, "reg:edit:name")))
    run(conversations.handle_conversation_message(db, 1001, "Updated Name"))
    assert db.get(BotConversation, 1001).step == "confirm"
    run(conversations.handle_callback(db, callback(1001, "reg:submit")))

    user = db.query(User).filter_by(telegram_id=1001).one()
    assert user.status == UserStatus.pending
    assert user.display_name == "Updated Name"
    assert user.telegram_username == "@new_user"
    assert user.committee_ids == {committee.id}
    assert db.get(BotConversation, 1001) is None
    conversations.notify_admins_registration_request.assert_awaited_once()

    run(conversations.handle_callback(db, callback(999, f"admin:reg:approve:{user.id}")))
    db.refresh(user)
    assert user.status == UserStatus.approved
    conversations.notify_user_registration_approved.assert_awaited_once_with(1001)


def test_registration_duplicate_email_and_rejected_user_can_retry(bot_db):
    db = bot_db
    existing = User(
        telegram_id=2000,
        email="used@example.com",
        role=UserRole.user,
        status=UserStatus.approved,
    )
    rejected = User(
        telegram_id=1001,
        email="old@example.com",
        role=UserRole.user,
        status=UserStatus.rejected,
    )
    db.add_all([existing, rejected])
    db.commit()

    run(conversations.start_registration(db, 1001, None))
    run(conversations.handle_conversation_message(db, 1001, "Retry User"))
    run(conversations.handle_conversation_message(db, 1001, "used@example.com"))
    assert db.get(BotConversation, 1001).step == "email"
    run(conversations.handle_conversation_message(db, 1001, "fresh@example.com"))
    assert db.get(BotConversation, 1001).step == "committee"


def test_disposable_conversation_description_submit_and_approve(bot_db):
    db = bot_db
    user, committee = add_approved_user(db)
    run(conversations.start_disposable(db, user.telegram_id))
    assert db.get(BotConversation, user.telegram_id).step == "activity_name"

    values = [
        "Movie Night",
        "10",  # cups
        "0",   # bowls
        "5",   # spoons
        "5",   # forks
        "10",  # plates
        (date.today() + timedelta(days=1)).isoformat(),
        "18:30",
        "For residents attending the screening",
    ]
    for value in values:
        assert run(conversations.handle_conversation_message(db, user.telegram_id, value)) is True

    conversation = db.get(BotConversation, user.telegram_id)
    assert conversation.step == "confirm"
    run(conversations.handle_callback(db, callback(user.telegram_id, "disp:submit")))

    disposable = db.query(DisposableRequest).one()
    assert disposable.proposal_id is None
    assert disposable.committee_id == committee.id
    assert disposable.activity_name == "Movie Night"
    assert disposable.description == "For residents attending the screening"
    assert disposable.status == DisposableStatus.pending
    assert disposable.cups == 10 and disposable.plates == 10
    assert db.get(BotConversation, user.telegram_id) is None
    conversations.notify_admins_new_disposable_request.assert_awaited_once()

    run(conversations.handle_callback(db, callback(999, f"admin:disp:approve:{disposable.id}")))
    db.refresh(disposable)
    assert disposable.status == DisposableStatus.approved
    conversations.notify_user_disposable_approved.assert_awaited_once()

    # Repeating the same callback is harmless and does not notify twice.
    run(conversations.handle_callback(db, callback(999, f"admin:disp:approve:{disposable.id}")))
    assert conversations.notify_user_disposable_approved.await_count == 1


def test_disposable_prompts_for_committee_when_user_has_multiple(bot_db):
    db = bot_db
    user, social = add_approved_user(db)
    welfare = db.query(Committee).filter_by(name="Welfare Comm").one()
    db.add(UserCommittee(user_id=user.id, committee_id=welfare.id))
    db.commit()

    run(conversations.start_disposable(db, user.telegram_id))
    assert db.get(BotConversation, user.telegram_id).step == "committee"
    run(conversations.handle_callback(db, callback(user.telegram_id, f"disp:committee:{social.id}")))
    saved = db.get(BotConversation, user.telegram_id)
    assert saved.step == "activity_name"
    assert json.loads(saved.payload)["committee_id"] == social.id


def test_registration_rejection_without_reason_allows_retry(bot_db):
    db = bot_db
    committee = db.query(Committee).filter_by(name="Block 2").one()
    user = User(
        telegram_id=3100,
        email="pending@example.com",
        display_name="Pending User",
        role=UserRole.user,
        status=UserStatus.pending,
    )
    db.add(user)
    db.flush()
    db.add(UserCommittee(user_id=user.id, committee_id=committee.id))
    db.commit()

    run(conversations.handle_callback(db, callback(999, f"admin:reg:reject:{user.id}")))
    run(conversations.handle_callback(db, callback(999, "admin:reject:skip:0")))
    db.refresh(user)
    assert user.status == UserStatus.rejected
    conversations.notify_user_registration_rejected_with_reason.assert_awaited_once_with(3100, None)

    run(conversations.start_registration(db, user.telegram_id, None))
    assert db.get(BotConversation, user.telegram_id).step == "name"


def test_description_validation_does_not_advance_and_cancel_always_works(bot_db):
    db = bot_db
    user, _ = add_approved_user(db)
    conversation = BotConversation(
        telegram_id=user.telegram_id,
        flow="disposable",
        step="description",
        payload=json.dumps({"committee_id": user.committee_memberships[0].committee_id}),
    )
    db.add(conversation)
    db.commit()

    run(conversations.handle_conversation_message(db, user.telegram_id, "x" * 2001))
    assert db.get(BotConversation, user.telegram_id).step == "description"
    run(conversations.cancel_conversation(db, user.telegram_id))
    assert db.get(BotConversation, user.telegram_id) is None


@pytest.mark.parametrize(
    "flow,step",
    [
        ("register", "name"),
        ("register", "email"),
        ("register", "committee"),
        ("register", "confirm"),
        ("disposable", "activity_name"),
        ("disposable", "cups"),
        ("disposable", "collection_date"),
        ("disposable", "description"),
        ("disposable", "confirm"),
        ("admin_reject", "reason"),
    ],
)
def test_cancel_removes_every_conversation_state(bot_db, flow, step):
    db = bot_db
    telegram_id = 5000
    db.add(BotConversation(telegram_id=telegram_id, flow=flow, step=step, payload="{}"))
    db.commit()
    run(conversations.cancel_conversation(db, telegram_id))
    assert db.get(BotConversation, telegram_id) is None


def test_optional_rejection_reason_and_portfolio_guard(bot_db, monkeypatch):
    db = bot_db
    user, social = add_approved_user(db)
    request = DisposableRequest(
        committee_id=social.id,
        requested_by=user.id,
        activity_name="Supper",
        plates=10,
        collection_date=date.today() + timedelta(days=1),
        status=DisposableStatus.pending,
    )
    db.add(request)
    db.commit()

    run(conversations.handle_callback(db, callback(999, f"admin:disp:reject:{request.id}")))
    assert db.get(BotConversation, 999).flow == "admin_reject"
    run(conversations.handle_conversation_message(db, 999, "Insufficient notice"))
    db.refresh(request)
    assert request.status == DisposableStatus.rejected
    assert request.rejection_reason == "Insufficient notice"
    conversations.notify_user_disposable_rejected.assert_awaited_once_with(
        user.telegram_id, "Supper", "Insufficient notice"
    )

    welfare_user, welfare = add_approved_user(db, telegram_id=1002, committee_name="Welfare Comm")
    welfare_request = DisposableRequest(
        committee_id=welfare.id,
        requested_by=welfare_user.id,
        activity_name="Care Packs",
        cups=5,
        collection_date=date.today() + timedelta(days=1),
        status=DisposableStatus.pending,
    )
    db.add(welfare_request)
    db.commit()
    admin = db.query(User).filter_by(telegram_id=999).one()
    with pytest.raises(HTTPException) as exc:
        decide_disposable(db, admin, welfare_request.id, DisposableStatus.approved)
    assert exc.value.status_code == 404


def test_invalid_quantity_date_and_time_keep_current_step(bot_db):
    db = bot_db
    user, committee = add_approved_user(db)
    conversation = BotConversation(
        telegram_id=user.telegram_id,
        flow="disposable",
        step="cups",
        payload=json.dumps({"committee_id": committee.id, "activity_name": "Test"}),
    )
    db.add(conversation)
    db.commit()
    run(conversations.handle_conversation_message(db, user.telegram_id, "-1"))
    assert db.get(BotConversation, user.telegram_id).step == "cups"

    conversation.step = "collection_date"
    db.commit()
    run(conversations.handle_conversation_message(db, user.telegram_id, "2000-01-01"))
    assert db.get(BotConversation, user.telegram_id).step == "collection_date"

    conversation.step = "collection_time"
    db.commit()
    run(conversations.handle_conversation_message(db, user.telegram_id, "25:99"))
    assert db.get(BotConversation, user.telegram_id).step == "collection_time"
    run(conversations.handle_conversation_message(db, user.telegram_id, "18:30:15"))
    assert db.get(BotConversation, user.telegram_id).step == "collection_time"


def test_stale_callback_cannot_skip_the_stored_step(bot_db):
    db = bot_db
    telegram_id = 7001
    db.add(BotConversation(
        telegram_id=telegram_id,
        flow="register",
        step="name",
        payload=json.dumps({}),
    ))
    db.commit()

    run(conversations.handle_callback(db, callback(telegram_id, "reg:submit")))
    assert db.get(BotConversation, telegram_id).step == "name"
    assert db.query(User).filter_by(telegram_id=telegram_id).first() is None
