from datetime import datetime, time as time_of_day, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.auth import get_current_user
from api.config import get_settings
from api.database import get_db
from api.models import (
    Committee,
    DisposableRequest,
    Proposal,
    Reminder,
    ReminderDirection,
    ReminderNudge,
    ReminderRecipient,
    ReminderTargetCommittee,
    ReminderTargetType,
    User,
    UserCommittee,
    UserRole,
    UserStatus,
)
from api.portfolio import admin_can_access_committee, committee_portfolio
from api.schemas import (
    ReminderBroadcastCreate,
    ReminderCommitteeOut,
    ReminderCreate,
    ReminderOut,
    ReminderRecipientOut,
    ReminderUnreadCount,
)
from bot.notifications import notify_admins_reminder, notify_user_reminder_broadcast

router = APIRouter(prefix="/api/reminders", tags=["reminders"])


def _admin_can_see_reminder(db: Session, reminder: Reminder, admin: User) -> bool:
    if admin.role != UserRole.admin:
        return False
    if reminder.target_type == ReminderTargetType.proposal and reminder.target_id:
        proposal = db.get(Proposal, reminder.target_id)
        return bool(proposal and admin_can_access_committee(admin, proposal.committee))
    if reminder.target_type == ReminderTargetType.disposable and reminder.target_id:
        disposable = db.get(DisposableRequest, reminder.target_id)
        return bool(disposable and admin_can_access_committee(admin, disposable.proposal.committee))
    return any(admin_can_access_committee(admin, m.committee) for m in reminder.sender.committee_memberships)


def _out(reminder: Reminder, viewer: User, include_recipients: bool = False) -> ReminderOut:
    mine = next((r for r in reminder.recipients if r.user_id == viewer.id), None)
    return ReminderOut(
        id=reminder.id,
        direction=reminder.direction,
        from_user=reminder.from_user,
        sender_name=reminder.sender.display_name or reminder.sender.email,
        message=reminder.message,
        target_type=reminder.target_type,
        target_id=reminder.target_id,
        is_read=mine.is_read if mine else reminder.is_read,
        is_done=mine.is_done if mine else None,
        deadline=reminder.deadline,
        committees=(
            [ReminderCommitteeOut(id=tc.committee.id, name=tc.committee.name) for tc in reminder.target_committees]
            if reminder.direction == ReminderDirection.to_user
            else []
        ),
        recipients=(
            [
                ReminderRecipientOut(
                    user_id=r.user_id,
                    name=r.user.display_name or r.user.email,
                    is_read=r.is_read,
                    is_done=r.is_done,
                    completed_at=r.completed_at,
                )
                for r in reminder.recipients
            ]
            if include_recipients
            else None
        ),
        created_at=reminder.created_at,
    )


def _normalize_deadline(deadline: datetime | None) -> datetime | None:
    """A bare <input type="date"> round-trips as midnight naive — treat that as "by
    end of that day" rather than midnight, then store everything as UTC."""
    if deadline is None:
        return None
    if deadline.tzinfo is None:
        if deadline.time() == time_of_day.min:
            deadline = deadline.replace(hour=23, minute=59, second=59)
        deadline = deadline.replace(tzinfo=ZoneInfo(get_settings().app_timezone))
    return deadline.astimezone(timezone.utc)


def _format_deadline(deadline: datetime) -> str:
    return deadline.astimezone(ZoneInfo(get_settings().app_timezone)).strftime("%d %b %Y, %H:%M %Z")


