import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from api.config import get_settings
from api.database import get_db
from api.main import app
from api.models import Committee, GradingNotification, Portfolio, Proposal, ProposalCategory, ProposalGrading, ProposalStatus, ProposalStatusHistory, User, UserCommittee, UserRole, UserStatus
from api.services.grading import RUBRICS, aware, process_grading_notifications, utcnow
from tests.conftest import auth_header


def session():
    return contextmanager(app.dependency_overrides[get_db])()


@pytest.fixture
def grading_client(client, monkeypatch):
    settings = get_settings()
    for name, value in {
        "grading_enabled": True, "google_drive_mode": "disabled",
        "social_admin_telegram_ids": "999", "welfare_admin_telegram_ids": "998",
        "grading_remind_admins": True,
    }.items():
        monkeypatch.setattr(settings, name, value)
    # Fake only the notification sender's credentials; auth remains in dev mode.
    monkeypatch.setattr("api.services.grading.get_settings", lambda: settings.model_copy(update={"telegram_bot_token": "test-token"}))
    monkeypatch.setattr("api.services.grading.send_message", AsyncMock())
    with session() as db:
        social = db.query(Committee).first()
        social.portfolio = Portfolio.social
        welfare = Committee(name="Welfare Comm", portfolio=Portfolio.welfare, color="#123456")
        db.add(welfare)
        db.flush()
        for tid, role, committee in [(999, UserRole.admin, social), (998, UserRole.admin, welfare), (1001, UserRole.user, social), (1002, UserRole.user, welfare), (1003, UserRole.user, social)]:
            user = User(telegram_id=tid, email=f"u{tid}@example.com", display_name=f"User {tid}", role=role, status=UserStatus.approved)
            db.add(user)
            db.flush()
            db.add(UserCommittee(user_id=user.id, committee_id=committee.id))
        db.commit()
    return client


def proposal(category="event", tid=1001, status=ProposalStatus.finished):
    with session() as db:
        user = db.query(User).filter_by(telegram_id=tid).one()
        p = Proposal(title="Test grading", category=ProposalCategory(category), submitted_by=user.id, committee_id=next(iter(user.committee_ids)), status=status)
        db.add(p)
        db.commit()
        return p.id


def complete(category="event", score=8):
    rubric = RUBRICS[category]
    return {"submit": True, "selections": rubric.get("options", [])[:1], "ratings": {field: {"score": score, "justification": f"Evidence for {field}"} for field in rubric["fields"]}}


def start(client, pid, admin=999):
    return client.post(f"/api/proposals/{pid}/grading/start", headers=auth_header(admin))


def save(client, pid, data, user=1001):
    return client.put(f"/api/proposals/{pid}/grading", json=data, headers=auth_header(user))


def test_lifecycle_drafts_admin_privacy_and_final(grading_client):
    c = grading_client
    pid = proposal()
    opened = start(c, pid)
    assert opened.status_code == 200, opened.text
    assert opened.json()["status"] == "grading"
    assert opened.json()["drive_url"] is None
    with session() as db:
        grading = db.get(ProposalGrading, pid)
        assert grading.deadline - grading.started_at == timedelta(days=14)
        assert {n.telegram_id for n in db.query(GradingNotification).all()} == {999, 1001}
    assert start(c, pid).status_code == 409
    partial = {"ratings": {"food": {"score": 0, "justification": "No food was provided"}}}
    assert save(c, pid, partial).status_code == 200
    admin_view = c.get(f"/api/proposals/{pid}/grading", headers=auth_header(999)).json()
    assert admin_view["user_assessment"] is None
    assert save(c, pid, partial, 999).status_code == 200
    assert c.get(f"/api/proposals/{pid}/grading", headers=auth_header(1001)).json()["admin_assessment"] is None
    assert save(c, pid, complete(), 999).status_code == 409
    final = save(c, pid, complete())
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "final"
    assert save(c, pid, partial).status_code == 409
    admin_view = c.get(f"/api/proposals/{pid}/grading", headers=auth_header(999)).json()
    assert admin_view["user_assessment"]["ratings"]["food"]["score"] == 8
    assert admin_view["admin_assessment"]["ratings"]["food"]["score"] == 0
    assert save(c, pid, complete(score=7), 999).status_code == 200
    assert save(c, pid, complete(), 999).status_code == 409
    visible = c.get(f"/api/proposals/{pid}/grading", headers=auth_header(1001)).json()
    assert visible["admin_assessment"]["ratings"]["food"]["score"] == 7
    with session() as db:
        transitions = db.query(ProposalStatusHistory).filter_by(proposal_id=pid).order_by(ProposalStatusHistory.id).all()
        assert [(h.from_status.value, h.to_status.value) for h in transitions] == [("finished", "grading"), ("grading", "final")]
        notices = db.query(GradingNotification).filter_by(milestone="submitted").all()
        assert [n.telegram_id for n in notices] == [999]


