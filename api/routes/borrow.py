"""Admin view of the borrowable tables and cambro: where everything is, plus decisions."""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session, undefer

from api.auth import get_current_admin
from api.database import get_db
from api.models import BorrowRequest, BorrowStatus, User
from api.schemas import (
    BorrowDecisionRequest,
    BorrowRequestOut,
    BorrowReturnDecision,
    BorrowSummaryOut,
)
from api.services import borrowing
from bot.notifications import (
    notify_user_borrow_approved,
    notify_user_borrow_rejected,
    notify_user_return_confirmed,
    notify_user_return_not_confirmed,
)

router = APIRouter(prefix="/api/borrow", tags=["borrow"])


def _to_out(r: BorrowRequest, today=None) -> BorrowRequestOut:
    return BorrowRequestOut(
        id=r.id,
        committee_id=r.committee_id,
        committee_name=r.committee.name,
        requested_by=r.requested_by,
        requester_name=r.requester.display_name,
        requester_telegram_username=r.requester.telegram_username,
        activity_name=r.activity_name,
        description=r.description,
        tables=r.tables,
        cambro=r.cambro,
        borrow_date=r.borrow_date,
        return_date=r.return_date,
        status=r.status,
        rejection_reason=r.rejection_reason,
        has_proof=r.has_proof,
        overdue=borrowing.is_overdue(r, today),
        return_submitted_at=r.return_submitted_at,
        returned_at=r.returned_at,
        created_at=r.created_at,
    )


@router.get("/summary", response_model=BorrowSummaryOut)
def get_summary(db: Session = Depends(get_db), _: User = Depends(get_current_admin)):
    return borrowing.summary(db)


@router.get("", response_model=list[BorrowRequestOut])
def list_borrow_requests(
    status_filter: BorrowStatus | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    query = db.query(BorrowRequest)
    if status_filter:
        query = query.filter(BorrowRequest.status == status_filter)
    today = borrowing.app_today()
    rows = query.order_by(BorrowRequest.borrow_date.desc(), BorrowRequest.id.desc()).limit(200).all()
    return [_to_out(r, today) for r in rows]


@router.post("/{request_id}/decision", response_model=BorrowRequestOut)
async def decide(
    request_id: int,
    body: BorrowDecisionRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    decision = BorrowStatus(body.status)
    request, changed = borrowing.decide_borrow(db, admin, request_id, decision, body.reason)
    if changed:
        telegram_id = request.requester.telegram_id
        if decision == BorrowStatus.approved:
            await notify_user_borrow_approved(
                telegram_id, request.activity_name, borrowing.describe_items(request), request.return_date.isoformat()
            )
        else:
            await notify_user_borrow_rejected(telegram_id, request.activity_name, request.rejection_reason)
    return _to_out(request)


@router.post("/{request_id}/return", response_model=BorrowRequestOut)
async def decide_return(
    request_id: int,
    body: BorrowReturnDecision,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    request, changed = borrowing.decide_return(db, admin, request_id, body.confirmed)
    if changed:
        telegram_id = request.requester.telegram_id
        if body.confirmed:
            await notify_user_return_confirmed(telegram_id, request.activity_name)
        else:
            await notify_user_return_not_confirmed(telegram_id, request.id, request.activity_name)
    return _to_out(request)


@router.get("/{request_id}/proof")
def get_proof(request_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_admin)):
    request = (
        db.query(BorrowRequest)
        .options(undefer(BorrowRequest.return_proof_data))
        .filter(BorrowRequest.id == request_id)
        .first()
    )
    if not request or not request.return_proof_data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No return photo stored for this request")
    content_type = request.return_proof_content_type or "image/jpeg"
    if not content_type.startswith("image/"):
        content_type = "application/octet-stream"
    return Response(
        content=request.return_proof_data,
        media_type=content_type,
        headers={"Content-Disposition": f'inline; filename="return-proof-{request.id}"', "X-Content-Type-Options": "nosniff"},
    )
