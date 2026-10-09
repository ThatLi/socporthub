from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from api.database import get_db
from api.models import Committee, EmailDraft, Proposal, User, UserCommittee, UserStatus
from api.portfolio import admin_can_access_committee
from api.schemas import UserOut, UserUpdateRequest
from api.routes.auth import normalize_telegram_username
from bot.notifications import notify_user_registration_approved, notify_user_registration_rejected_with_reason

router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])


def _to_user_out(user: User) -> UserOut:
    out = UserOut.model_validate(user)
    out.committees = [m.committee for m in user.committee_memberships]
    return out


@router.get("", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> list[UserOut]:
    users = db.query(User).order_by(User.created_at.desc()).all()
    users = [
        user
        for user in users
        if not user.committee_memberships
        or any(admin_can_access_committee(admin, membership.committee) for membership in user.committee_memberships)
    ]
    return [_to_user_out(u) for u in users]


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    req: UserUpdateRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> UserOut:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user.committee_memberships and not any(
        admin_can_access_committee(admin, membership.committee) for membership in user.committee_memberships
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    previous_status = user.status
    if req.email is not None and str(req.email) != user.email:
        if db.query(User).filter(User.email == str(req.email), User.id != user.id).first():
            raise HTTPException(status.HTTP_409_CONFLICT, "Email already in use")
        user.email = str(req.email)

        # Drafts cache their recipient, so keep them aligned with the user's
        # current registered address when an admin changes it.
        proposal_ids = db.query(Proposal.id).filter(Proposal.submitted_by == user.id)
        db.query(EmailDraft).filter(EmailDraft.proposal_id.in_(proposal_ids)).update(
            {EmailDraft.recipient: str(req.email)}, synchronize_session=False
        )

    if req.telegram_username is not None:
        username = normalize_telegram_username(req.telegram_username)
        existing = db.query(User).filter(User.telegram_username == username, User.id != user.id).first()
        if existing:
            raise HTTPException(status.HTTP_409_CONFLICT, "That Telegram handle is already in use")
        user.telegram_username = username

    if req.status is not None:
        user.status = req.status

    if req.committee_ids is not None:
        found = db.query(Committee).filter(Committee.id.in_(req.committee_ids)).all()
        if len(found) != len(set(req.committee_ids)):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown committee_id in list")
        if any(not admin_can_access_committee(admin, committee) for committee in found):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot assign a committee from another portfolio")
        db.query(UserCommittee).filter(UserCommittee.user_id == user.id).delete()
        for committee_id in req.committee_ids:
            db.add(UserCommittee(user_id=user.id, committee_id=committee_id))

    db.commit()
    db.refresh(user)

    if previous_status != UserStatus.approved and user.status == UserStatus.approved:
        await notify_user_registration_approved(user.telegram_id)
    elif previous_status != UserStatus.rejected and user.status == UserStatus.rejected:
        await notify_user_registration_rejected_with_reason(user.telegram_id, req.rejection_reason)

    return _to_user_out(user)