def test_portfolio_and_owner_access(grading_client):
    c = grading_client
    pid = proposal()
    assert start(c, pid, 1001).status_code == 403
    assert start(c, pid, 998).status_code == 404
    assert start(c, pid).status_code == 200
    assert c.get(f"/api/proposals/{pid}/grading", headers=auth_header(998)).status_code == 404
    assert save(c, pid, complete(), 998).status_code == 404
    assert save(c, pid, complete(), 1002).status_code == 404
    peer_view = c.get(f"/api/proposals/{pid}/grading", headers=auth_header(1003))
    assert peer_view.status_code == 200
    assert peer_view.json()["can_grade"] is True
    assert save(c, pid, {"ratings": {"food": {"score": 6, "justification": "Peer review draft"}}}, 1003).status_code == 200
    welfare = proposal("initiative", tid=1002)
    assert start(c, welfare, 999).status_code == 404
    assert start(c, welfare, 998).status_code == 200
    assert save(c, welfare, complete("initiative"), 1002).status_code == 200
    with session() as db:
        notices = db.query(GradingNotification).filter_by(proposal_id=welfare).all()
        assert {n.telegram_id for n in notices} == {998, 1002}


def test_same_committee_peer_can_submit_admin_grade(grading_client):
    c = grading_client
    pid = proposal(tid=1001)
    assert start(c, pid).status_code == 200
    self_assessment = save(c, pid, complete(), 1001)
    assert self_assessment.status_code == 200, self_assessment.text
    peer_grade = save(c, pid, complete(score=9), 1003)
    assert peer_grade.status_code == 200, peer_grade.text
    assert peer_grade.json()["admin_submitted_at"] is not None
    with session() as db:
        peer = db.query(User).filter_by(telegram_id=1003).one()
        grading = db.get(ProposalGrading, pid)
        assert grading.admin_author_id == peer.id


def test_same_committee_peer_can_mark_evidence_done(grading_client):
    c = grading_client
    pid = proposal(tid=1001)
    assert start(c, pid).status_code == 200
    with session() as db:
        p = db.get(Proposal, pid)
        p.drive_ready = True
        p.drive_folder_id = "folder-id"
        db.commit()
    response = c.post(f"/api/proposals/{pid}/grading/evidence/done", headers=auth_header(1003))
    assert response.status_code == 200, response.text
    assert response.json()["evidence_done_at"] is not None


@pytest.mark.parametrize("category", list(RUBRICS))
@pytest.mark.parametrize("score", [0, 10])
def test_every_rubric_and_boundary_scores(grading_client, category, score):
    pid = proposal(category)
    assert start(grading_client, pid).status_code == 200
    payload = complete(category, score)
    if RUBRICS[category].get("multiple"):
        payload["selections"] = RUBRICS[category]["options"]
    response = save(grading_client, pid, payload)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("score", [-1, 11, 2.5, True, "8"])
def test_invalid_scores_rejected_even_in_drafts(grading_client, score):
    pid = proposal()
    start(grading_client, pid)
    assert save(grading_client, pid, {"ratings": {"food": {"score": score}}}).status_code == 422


def test_validation_and_transition_bypasses(grading_client):
    c = grading_client
    pid = proposal("welfare")
    assert save(c, pid, complete("welfare")).status_code == 409
    start(c, pid)
    assert save(c, pid, {"submit": True}).status_code == 400
    payload = complete("welfare")
    payload["selections"] = ["food", "gifts"]
    assert save(c, pid, payload).status_code == 400
    payload["selections"] = ["unknown"]
    assert save(c, pid, payload).status_code == 400
    payload = complete("welfare")
    payload["ratings"]["quality"]["justification"] = "  "
    assert save(c, pid, payload).status_code == 400
    assert c.patch(f"/api/proposals/{pid}", json={"status": "final"}, headers=auth_header(999)).status_code == 400
    assert c.patch(f"/api/proposals/{pid}", json={"category": "event"}, headers=auth_header(999)).status_code == 409
    assert start(c, proposal(status=ProposalStatus.in_review)).status_code == 409
    assert start(c, proposal("merch")).status_code == 400


