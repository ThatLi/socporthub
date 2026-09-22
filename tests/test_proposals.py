from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from api.database import get_db
from api.main import app
from api.models import Proposal, ProposalComment, ProposalStatusHistory
from tests.conftest import auth_header

ADMIN = 999
USER_A = 1001
USER_B = 1002


def session():
    return contextmanager(app.dependency_overrides[get_db])()


def register(client, telegram_id, email):
    return client.post(
        "/api/auth/register",
        json={"email": email, "display_name": email.split("@")[0]},
        headers=auth_header(telegram_id),
    )


def test_admin_auto_approved_on_registration(client):
    resp = register(client, ADMIN, "admin@example.com")
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"
    assert resp.json()["status"] == "approved"


def test_regular_user_starts_pending_and_is_blocked_until_approved(client):
    register(client, ADMIN, "admin@example.com")
    register(client, USER_A, "a@example.com")

    resp = client.get("/api/proposals", headers=auth_header(USER_A))
    assert resp.status_code == 403  # not approved yet

    users = client.get("/api/admin/users", headers=auth_header(ADMIN)).json()
    user_a_id = next(u["id"] for u in users if u["telegram_id"] == USER_A)
    committees = client.get("/api/committees", headers=auth_header(ADMIN)).json()

    approve = client.patch(
        f"/api/admin/users/{user_a_id}",
        json={"status": "approved", "committee_ids": [committees[0]["id"]]},
        headers=auth_header(ADMIN),
    )
    assert approve.status_code == 200
    assert approve.json()["status"] == "approved"

    resp = client.get("/api/proposals", headers=auth_header(USER_A))
    assert resp.status_code == 200
    assert resp.json() == []


def _approve_user(client, telegram_id, email, committee_index=0):
    register(client, telegram_id, email)
    users = client.get("/api/admin/users", headers=auth_header(ADMIN)).json()
    user_id = next(u["id"] for u in users if u["telegram_id"] == telegram_id)
    committees = client.get("/api/committees", headers=auth_header(ADMIN)).json()
    client.patch(
        f"/api/admin/users/{user_id}",
        json={"status": "approved", "committee_ids": [committees[committee_index]["id"]]},
        headers=auth_header(ADMIN),
    )
    return user_id


def test_proposal_lifecycle_and_status_guardrails(client):
    register(client, ADMIN, "admin@example.com")
    _approve_user(client, USER_A, "a@example.com")

    create = client.post(
        "/api/proposals",
        json={"title": "Movie Night", "description": "Fun", "event_date": "2026-10-01"},
        headers=auth_header(USER_A),
    )
    assert create.status_code == 201
    proposal_id = create.json()["id"]
    # Submitting (not saving as draft) goes straight to in_review — needs_action is
    # reached only when an admin sends it back, not a step every submission passes through.
    assert create.json()["status"] == "in_review"

    # Illegal skip-ahead transition rejected.
    bad = client.patch(
        f"/api/proposals/{proposal_id}",
        json={"status": "finished"},
        headers=auth_header(ADMIN),
    )
    assert bad.status_code == 400

    # Owner can't advance past in_review themselves — that's the admin's call.
    forbidden = client.patch(
        f"/api/proposals/{proposal_id}",
        json={"status": "submitted"},
        headers=auth_header(USER_A),
    )
    assert forbidden.status_code == 403

    # Admin sends it back for more detail.
    sent_back = client.patch(
        f"/api/proposals/{proposal_id}",
        json={"status": "needs_action", "comment": "Please add more detail"},
        headers=auth_header(ADMIN),
    )
    assert sent_back.status_code == 200
    assert sent_back.json()["status"] == "needs_action"

    # Owner can edit again while needs_action.
    edited = client.patch(
        f"/api/proposals/{proposal_id}",
        json={"title": "Movie Night (updated)"},
        headers=auth_header(USER_A),
    )
    assert edited.status_code == 200

    # Owner resubmits themselves — lands back on in_review.
    resubmitted = client.patch(
        f"/api/proposals/{proposal_id}",
        json={"status": "in_review"},
        headers=auth_header(USER_A),
    )
    assert resubmitted.status_code == 200
    assert resubmitted.json()["status"] == "in_review"

    # Owner can no longer edit content once back in in_review.
    edit_blocked = client.patch(
        f"/api/proposals/{proposal_id}",
        json={"title": "Hacked"},
        headers=auth_header(USER_A),
    )
    assert edit_blocked.status_code == 403