@router.post("", response_model=ReminderOut, status_code=status.HTTP_201_CREATED)
async def create_reminder(req: ReminderCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> ReminderOut:
    if not req.message.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Reminder message cannot be empty")
    if req.target_type == ReminderTargetType.general and req.target_id is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "General reminders cannot have a target")
    if req.target_type == ReminderTargetType.proposal:
        proposal = db.get(Proposal, req.target_id) if req.target_id else None
        if not proposal or (
            user.role != UserRole.admin
            and proposal.committee_id not in user.committee_ids
        ) or (
            user.role == UserRole.admin
            and not admin_can_access_committee(user, proposal.committee)
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Proposal not found")
    if req.target_type == ReminderTargetType.disposable:
        disposable = db.get(DisposableRequest, req.target_id) if req.target_id else None
        if not disposable or (
            user.role != UserRole.admin and disposable.requested_by != user.id
        ) or (
            user.role == UserRole.admin
            and not admin_can_access_committee(user, disposable.proposal.committee)
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Disposable request not found")

    reminder = Reminder(
        from_user=user.id,
        message=req.message.strip(),
        direction=ReminderDirection.to_admin,
        target_type=req.target_type,
        target_id=req.target_id,
    )
    db.add(reminder)
    db.commit()
    db.refresh(reminder)
    portfolio = None
    if req.target_type == ReminderTargetType.proposal and proposal:
        portfolio = committee_portfolio(proposal.committee)
    elif user.committee_memberships:
        portfolio = committee_portfolio(user.committee_memberships[0].committee)
    await notify_admins_reminder(user.display_name or user.email, reminder.message, portfolio)
    return _out(reminder, user)


@router.post("/broadcast", response_model=ReminderOut, status_code=status.HTTP_201_CREATED)
async def create_broadcast(
    req: ReminderBroadcastCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_user)
) -> ReminderOut:
    if admin.role != UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    if not req.message.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Reminder message cannot be empty")

    committee_ids = list(dict.fromkeys(req.committee_ids))
    committees = db.query(Committee).filter(Committee.id.in_(committee_ids)).all()
    if len(committees) != len(committee_ids):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "One or more CCAs not found")
    if any(not admin_can_access_committee(admin, committee) for committee in committees):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot target a CCA outside your portfolio")

    reminder = Reminder(
        from_user=admin.id,
        message=req.message.strip(),
        direction=ReminderDirection.to_user,
        target_type=ReminderTargetType.general,
        deadline=_normalize_deadline(req.deadline),
    )
    db.add(reminder)
    db.flush()
    for committee in committees:
        db.add(ReminderTargetCommittee(reminder_id=reminder.id, committee_id=committee.id))

    member_ids = {
        m.user_id
        for m in db.query(UserCommittee).filter(UserCommittee.committee_id.in_(committee_ids)).all()
    }
    recipients = (
        db.query(User)
        .filter(User.id.in_(member_ids), User.role == UserRole.user, User.status == UserStatus.approved)
        .all()
        if member_ids
        else []
    )
    for recipient in recipients:
        db.add(ReminderRecipient(reminder_id=reminder.id, user_id=recipient.id))
    db.commit()
    db.refresh(reminder)

    deadline_text = _format_deadline(reminder.deadline) if reminder.deadline else None
    sender_name = admin.display_name or admin.email
    for recipient in recipients:
        await notify_user_reminder_broadcast(recipient.telegram_id, sender_name, reminder.message, deadline_text)

    return _out(reminder, admin, include_recipients=True)