@pytest.mark.parametrize("days", [7, 3, 1])
def test_reminders_are_durable_scoped_and_stop_at_final(grading_client, days):
    pid = proposal()
    start(grading_client, pid)
    with session() as db:
        grading = db.get(ProposalGrading, pid)
        deadline = aware(grading.deadline)
        when = deadline - timedelta(days=days) + timedelta(minutes=1)
        asyncio.run(process_grading_notifications(db, now=when))
        asyncio.run(process_grading_notifications(db, now=when))
    with session() as db:  # Simulate restart with a fresh session.
        asyncio.run(process_grading_notifications(db, now=when))
        notices = db.query(GradingNotification).filter_by(proposal_id=pid, milestone=str(days)).all()
        assert len(notices) == 2
        assert {n.telegram_id for n in notices} == {999, 1001}
        assert all(n.sent_at for n in notices)
        db.get(Proposal, pid).status = ProposalStatus.final
        db.commit()
        count = db.query(GradingNotification).count()
        asyncio.run(process_grading_notifications(db, now=deadline - timedelta(hours=12)))
        assert db.query(GradingNotification).count() == count


def test_admin_can_extend_deadline_and_stale_reminders_are_cleared(grading_client):
    c = grading_client
    pid = proposal()
    start(c, pid)
    with session() as db:
        grading = db.get(ProposalGrading, pid)
        old_deadline = aware(grading.deadline)
        when = old_deadline - timedelta(days=3) + timedelta(minutes=1)
        asyncio.run(process_grading_notifications(db, now=when))
        assert db.query(GradingNotification).filter_by(proposal_id=pid, milestone="3").count() == 2

    # Non-admin cannot extend.
    assert c.post(f"/api/proposals/{pid}/grading/extend", json={"deadline": "2099-01-01"}, headers=auth_header(1001)).status_code == 403

    # Must move strictly later than the current deadline.
    earlier = (old_deadline - timedelta(days=1)).date().isoformat()
    assert c.post(f"/api/proposals/{pid}/grading/extend", json={"deadline": earlier}, headers=auth_header(999)).status_code == 400

    later = (old_deadline + timedelta(days=7)).date().isoformat()
    resp = c.post(f"/api/proposals/{pid}/grading/extend", json={"deadline": later}, headers=auth_header(999))
    assert resp.status_code == 200
    assert datetime.fromisoformat(resp.json()["deadline"]) > old_deadline

    with session() as db:
        # Stale 3-day reminders from the old deadline are gone so they can be
        # recomputed (and fire again) relative to the new one.
        assert db.query(GradingNotification).filter_by(proposal_id=pid, milestone="3").count() == 0

    # Once the self-assessment is submitted, the deadline can no longer move.
    save(c, pid, complete())
    assert c.post(f"/api/proposals/{pid}/grading/extend", json={"deadline": "2099-01-01"}, headers=auth_header(999)).status_code == 409


def test_failed_delivery_retries_and_admin_copy_can_be_disabled(grading_client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "grading_remind_admins", False)
    pid = proposal()
    start(grading_client, pid)
    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
    sender = AsyncMock(side_effect=RuntimeError("provider unavailable"))
    monkeypatch.setattr("api.services.grading.send_message", sender)
    with session() as db:
        deadline = aware(db.get(ProposalGrading, pid).deadline)
        when = deadline - timedelta(days=3) + timedelta(minutes=1)
        asyncio.run(process_grading_notifications(db, now=when))
        notice = db.query(GradingNotification).filter_by(milestone="3").one()
        assert notice.telegram_id == 1001 and notice.sent_at is None
        sender.side_effect = None
        asyncio.run(process_grading_notifications(db, now=when))
        db.refresh(notice)
        assert notice.sent_at is not None
        assert sender.await_count == 2


def test_pubs_creation_and_grading_feature_flag(grading_client, monkeypatch):
    c = grading_client
    response = c.post("/api/proposals", headers=auth_header(1001), json={"title": "Pubs campaign", "category": "pubs", "save_draft": True})
    assert response.status_code == 201
    assert c.post("/api/proposals", headers=auth_header(1002), json={"title": "Pubs campaign", "category": "pubs", "save_draft": True}).status_code == 400
    pid = proposal()
    monkeypatch.setattr(get_settings(), "grading_enabled", False)
    assert start(c, pid).status_code == 404
    assert c.get(f"/api/proposals/{pid}", headers=auth_header(999)).json()["grading_available"] is False


def test_legacy_welfare_committee_is_not_visible_to_social_admin(grading_client):
    pid = proposal("initiative", tid=1002)
    with session() as db:
        db.get(Proposal, pid).committee.portfolio = None
        db.commit()
    assert start(grading_client, pid, 999).status_code == 404
    assert start(grading_client, pid, 998).status_code == 200


