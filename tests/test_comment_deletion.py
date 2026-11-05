from unittest.mock import AsyncMock

import pytest

from api.models import ProposalComment, User, UserRole, UserStatus
from tests.conftest import auth_header
from tests.test_proposals import ADMIN, USER_A, USER_B, _approve_user, register, session


@pytest.fixture
def thread(client):
    register(client, ADMIN, "admin@example.com")
    _approve_user(client, USER_A, "a@example.com")
    _approve_user(client, USER_B, "b@example.com")
    proposal = client.post("/api/proposals", json={"title": "Review me", "category": "decor"}, headers=auth_header(USER_A)).json()
    base = f"/api/proposals/{proposal['id']}"
    comment = client.post(f"{base}/comments", json={"body": "Private original"}, headers=auth_header(ADMIN)).json()
    return base, comment


def test_delete_preserves_thread_redacts_body_and_is_repeatable(client, thread, monkeypatch):
    base, original = thread
    reply = client.post(f"{base}/comments", json={"body": "Existing reply", "reply_to_comment_id": original["id"]}, headers=auth_header(USER_A)).json()
    before = client.get("/api/proposals", headers=auth_header(USER_A)).json()[0]
    assert before["unread_comment_count"] == 1
    notify = AsyncMock()
    monkeypatch.setattr("api.routes.proposals.notify_user_new_comment", notify)
    monkeypatch.setattr("api.routes.proposals.notify_admins_new_comment", notify)

    endpoint = f"{base}/comments/{original['id']}"
    assert client.delete(endpoint, headers=auth_header(ADMIN)).status_code == 204
    for viewer in (ADMIN, USER_A, USER_B):
        comments = client.get(f"{base}/comments", headers=auth_header(viewer)).json()
        assert comments[0]["body"] == "This comment was deleted."
        assert comments[0]["deleted_at"] is not None
        assert comments[0]["created_at"] == original["created_at"]
        assert comments[0]["author_id"] == original["author_id"]
        assert comments[1]["reply_to_comment_id"] == original["id"]
        assert comments[1]["body"] == reply["body"]
    deleted_at = comments[0]["deleted_at"]
    assert client.delete(endpoint, headers=auth_header(ADMIN)).status_code == 204
    assert client.get(f"{base}/comments", headers=auth_header(ADMIN)).json()[0]["deleted_at"] == deleted_at
    after = client.get("/api/proposals", headers=auth_header(USER_A)).json()[0]
    assert after["unread_comment_count"] == 0
    assert after["latest_comment_at"] == before["latest_comment_at"]
    assert after["updated_at"] == before["updated_at"]
    with session() as db:
        assert db.get(ProposalComment, original["id"]).body == "Private original"
    notify.assert_not_awaited()


def test_delete_requires_current_admin_and_comment_ownership(client, thread):
    base, original = thread
    endpoint = f"{base}/comments/{original['id']}"
    assert client.delete(endpoint).status_code == 422
    assert client.delete(endpoint, headers=auth_header(USER_A)).status_code == 403
    user_comment = client.post(f"{base}/comments", json={"body": "User comment"}, headers=auth_header(USER_A)).json()
    assert client.delete(f"{base}/comments/{user_comment['id']}", headers=auth_header(ADMIN)).status_code == 403
    with session() as db:
        other_admin = db.query(User).filter_by(telegram_id=USER_B).one()
        other_admin.role = UserRole.admin
        db.commit()
    assert client.delete(endpoint, headers=auth_header(USER_B)).status_code == 403
    with session() as db:
        other_admin = db.query(User).filter_by(telegram_id=USER_B).one()
        other_admin.status = UserStatus.rejected
        db.commit()
    assert client.delete(endpoint, headers=auth_header(USER_B)).status_code == 403
    assert client.get(f"{base}/comments", headers=auth_header(ADMIN)).json()[0]["deleted_at"] is None


def test_delete_rejects_missing_mismatched_and_inaccessible_records(client, thread, monkeypatch):
    from api.config import get_settings

    base, original = thread
    assert client.delete(f"{base}/comments/999999", headers=auth_header(ADMIN)).status_code == 404
    other = client.post("/api/proposals", json={"title": "Other proposal", "category": "decor"}, headers=auth_header(USER_A)).json()
    assert client.delete(f"/api/proposals/{other['id']}/comments/{original['id']}", headers=auth_header(ADMIN)).status_code == 404
    assert client.delete(f"/api/proposals/999999/comments/{original['id']}", headers=auth_header(ADMIN)).status_code == 404
    monkeypatch.setattr(get_settings(), "welfare_admin_telegram_ids", str(ADMIN))
    assert client.delete(f"{base}/comments/{original['id']}", headers=auth_header(ADMIN)).status_code == 404


def test_deleted_parent_rejects_new_replies(client, thread):
    base, original = thread
    client.delete(f"{base}/comments/{original['id']}", headers=auth_header(ADMIN))
    for author in (ADMIN, USER_A):
        response = client.post(f"{base}/comments", json={"body": "Stale reply", "reply_to_comment_id": original["id"]}, headers=auth_header(author))
        assert response.status_code == 400
        assert response.json()["detail"] == "Cannot reply to a deleted comment"
    assert len(client.get(f"{base}/comments", headers=auth_header(ADMIN)).json()) == 1


def test_deleting_status_change_comment_preserves_status_history(client, thread):
    base, _ = thread
    response = client.patch(base, json={"status": "needs_action", "comment": "Status reason"}, headers=auth_header(ADMIN))
    assert response.status_code == 200
    history = client.get(f"{base}/status-history", headers=auth_header(USER_A)).json()
    comments = client.get(f"{base}/comments", headers=auth_header(ADMIN)).json()
    assert client.delete(f"{base}/comments/{comments[-1]['id']}", headers=auth_header(ADMIN)).status_code == 204
    assert client.get(base, headers=auth_header(USER_A)).json()["status"] == "needs_action"
    assert client.get(f"{base}/status-history", headers=auth_header(USER_A)).json() == history

