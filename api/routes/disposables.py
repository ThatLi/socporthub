from datetime import date, time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.auth import get_current_user
from api.database import get_db
from api.models import DisposableRequest, Proposal, User, UserRole
from api.portfolio import admin_committee_filter, committee_portfolio
from api.schemas import DisposableRequestOut, DisposableRequestUpdate, DisposableRequestUpsert
from bot.notifications import (
    notify_admins_new_disposable_request,
    notify_user_disposable_approved,
    notify_user_disposable_rejected,
)
from api.routes.proposals import get_visible_proposal

router = APIRouter(prefix="/api/disposables", tags=["disposables"])


def _to_out(d: DisposableRequest) -> DisposableRequestOut:
    proposal_title = d.proposal.title if d.proposal else (d.request_title or "Disposable request")
    committee_name = d.proposal.committee.name if d.proposal else (d.requester_cca or "—")
    return DisposableRequestOut(
        id=d.id,
        proposal_id=d.proposal_id,
        proposal_title=proposal_title,
        committee_name=committee_name,
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
        rejected=d.rejected,
        requester_cca=d.requester_cca,
        request_title=d.request_title,
        description=d.description,
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
        query = query.outerjoin(DisposableRequest.proposal).outerjoin(Proposal.committee)
        # Standalone requests are deliberately visible to admins for vetting;
        # proposal-backed requests retain the existing portfolio restriction.
        query = query.filter((DisposableRequest.proposal_id.is_(None)) | admin_committee_filter(user))
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
        .outerjoin(DisposableRequest.proposal).outerjoin(Proposal.committee)
        .filter(DisposableRequest.approved.is_(True), DisposableRequest.collection_date == date.today())
        .filter((DisposableRequest.proposal_id.is_(None)) | admin_committee_filter(admin))
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
    proposal = get_visible_proposal(db, user, proposal_id)
    if proposal.submitted_by != user.id and user.role != UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the proposal owner can request disposables")

    existing = db.query(DisposableRequest).filter(DisposableRequest.proposal_id == proposal_id).first()
    if existing and existing.approved and user.role != UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Approved requests can only be changed by an admin")

    is_new = existing is None
    disposable = existing or DisposableRequest(proposal_id=proposal_id, requested_by=user.id)
    disposable.plates = req.plates
    disposable.cups = req.cups
    disposable.bowls = req.bowls
    disposable.forks = req.forks
    disposable.spoons = req.spoons
    disposable.collection_date = req.collection_date
    disposable.description = req.description
    disposable.rejected = False
    try:
        disposable.collection_time = time.fromisoformat(req.collection_time) if req.collection_time else None
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "collection_time must use HH:MM format") from exc
    if not is_new and user.role != UserRole.admin:
        disposable.approved = False  # editing a request re-opens it for admin review

    db.add(disposable)
    db.commit()
    db.refresh(disposable)

    if is_new:
        await notify_admins_new_disposable_request(
            proposal.title, user.display_name or user.email, committee_portfolio(proposal.committee),
        )

    return _to_out(disposable)


@router.patch("/{disposable_id}", response_model=DisposableRequestOut)
async def update_disposable(
    disposable_id: int,
    req: DisposableRequestUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_user),
) -> DisposableRequestOut:
    if admin.role != UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")

    disposable = db.get(DisposableRequest, disposable_id)
    if not disposable:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Disposable request not found")

    disposable.approved = req.approved
    disposable.rejected = not req.approved
    db.commit()
    db.refresh(disposable)

    request_label = disposable.proposal.title if disposable.proposal else (disposable.request_title or "your disposable request")
    if req.approved:
        await notify_user_disposable_approved(
            disposable.requester.telegram_id, request_label, disposable.collection_date.isoformat()
        )
    else:
        await notify_user_disposable_rejected(disposable.requester.telegram_id, request_label)

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
    if not disposable:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Disposable request not found")

    db.delete(disposable)
    db.commit()