@router.get("", response_model=list[ReminderOut])
def list_reminders(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[ReminderOut]:
    reminders = (
        db.query(Reminder)
        .filter(Reminder.direction == ReminderDirection.to_admin)
        .order_by(Reminder.created_at.desc())
        .all()
    )
    if user.role != UserRole.admin:
        reminders = [r for r in reminders if r.from_user == user.id]
    else:
        reminders = [r for r in reminders if _admin_can_see_reminder(db, r, user)]
    return [_out(r, user) for r in reminders]


@router.get("/inbox", response_model=list[ReminderOut])
def list_inbox(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[ReminderOut]:
    """Reminders addressed to the current user: nudges from users (admin) or
    broadcasts from admins (everyone else)."""
    if user.role == UserRole.admin:
        return list_reminders(db, user)
    reminders = (
        db.query(Reminder)
        .join(ReminderRecipient)
        .filter(Reminder.direction == ReminderDirection.to_user, ReminderRecipient.user_id == user.id)
        .order_by(Reminder.created_at.desc())
        .all()
    )
    return [_out(r, user) for r in reminders]


@router.get("/outbox", response_model=list[ReminderOut])
def list_outbox(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[ReminderOut]:
    """Reminders the current user has sent: broadcasts to CCAs (admin) or nudges to
    admin (everyone else)."""
    if user.role == UserRole.admin:
        reminders = (
            db.query(Reminder)
            .filter(Reminder.direction == ReminderDirection.to_user, Reminder.from_user == user.id)
            .order_by(Reminder.created_at.desc())
            .all()
        )
        return [_out(r, user, include_recipients=True) for r in reminders]
    reminders = (
        db.query(Reminder)
        .filter(Reminder.direction == ReminderDirection.to_admin, Reminder.from_user == user.id)
        .order_by(Reminder.created_at.desc())
        .all()
    )
    return [_out(r, user) for r in reminders]


@router.get("/unread-count", response_model=ReminderUnreadCount)
def unread_count(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> ReminderUnreadCount:
    if user.role == UserRole.admin:
        reminders = (
            db.query(Reminder)
            .filter(Reminder.direction == ReminderDirection.to_admin, Reminder.is_read.is_(False))
            .all()
        )
        return ReminderUnreadCount(count=sum(_admin_can_see_reminder(db, r, user) for r in reminders))
    count = (
        db.query(ReminderRecipient)
        .join(Reminder)
        .filter(
            Reminder.direction == ReminderDirection.to_user,
            ReminderRecipient.user_id == user.id,
            ReminderRecipient.is_read.is_(False),
        )
        .count()
    )
    return ReminderUnreadCount(count=count)


@router.patch("/{reminder_id}/read", response_model=ReminderOut)
def mark_read(reminder_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> ReminderOut:
    reminder = db.get(Reminder, reminder_id)
    if not reminder:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reminder not found")
    if reminder.direction == ReminderDirection.to_admin:
        if not _admin_can_see_reminder(db, reminder, user):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Reminder not found")
        reminder.is_read = True
    else:
        recipient = db.query(ReminderRecipient).filter_by(reminder_id=reminder_id, user_id=user.id).first()
        if not recipient:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Reminder not found")
        recipient.is_read = True
    db.commit()
    db.refresh(reminder)
    return _out(reminder, user)


@router.patch("/{reminder_id}/done", response_model=ReminderOut)
def mark_done(reminder_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> ReminderOut:
    reminder = db.get(Reminder, reminder_id)
    if not reminder or reminder.direction != ReminderDirection.to_user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reminder not found")
    recipient = db.query(ReminderRecipient).filter_by(reminder_id=reminder_id, user_id=user.id).first()
    if not recipient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reminder not found")
    recipient.is_done = True
    recipient.is_read = True
    recipient.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(reminder)
    return _out(reminder, user)


@router.delete("/{reminder_id}/mine", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_recipient(reminder_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> None:
    """A recipient removes a completed broadcast from their own inbox. Only their
    ReminderRecipient row (and its nudge receipts) is deleted — the Reminder and
    every other recipient's copy are untouched."""
    recipient = (
        db.query(ReminderRecipient)
        .join(Reminder)
        .filter(
            ReminderRecipient.reminder_id == reminder_id,
            ReminderRecipient.user_id == user.id,
            Reminder.direction == ReminderDirection.to_user,
        )
        .first()
    )
    if not recipient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reminder not found")
    if not recipient.is_done:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only reminders marked as done can be removed")
    db.query(ReminderNudge).filter(ReminderNudge.reminder_recipient_id == recipient.id).delete()
    db.delete(recipient)
    db.commit()


@router.delete("/{reminder_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reminder(reminder_id: int, db: Session = Depends(get_db), admin: User = Depends(get_current_user)) -> None:
    if admin.role != UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    reminder = db.get(Reminder, reminder_id)
    if not reminder or reminder.direction != ReminderDirection.to_admin:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reminder not found")
    db.delete(reminder)
    db.commit()
