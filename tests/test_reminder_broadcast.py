import asyncio
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from api.config import get_settings
from api.database import get_db
from api.main import app
from api.models import Committee, Portfolio
from api.services.reminder_broadcasts import aware, process_reminder_nudges
from tests.conftest import auth_header

ADMIN = 999
USER_A = 1001
USER_B = 1002


def session():
    return contextmanager(app.dependency_overrides[get_db])()


def _register_and_approve(client, telegram_id, email, committee_id):
    client.post(
        "/api/auth/register",
        json={"email": email, "telegram_username": f"user{telegram_id}"},
        headers=auth_header(telegram_id),
    )
    users = client.get("/api/admin/users", headers=auth_header(ADMIN)).json()
    user_id = next(u["id"] for u in users if u["telegram_id"] == telegram_id)
    client.patch(
        f"/api/admin/users/{user_id}",
        json={"status": "approved", "committee_ids": [committee_id]},
        headers=auth_header(ADMIN),
    )
    return user_id


def _setup(client):
    client.post("/api/auth/register", json={"email": "admin@example.com", "telegram_username": "adminuser"}, headers=auth_header(ADMIN))
    committees = client.get("/api/committees", headers=auth_header(ADMIN)).json()
    committee_id = committees[0]["id"]
    _register_and_approve(client, USER_A, "a@example.com", committee_id)
    _register_and_approve(client, USER_B, "b@example.com", committee_id)
    return committee_id


def test_broadcast_delivers_to_committee_members_inbox_and_admin_outbox(client):
    committee_id = _setup(client)
    sent = client.post(
        "/api/reminders/broadcast",
        json={"message": "Submit your blast message", "committee_ids": [committee_id]},
        headers=auth_header(ADMIN),
    )
    assert sent.status_code == 201
    body = sent.json()
    assert body["direction"] == "to_user"
    assert len(body["recipients"]) == 2  # the two approved members of the targeted committee
    assert len(body["committees"]) == 1

    inbox_a = client.get("/api/reminders/inbox", headers=auth_header(USER_A)).json()
    assert len(inbox_a) == 1
    assert inbox_a[0]["message"] == "Submit your blast message"
    assert inbox_a[0]["is_done"] is False
    assert client.get("/api/reminders/unread-count", headers=auth_header(USER_A)).json() == {"count": 1}

    outbox = client.get("/api/reminders/outbox", headers=auth_header(ADMIN)).json()
    assert len(outbox) == 1
    assert {r["user_id"] for r in outbox[0]["recipients"]} != set()
    assert len(outbox[0]["recipients"]) == 2


def test_recipient_marks_done_and_admin_sees_it(client):
    committee_id = _setup(client)
    reminder_id = client.post(
        "/api/reminders/broadcast",
        json={"message": "Reply to this", "committee_ids": [committee_id]},
        headers=auth_header(ADMIN),
    ).json()["id"]

    done = client.patch(f"/api/reminders/{reminder_id}/done", headers=auth_header(USER_A))
    assert done.status_code == 200
    assert done.json()["is_done"] is True
    assert client.get("/api/reminders/unread-count", headers=auth_header(USER_A)).json() == {"count": 0}

    outbox = client.get("/api/reminders/outbox", headers=auth_header(ADMIN)).json()[0]
    statuses = {r["user_id"]: r["is_done"] for r in outbox["recipients"]}
    assert sum(statuses.values()) == 1


def test_recipient_can_only_delete_their_own_done_reminder(client):
    committee_id = _setup(client)
    reminder_id = client.post(
        "/api/reminders/broadcast",
        json={"message": "Cleanup test", "committee_ids": [committee_id]},
        headers=auth_header(ADMIN),
    ).json()["id"]

    blocked = client.delete(f"/api/reminders/{reminder_id}/mine", headers=auth_header(USER_A))
    assert blocked.status_code == 400

    client.patch(f"/api/reminders/{reminder_id}/done", headers=auth_header(USER_A))
    deleted = client.delete(f"/api/reminders/{reminder_id}/mine", headers=auth_header(USER_A))
    assert deleted.status_code == 204

    assert client.get("/api/reminders/inbox", headers=auth_header(USER_A)).json() == []
    inbox_b = client.get("/api/reminders/inbox", headers=auth_header(USER_B)).json()
    assert len(inbox_b) == 1

    outbox = client.get("/api/reminders/outbox", headers=auth_header(ADMIN)).json()[0]
    assert len(outbox["recipients"]) == 2


def test_admin_cannot_target_a_committee_outside_their_portfolio(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "social_admin_telegram_ids", "999")
    monkeypatch.setattr(settings, "welfare_admin_telegram_ids", "998")
    with session() as db:
        welfare = Committee(name="Welfare Only", portfolio=Portfolio.welfare, color="#123456")
        db.add(welfare)
        db.commit()
        welfare_id = welfare.id

    client.post("/api/auth/register", json={"email": "admin@example.com", "telegram_username": "adminuser"}, headers=auth_header(ADMIN))
    resp = client.post(
        "/api/reminders/broadcast",
        json={"message": "Should fail", "committee_ids": [welfare_id]},
        headers=auth_header(ADMIN),
    )
    assert resp.status_code == 403


def test_scheduled_nudges_fire_at_3_and_1_days_and_stop_once_done(client, monkeypatch):
    committee_id = _setup(client)
    settings = get_settings()
    # Fake only the notification sender's credentials; auth stays in dev-bypass mode.
    monkeypatch.setattr(
        "api.services.reminder_broadcasts.get_settings",
        lambda: settings.model_copy(update={"telegram_bot_token": "test-token"}),
    )
    sender = AsyncMock()
    monkeypatch.setattr("api.services.reminder_broadcasts.send_message", sender)

    deadline = "2026-12-31"
    reminder = client.post(
        "/api/reminders/broadcast",
        json={"message": "Finish the form", "committee_ids": [committee_id], "deadline": deadline},
        headers=auth_header(ADMIN),
    ).json()

    with session() as db:
        from api.models import Reminder

        db_reminder = db.get(Reminder, reminder["id"])
        actual_deadline = aware(db_reminder.deadline)

    # 3-day window: both recipients get nudged.
    when = actual_deadline - timedelta(days=3) + timedelta(minutes=1)
    with session() as db:
        asyncio.run(process_reminder_nudges(db, now=when))
    assert sender.await_count == 2

    # USER_A marks done before the 1-day window; they should not be nudged again.
    client.patch(f"/api/reminders/{reminder['id']}/done", headers=auth_header(USER_A))
    sender.reset_mock()
    when = actual_deadline - timedelta(days=1) + timedelta(minutes=1)
    with session() as db:
        asyncio.run(process_reminder_nudges(db, now=when))
        asyncio.run(process_reminder_nudges(db, now=when))  # idempotent: no duplicate sends
    assert sender.await_count == 1
