"""Rules for borrowing the shared tables and cambro: availability, decisions, return proof,
and the scheduled reminders. Used by both the Telegram flows and the web API."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from api.config import get_settings
from api.models import BorrowRequest, BorrowStatus, User, UserRole, UserStatus

# The physical inventory the owner is responsible for.
ITEM_TOTALS: dict[str, int] = {"tables": 4, "cambro": 1}
ITEM_LABELS: dict[str, str] = {"tables": "Foldable tables", "cambro": "Cambro (drink dispenser)"}

# Statuses where the items are physically (or about to be) with the borrower.
OUT_STATUSES = (BorrowStatus.approved, BorrowStatus.return_submitted)


def app_today() -> date:
    return datetime.now(ZoneInfo(get_settings().app_timezone)).date()


def describe_items(request: BorrowRequest | dict) -> str:
    tables = request["tables"] if isinstance(request, dict) else request.tables
    cambro = request["cambro"] if isinstance(request, dict) else request.cambro
    parts = []
    if tables:
        parts.append(f"{tables} foldable table{'s' if tables != 1 else ''}")
    if cambro:
        parts.append("the cambro")
    return " + ".join(parts) or "nothing"


def is_overdue(request: BorrowRequest, today: date | None = None) -> bool:
    return request.status == BorrowStatus.approved and (today or app_today()) > request.return_date


def availability(
    db: Session, start: date, end: date, exclude_id: int | None = None, today: date | None = None
) -> dict[str, int]:
    """Free quantity of each item for the window [start, end].

    Only approved items block a window. An item that is still unreturned past its return
    date keeps blocking until the owner confirms it is back, so the count reflects where
    things really are."""
    today = today or app_today()
    query = db.query(BorrowRequest).filter(BorrowRequest.status.in_(OUT_STATUSES))
    if exclude_id is not None:
        query = query.filter(BorrowRequest.id != exclude_id)
    used = {key: 0 for key in ITEM_TOTALS}
    for other in query.all():
        occupied_until = max(other.return_date, today)
        if other.borrow_date <= end and start <= occupied_until:
            used["tables"] += other.tables
            used["cambro"] += other.cambro
    return {key: max(0, total - used[key]) for key, total in ITEM_TOTALS.items()}


def shortfall(db: Session, request: BorrowRequest | dict, exclude_id: int | None = None) -> str | None:
    """Human-readable reason the request cannot be satisfied for its dates, or None."""
    get = (lambda k: request[k]) if isinstance(request, dict) else (lambda k: getattr(request, k))
    free = availability(db, get("borrow_date"), get("return_date"), exclude_id)
    problems = []
    if get("tables") > free["tables"]:
        problems.append(f"only {free['tables']} of {ITEM_TOTALS['tables']} tables are free")
    if get("cambro") > free["cambro"]:
        problems.append("the cambro is not free")
    return ("For those dates " + " and ".join(problems) + ".") if problems else None


def _require_admin(admin: User) -> None:
    if admin.role != UserRole.admin or admin.status != UserStatus.approved:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")


def _locked(db: Session, request_id: int) -> BorrowRequest:
    request = db.query(BorrowRequest).filter(BorrowRequest.id == request_id).with_for_update().first()
    if not request:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Borrow request not found")
    return request


def decide_borrow(
    db: Session, admin: User, request_id: int, decision: BorrowStatus, reason: str | None = None
) -> tuple[BorrowRequest, bool]:
    """Approve or reject a pending request. Returns (request, changed)."""
    _require_admin(admin)
    if decision not in (BorrowStatus.approved, BorrowStatus.rejected):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Decision must be approved or rejected")
    request = _locked(db, request_id)
    if request.status == decision:
        return request, False
    if request.status != BorrowStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, "Borrow request has already been decided")
    if decision == BorrowStatus.approved:
        problem = shortfall(db, request, exclude_id=request.id)
        if problem:
            raise HTTPException(status.HTTP_409_CONFLICT, problem)
    request.status = decision
    request.rejection_reason = reason.strip() if decision == BorrowStatus.rejected and reason else None
    request.decided_by = admin.id
    request.decided_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(request)
    return request, True


MAX_PROOF_BYTES = 10 * 1024 * 1024


def submit_return_proof(
    db: Session,
    user: User,
    request_id: int,
    file_id: str,
    content_type: str | None,
    data: bytes | None,
) -> BorrowRequest:
    request = _locked(db, request_id)
    if request.requested_by != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Borrow request not found")
    if request.status != BorrowStatus.approved:
        raise HTTPException(status.HTTP_409_CONFLICT, "This request is not waiting for a return photo")
    if data is not None and len(data) > MAX_PROOF_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Photo must be 10 MB or smaller")
    request.status = BorrowStatus.return_submitted
    request.return_proof_file_id = file_id
    request.return_proof_content_type = content_type
    request.return_proof_data = data
    request.return_submitted_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(request)
    return request


def decide_return(db: Session, admin: User, request_id: int, confirmed: bool) -> tuple[BorrowRequest, bool]:
    """Owner confirms the proof (returned) or sends it back (approved again, proof cleared)."""
    _require_admin(admin)
    request = _locked(db, request_id)
    target = BorrowStatus.returned if confirmed else BorrowStatus.approved
    if request.status == target:
        return request, False
    if request.status != BorrowStatus.return_submitted:
        raise HTTPException(status.HTTP_409_CONFLICT, "No return photo is waiting for confirmation")
    request.status = target
    if confirmed:
        request.returned_at = datetime.now(timezone.utc)
    else:
        request.return_proof_file_id = None
        request.return_proof_content_type = None
        request.return_proof_data = None
        request.return_submitted_at = None
    db.commit()
    db.refresh(request)
    return request, True


def out_now(db: Session, today: date | None = None) -> list[BorrowRequest]:
    today = today or app_today()
    return (
        db.query(BorrowRequest)
        .filter(BorrowRequest.status.in_(OUT_STATUSES), BorrowRequest.borrow_date <= today)
        .order_by(BorrowRequest.return_date, BorrowRequest.id)
        .all()
    )


def summary(db: Session, today: date | None = None) -> dict:
    today = today or app_today()
    out = out_now(db, today)
    return {
        "items": [
            {
                "key": key,
                "label": ITEM_LABELS[key],
                "total": total,
                "out": min(total, sum(getattr(r, key) for r in out)),
            }
            for key, total in ITEM_TOTALS.items()
        ],
        "pending": db.query(BorrowRequest).filter(BorrowRequest.status == BorrowStatus.pending).count(),
        "awaiting_confirmation": db.query(BorrowRequest)
        .filter(BorrowRequest.status == BorrowStatus.return_submitted)
        .count(),
        "overdue": sum(1 for r in out if is_overdue(r, today)),
    }


async def process_borrow_reminders(db: Session, today: date | None = None) -> None:
    """Remind borrowers the day before and on the return date, then daily once overdue,
    and send the owner(s) one digest of newly nagged overdue items. Receipts live on the
    row so a restart never double-sends."""
    from bot.notifications import notify_admins_overdue_borrows, notify_user_borrow_reminder

    today = today or app_today()
    requests = db.query(BorrowRequest).filter(BorrowRequest.status == BorrowStatus.approved).all()
    newly_overdue = []
    for request in requests:
        days_left = (request.return_date - today).days
        if days_left == 1:
            kind = "day_before"
        elif days_left == 0:
            kind = "due"
        elif days_left < 0:
            kind = "overdue"
        else:
            continue
        if request.borrow_date > today:
            continue
        if kind == "overdue":
            if request.last_reminder_kind == "overdue" and request.last_reminded_on == today:
                continue
        elif request.last_reminder_kind == kind:
            continue
        await notify_user_borrow_reminder(
            request.requester.telegram_id, request.id, request.activity_name, describe_items(request),
            request.return_date.isoformat(), kind,
        )
        request.last_reminder_kind = kind
        request.last_reminded_on = today
        db.commit()
        if kind == "overdue":
            newly_overdue.append(
                {
                    "id": request.id,
                    "activity": request.activity_name,
                    "items": describe_items(request),
                    "borrower": request.requester.display_name or request.requester.email,
                    "username": request.requester.telegram_username,
                    "return_date": request.return_date.isoformat(),
                    "days": (today - request.return_date).days,
                }
            )
    if newly_overdue:
        await notify_admins_overdue_borrows(newly_overdue)