def test_status_change_and_new_comment_bump_proposal_to_top(client):
    register(client, ADMIN, "admin@example.com")
    _approve_user(client, USER_A, "a@example.com")

    older = client.post(
        "/api/proposals",
        json={"title": "Older Event", "description": "First", "event_date": "2026-10-01"},
        headers=auth_header(USER_A),
    ).json()["id"]
    newer = client.post(
        "/api/proposals",
        json={"title": "Newer Event", "description": "Second", "event_date": "2026-10-02"},
        headers=auth_header(USER_A),
    ).json()["id"]

    # SQLite's CURRENT_TIMESTAMP only has 1-second resolution, so pin explicit,
    # well-separated timestamps rather than relying on real wall-clock gaps between
    # these calls.
    now = datetime.now(timezone.utc)
    with session() as db:
        db.get(Proposal, older).created_at = now - timedelta(days=2)
        db.get(Proposal, newer).created_at = now - timedelta(days=1)
        db.commit()

    # Freshly created: newest-created proposal leads.
    listing = client.get("/api/proposals", headers=auth_header(ADMIN)).json()
    assert [p["id"] for p in listing] == [newer, older]

    # A status change on the older proposal should bump it back to the top.
    sent_back = client.patch(
        f"/api/proposals/{older}",
        json={"status": "needs_action", "comment": "Please add more detail"},
        headers=auth_header(ADMIN),
    )
    assert sent_back.status_code == 200
    with session() as db:
        history = (
            db.query(ProposalStatusHistory)
            .filter_by(proposal_id=older)
            .order_by(ProposalStatusHistory.id.desc())
            .first()
        )
        history.created_at = now
        db.commit()
    listing = client.get("/api/proposals", headers=auth_header(ADMIN)).json()
    assert [p["id"] for p in listing] == [older, newer]

    # A new comment on the (now second) newer proposal should bump it back up.
    commented = client.post(
        f"/api/proposals/{newer}/comments",
        json={"body": "Any update?"},
        headers=auth_header(ADMIN),
    )
    assert commented.status_code == 201
    with session() as db:
        comment = (
            db.query(ProposalComment)
            .filter_by(proposal_id=newer)
            .order_by(ProposalComment.id.desc())
            .first()
        )
        comment.created_at = now + timedelta(minutes=1)
        db.commit()
    listing = client.get("/api/proposals", headers=auth_header(ADMIN)).json()
    assert [p["id"] for p in listing] == [newer, older]


def test_admin_can_clear_event_date_after_finished(client):
    register(client, ADMIN, "admin@example.com")
    _approve_user(client, USER_A, "a@example.com")

    create = client.post(
        "/api/proposals",
        json={"title": "Movie Night", "event_date": "2026-10-01"},
        headers=auth_header(USER_A),
    )
    proposal_id = create.json()["id"]
    assert create.json()["status"] == "in_review"

    client.patch(f"/api/proposals/{proposal_id}", json={"status": "submitted"}, headers=auth_header(ADMIN))
    finish = client.patch(f"/api/proposals/{proposal_id}", json={"status": "finished"}, headers=auth_header(ADMIN))
    assert finish.json()["event_date"] == "2026-10-01"

    # Omitting event_date entirely leaves it untouched.
    untouched = client.patch(f"/api/proposals/{proposal_id}", json={"description": "note"}, headers=auth_header(ADMIN))
    assert untouched.json()["event_date"] == "2026-10-01"

    # Admin can edit content fields even after finished, and explicit null clears it.
    cleared = client.patch(f"/api/proposals/{proposal_id}", json={"event_date": None}, headers=auth_header(ADMIN))
    assert cleared.status_code == 200
    assert cleared.json()["event_date"] is None

    # title is NOT NULL at the DB level — clearing it is rejected, not a 500.
    title_clear = client.patch(f"/api/proposals/{proposal_id}", json={"title": None}, headers=auth_header(ADMIN))
    assert title_clear.status_code == 400


def test_draft_submission_flow(client):
    register(client, ADMIN, "admin@example.com")
    _approve_user(client, USER_A, "a@example.com")

    draft = client.post(
        "/api/proposals",
        json={"title": "Draft idea", "save_draft": True},
        headers=auth_header(USER_A),
    )
    assert draft.status_code == 201
    proposal_id = draft.json()["id"]
    assert draft.json()["status"] == "draft"

    # Admin's list still includes it server-side — the admin dashboard hides drafts
    # client-side, but this endpoint has no reason to lie about what exists.
    admin_list = client.get("/api/proposals", headers=auth_header(ADMIN)).json()
    assert any(p["id"] == proposal_id for p in admin_list)

    # Owner submits the draft themselves — lands on in_review, not needs_action.
    submitted = client.patch(
        f"/api/proposals/{proposal_id}",
        json={"status": "in_review"},
        headers=auth_header(USER_A),
    )
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "in_review"


def test_committee_scoped_visibility(client):
    register(client, ADMIN, "admin@example.com")
    _approve_user(client, USER_A, "a@example.com", committee_index=0)
    _approve_user(client, USER_B, "b@example.com", committee_index=1)

    client.post(
        "/api/proposals",
        json={"title": "Block 2 Event"},
        headers=auth_header(USER_A),
    )

    # User B is in a different committee and shouldn't see A's proposal.
    visible_to_b = client.get("/api/proposals", headers=auth_header(USER_B)).json()
    assert visible_to_b == []

    # Admin sees everything.
    visible_to_admin = client.get("/api/proposals", headers=auth_header(ADMIN)).json()
    assert len(visible_to_admin) == 1


def test_admin_can_view_submitted_poster(client):
    register(client, ADMIN, "admin@example.com")
    _approve_user(client, USER_A, "a@example.com")

    draft = client.post(
        "/api/proposals",
        json={"title": "Poster event", "category": "event", "save_draft": True},
        headers=auth_header(USER_A),
    )
    proposal_id = draft.json()["id"]
    poster = b"fake-png-bytes"
    uploaded = client.post(
        f"/api/proposals/{proposal_id}/poster",
        files={"poster": ("event.png", poster, "image/png")},
        headers=auth_header(USER_A),
    )
    assert uploaded.status_code == 200

    response = client.get(f"/api/proposals/{proposal_id}/poster", headers=auth_header(ADMIN))
    assert response.status_code == 200
    assert response.content == poster
    assert response.headers["content-type"] == "image/png"
    assert 'inline; filename="event.png"' in response.headers["content-disposition"]