def test_drive_folder_pdf_and_permissions_reused_on_retry(grading_client, monkeypatch):
    import httpx
    from api.services import google_drive
    settings = get_settings()
    monkeypatch.setattr(settings, "google_drive_mode", "live")
    monkeypatch.setattr(settings, "google_drive_social_parent_folder_id", "social-parent")
    monkeypatch.setattr(google_drive, "_access_token", lambda: "test-token")
    monkeypatch.setattr(google_drive, "download_google_doc_pdf", AsyncMock(return_value=("proposal.pdf", b"%PDF-test")))
    requests = []
    uploaded = False
    permissions = []

    def handle(request):
        nonlocal uploaded
        requests.append(request)
        path = request.url.path
        if path == "/drive/v3/files" and request.method == "GET":
            return httpx.Response(200, json={"files": []})
        if path.endswith("/permissions"):
            if request.method == "POST":
                permissions.append({"emailAddress": "u1001@example.com", "role": "writer"})
            return httpx.Response(200, json={"permissions": permissions})
        if path.endswith("/files/pdf-id"):
            return httpx.Response(200 if uploaded else 404, json={"id": "pdf-id"})
        if path.startswith("/upload"):
            uploaded = True
            assert b"%PDF-test" in request.content
            return httpx.Response(200, json={"id": "pdf-id"})
        assert request.method == "POST" and path.endswith("/files")
        import json
        metadata = json.loads(request.content)
        assert metadata["name"] == "Block 2-Test grading"
        assert metadata["parents"] == ["social-parent"]
        assert request.url.params["supportsAllDrives"] == "true"
        return httpx.Response(200, json={"id": "folder-id"})

    client_class = httpx.AsyncClient
    monkeypatch.setattr(google_drive.httpx, "AsyncClient", lambda **kwargs: client_class(transport=httpx.MockTransport(handle), **kwargs))
    pid = proposal()
    with session() as db:
        db.get(Proposal, pid).doc_link = "https://docs.google.com/document/d/test/edit"
        db.commit()
        asyncio.run(google_drive.provision_evidence(db, pid))
        asyncio.run(google_drive.provision_evidence(db, pid))
        p = db.get(Proposal, pid)
        assert p.drive_ready and p.drive_error is None
        assert google_drive.folder_url(p) == "https://drive.google.com/drive/folders/folder-id"
    assert sum(r.method == "GET" and r.url.path == "/drive/v3/files" for r in requests) >= 2
    assert sum(r.method == "POST" and r.url.path == "/drive/v3/files" for r in requests) == 1
    assert sum(r.url.path.startswith("/upload") for r in requests) == 1
    assert sum(r.method == "POST" and r.url.path.endswith("permissions") for r in requests) == 1


def test_drive_timeout_reconciles_created_folder_on_retry(grading_client, monkeypatch):
    import httpx
    from api.services import google_drive
    settings = get_settings()
    monkeypatch.setattr(settings, "google_drive_mode", "live")
    monkeypatch.setattr(settings, "google_drive_social_parent_folder_id", "social-parent")
    monkeypatch.setattr(google_drive, "_access_token", lambda: "test-token")
    attempts = 0

    def handle(request):
        nonlocal attempts
        if request.url.path == "/drive/v3/files" and request.method == "GET":
            return httpx.Response(200, json={"files": [{"id": "reserved-folder", "mimeType": "application/vnd.google-apps.folder"}]} if attempts else {"files": []})
        if request.url.path.endswith("permissions"):
            return httpx.Response(200, json={"permissions": [{"emailAddress": "u1001@example.com", "role": "writer"}]})
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("Lost response after creation", request=request)
        return httpx.Response(409, json={"error": "Already created"})

    client_class = httpx.AsyncClient
    monkeypatch.setattr(google_drive.httpx, "AsyncClient", lambda **kwargs: client_class(transport=httpx.MockTransport(handle), **kwargs))
    pid = proposal()
    response = start(grading_client, pid)
    assert response.status_code == 200 and response.json()["status"] == "grading"
    assert response.json()["drive_error"]
    with session() as db:
        assert db.get(Proposal, pid).drive_folder_id is None
        asyncio.run(google_drive.provision_evidence(db, pid))
        assert db.get(Proposal, pid).drive_ready
        assert db.get(Proposal, pid).drive_error is None


def test_grading_migration_preserves_existing_proposal():
    import importlib.util
    from pathlib import Path
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).resolve().parents[1] / "migrations/versions/0021_add_grading.py"
    spec = importlib.util.spec_from_file_location("grading_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        connection.execute(sa.text("CREATE TABLE proposals (id INTEGER PRIMARY KEY, title VARCHAR(255))"))
        connection.execute(sa.text("INSERT INTO proposals VALUES (1, 'Existing proposal')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        row = connection.execute(sa.text("SELECT title, drive_ready FROM proposals WHERE id = 1")).one()
        assert row == ("Existing proposal", 0)
        assert "proposal_gradings" in sa.inspect(connection).get_table_names()
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert connection.execute(sa.text("SELECT title FROM proposals")).scalar() == "Existing proposal"
