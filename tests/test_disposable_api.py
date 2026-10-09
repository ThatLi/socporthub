from datetime import date, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base, get_db
from api.main import app
from api.models import (
    Committee,
    DisposableRequest,
    DisposableStatus,
    Portfolio,
    User,
    UserCommittee,
    UserRole,
    UserStatus,
)
from api.routes import disposables as disposable_routes


def auth(telegram_id: int) -> dict[str, str]:
    return {"X-Telegram-Init-Data": f"dev_telegram_id={telegram_id}"}


@pytest.fixture()
def disposable_api(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    db = Session()
    social = Committee(name="Social CCA", color="#111111", portfolio=Portfolio.social)
    welfare = Committee(name="Welfare CCA", color="#222222", portfolio=Portfolio.welfare)
    requester = User(
        telegram_id=1101,
        email="requester@example.com",
        display_name="Requester",
        role=UserRole.user,
        status=UserStatus.approved,
    )
    welfare_requester = User(
        telegram_id=1102,
        email="welfare@example.com",
        display_name="Welfare Requester",
        role=UserRole.user,
        status=UserStatus.approved,
    )
    admin = User(
        telegram_id=999,
        email="admin@example.com",
        display_name="Social Admin",
        role=UserRole.admin,
        status=UserStatus.approved,
    )
    db.add_all([social, welfare, requester, welfare_requester, admin])
    db.flush()
    db.add_all([
        UserCommittee(user_id=requester.id, committee_id=social.id),
        UserCommittee(user_id=welfare_requester.id, committee_id=welfare.id),
    ])
    social_request = DisposableRequest(
        committee_id=social.id,
        requested_by=requester.id,
        activity_name="Social Night",
        description="Late-night event",
        cups=20,
        collection_date=date.today() + timedelta(days=1),
        status=DisposableStatus.pending,
    )
    welfare_request = DisposableRequest(
        committee_id=welfare.id,
        requested_by=welfare_requester.id,
        activity_name="Care Packs",
        plates=10,
        collection_date=date.today() + timedelta(days=2),
        status=DisposableStatus.pending,
    )
    db.add_all([social_request, welfare_request])
    db.commit()

    def override_get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    approved = AsyncMock()
    rejected = AsyncMock()
    monkeypatch.setattr(disposable_routes, "notify_user_disposable_approved", approved)
    monkeypatch.setattr(disposable_routes, "notify_user_disposable_rejected", rejected)
    with TestClient(app) as client:
        yield client, Session, social_request.id, welfare_request.id, approved, rejected
    app.dependency_overrides.clear()
    db.close()


def test_standalone_requests_are_serialized_and_portfolio_scoped(disposable_api):
    client, _, social_id, welfare_id, _, _ = disposable_api

    user_response = client.get("/api/disposables", headers=auth(1101))
    assert user_response.status_code == 200
    assert [item["id"] for item in user_response.json()] == [social_id]
    assert user_response.json()[0] | {
        "proposal_id": None,
        "proposal_title": "Social Night",
        "activity_name": "Social Night",
        "description": "Late-night event",
        "status": "pending",
        "approved": False,
    } == user_response.json()[0]

    admin_response = client.get("/api/disposables", headers=auth(999))
    assert admin_response.status_code == 200
    ids = {item["id"] for item in admin_response.json()}
    assert social_id in ids
    assert welfare_id not in ids


def test_decision_endpoint_is_idempotent_and_rejects_conflicts(disposable_api):
    client, Session, request_id, _, approved, _ = disposable_api

    first = client.post(
        f"/api/disposables/{request_id}/decision",
        headers=auth(999),
        json={"status": "approved"},
    )
    assert first.status_code == 200
    assert first.json()["status"] == "approved"
    assert first.json()["approved"] is True
    assert approved.await_count == 1

    duplicate = client.post(
        f"/api/disposables/{request_id}/decision",
        headers=auth(999),
        json={"status": "approved"},
    )
    assert duplicate.status_code == 200
    assert approved.await_count == 1

    conflict = client.post(
        f"/api/disposables/{request_id}/decision",
        headers=auth(999),
        json={"status": "rejected", "reason": "Too late"},
    )
    assert conflict.status_code == 409
    with Session() as db:
        assert db.get(DisposableRequest, request_id).status == DisposableStatus.approved


def test_decision_endpoint_rejects_pending_as_a_decision(disposable_api):
    client, _, request_id, _, _, _ = disposable_api
    response = client.post(
        f"/api/disposables/{request_id}/decision",
        headers=auth(999),
        json={"status": "pending"},
    )
    assert response.status_code == 400


def test_decision_rejection_reason_and_portfolio_authorization(disposable_api):
    client, Session, social_id, welfare_id, _, rejected = disposable_api

    response = client.post(
        f"/api/disposables/{social_id}/decision",
        headers=auth(999),
        json={"status": "rejected", "reason": "  Insufficient notice  "},
    )
    assert response.status_code == 200
    assert response.json()["rejection_reason"] == "Insufficient notice"
    assert rejected.await_count == 1

    forbidden_portfolio = client.post(
        f"/api/disposables/{welfare_id}/decision",
        headers=auth(999),
        json={"status": "approved"},
    )
    assert forbidden_portfolio.status_code == 404
    with Session() as db:
        assert db.get(DisposableRequest, welfare_id).status == DisposableStatus.pending


def test_legacy_approval_patch_remains_supported(disposable_api):
    client, _, request_id, _, approved, _ = disposable_api
    response = client.patch(
        f"/api/disposables/{request_id}",
        headers=auth(999),
        json={"approved": True},
    )
    assert response.status_code == 200
    assert response.json()["approved"] is True
    assert approved.await_count == 1


def test_legacy_false_patch_maps_to_rejection(disposable_api):
    client, _, request_id, _, _, rejected = disposable_api
    response = client.patch(
        f"/api/disposables/{request_id}",
        headers=auth(999),
        json={"approved": False},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["approved"] is False
    assert rejected.await_count == 1


def test_registration_rest_actions_are_portfolio_scoped(disposable_api):
    client, Session, _, _, _, _ = disposable_api
    with Session() as db:
        welfare_user_id = db.query(User).filter_by(telegram_id=1102).one().id
    response = client.patch(
        f"/api/admin/users/{welfare_user_id}",
        headers=auth(999),
        json={"status": "rejected"},
    )
    assert response.status_code == 404
