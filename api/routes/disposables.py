from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.auth import get_current_user
from api.config import get_settings
from api.database import get_db
from api.models import DisposableRequest, DisposableStatus, User, UserRole
from api.portfolio import admin_can_access_committee, admin_committee_filter, committee_portfolio
from api.schemas import (
    DisposableDecisionRequest,
    DisposableRequestOut,
    DisposableRequestUpdate,
    DisposableRequestUpsert,
)
from api.services.disposables import decide_disposable
from bot.notifications import (
    notify_admins_new_disposable_request,
    notify_user_disposable_approved,
    notify_user_disposable_rejected,
)
from api.routes.proposals import get_visible_proposal

router = APIRouter(prefix="/api/disposables", tags=["disposables"])


def _app_today() -> date:
    return datetime.now(ZoneInfo(get_settings().app_timezone)).date()


def _to_out(d: DisposableRequest) -> DisposableRequestOut:
    return DisposableRequestOut(
        id=d.id,
        proposal_id=d.proposal_id,
        committee_id=d.committee_id,
        proposal_title=d.activity_name,
        activity_name=d.activity_name,
        description=d.description,
        committee_name=d.committee.name,
        requested_by=d.requested_by,
        requester_name=d.requester.display_name,
        plates=d.plates,
        cups=d.cups,
        bowls=d.bowls,
        forks=d.forks,
        spoons=d.spoons,
        collection_date=d.collection_date,
        collection_time=d.collection_time.isoformat() if d.collection_time else None,
        approved=d.approved,
        status=d.status,
        rejection_reason=d.rejection_reason,
        created_at=d.created_at,
    )


@router.get("", response_model=list[DisposableRequestOut])
def list_disposables(
    proposal_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[DisposableRequestOut]:
    query = db.query(DisposableRequest)
    if proposal_id is not None:
        get_visible_proposal(db, user, proposal_id)  # 404s if the proposal isn't visible to this user
        query = query.filter(DisposableRequest.proposal_id == proposal_id)
    elif user.role != UserRole.admin:
        query = query.filter(DisposableRequest.requested_by == user.id)
    else:
        query = query.join(DisposableRequest.committee).filter(admin_committee_filter(user))
    requests = query.order_by(DisposableRequest.collection_date).all()
    return [_to_out(d) for d in requests]


@router.get("/today", response_model=list[DisposableRequestOut])
def todays_collections(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_user),
) -> list[DisposableRequestOut]:
    if admin.role != UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    requests = (
        db.query(DisposableRequest)
        .join(DisposableRequest.committee)
        .filter(DisposableRequest.status == DisposableStatus.approved, DisposableRequest.collection_date == _app_today())
        .filter(admin_committee_filter(admin))
        .all()
    )
    return [_to_out(d) for d in requests]


@router.post("/{proposal_id}", response_model=DisposableRequestOut, status_code=status.HTTP_201_CREATED)
async def upsert_disposable(
    proposal_id: int,
    req: DisposableRequestUpsert,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DisposableRequestOut:
    if not any((req.plates, req.cups, req.bowls, req.forks, req.spoons)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "At least one disposable quantity must be greater than zero")
    if req.collection_date < _app_today():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Collection date cannot be in the past")
    proposal = get_visible_proposal(db, user, proposal_id)
    if proposal.submitted_by != user.id and user.role != UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the proposal owner can request disposables")

    existing = db.query(DisposableRequest).filter(DisposableRequest.proposal_id == proposal_id).first()
    if existing and existing.status == DisposableStatus.approved and user.role != UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Approved requests can only be changed by an admin")

    is_new = existing is None
    disposable = existing or DisposableRequest(
        proposal_id=proposal_id,
        committee_id=proposal.committee_id,
        requested_by=user.id,
        activity_name=proposal.title,
    )
    disposable.committee_id = proposal.committee_id
    disposable.activity_name = proposal.title
    disposable.description = req.description
    disposable.plates = req.plates
    disposable.cups = req.cups
    disposable.bowls = req.bowls
    disposable.forks = req.forks
    disposable.spoons = req.spoons
    disposable.collection_date = req.collection_date
    try:
        disposable.collection_time = datetime.strptime(req.collection_time, "%H:%M").time() if req.collection_time else None
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "collection_time must use HH:MM format") from exc
    if not is_new and user.role != UserRole.admin:
        disposable.status = DisposableStatus.pending  # editing re-opens it for admin review
        disposable.rejection_reason = None

    db.add(disposable)
    db.commit()
    db.refresh(disposable)

    if is_new:
        await notify_admins_new_disposable_request(
            proposal.title,
            user.display_name or user.email,
            committee_portfolio(proposal.committee),
            disposable.id,
        )

    return _to_out(disposable)


@router.patch("/{disposable_id}", response_model=DisposableRequestOut)
async def update_disposable(
    disposable_id: int,
    req: DisposableRequestUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_user),
) -> DisposableRequestOut:
    decision = DisposableStatus.approved if req.approved else DisposableStatus.rejected
    disposable, changed = decide_disposable(db, admin, disposable_id, decision)
    if changed and decision == DisposableStatus.approved:
        await notify_user_disposable_approved(
            disposable.requester.telegram_id, disposable.activity_name, disposable.collection_date.isoformat()
        )
    elif changed:
        await notify_user_disposable_rejected(
            disposable.requester.telegram_id, disposable.activity_name, disposable.rejection_reason
        )

    return _to_out(disposable)


@router.post("/{disposable_id}/decision", response_model=DisposableRequestOut)
async def decide_request(
    disposable_id: int,
    req: DisposableDecisionRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_user),
) -> DisposableRequestOut:
    disposable, changed = decide_disposable(db, admin, disposable_id, req.status, req.reason)
    if changed and req.status == DisposableStatus.approved:
        await notify_user_disposable_approved(
            disposable.requester.telegram_id, disposable.activity_name, disposable.collection_date.isoformat()
        )
    elif changed:
        await notify_user_disposable_rejected(
            disposable.requester.telegram_id, disposable.activity_name, disposable.rejection_reason
        )
    return _to_out(disposable)


@router.delete("/{disposable_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_disposable(
    disposable_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_user),
) -> None:
    if admin.role != UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    disposable = db.get(DisposableRequest, disposable_id)
    if not disposable or not admin_can_access_committee(admin, disposable.committee):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Disposable request not found")
    db.delete(disposable)
    db.commit()
