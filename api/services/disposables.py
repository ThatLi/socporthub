from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from api.models import DisposableRequest, DisposableStatus, User, UserRole, UserStatus
from api.portfolio import admin_can_access_committee


def decide_disposable(
    db: Session,
    admin: User,
    disposable_id: int,
    decision: DisposableStatus,
    reason: str | None = None,
) -> tuple[DisposableRequest, bool]:
    if admin.role != UserRole.admin or admin.status != UserStatus.approved:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    if decision not in (DisposableStatus.approved, DisposableStatus.rejected):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Decision must be approved or rejected")

    disposable = (
        db.query(DisposableRequest)
        .filter(DisposableRequest.id == disposable_id)
        .with_for_update()
        .first()
    )
    if not disposable or not admin_can_access_committee(admin, disposable.committee):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Disposable request not found")
    if disposable.status == decision:
        return disposable, False
    if disposable.status != DisposableStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, "Disposable request has already been decided")

    disposable.status = decision
    disposable.rejection_reason = reason.strip() if decision == DisposableStatus.rejected and reason else None
    db.commit()
    db.refresh(disposable)
    return disposable, True
