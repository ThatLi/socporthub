"""Scheduled Telegram nudges for admin->CCA broadcast reminders with a deadline.

Mirrors api/services/grading.py's queue_notice/process_grading_notifications shape:
a minute-tick loop that computes which recipients fall in a reminder window and
stores delivery receipts (ReminderNudge) so a restart or slow tick can't
double-send. Grading uses 7/3/1-day windows; this uses 3/1-day only.
"""
import logging
from datetime import datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from api.config import get_settings
from api.models import Reminder, ReminderDirection, ReminderNudge, ReminderRecipient
from api.services.telegram import send_message

logger = logging.getLogger(__name__)

NUDGE_WINDOWS = (3, 1)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _nudge_message(reminder: Reminder, days: int) -> str:
    settings = get_settings()
    due = aware(reminder.deadline).astimezone(ZoneInfo(settings.app_timezone)).strftime("%d %b %Y, %H:%M %Z")
    plural = "day" if days == 1 else "days"
    sender_name = reminder.sender.display_name or reminder.sender.email
    return (
        f"⏰ Reminder — {days} {plural} left\n"
        f"From: <b>{escape(sender_name)}</b>\n\n"
        f"{escape(reminder.message)}\n\nDue: {due}"
    )


def queue_nudge(db: Session, recipient: ReminderRecipient, days: int) -> None:
    milestone = str(days)
    existing = (
        db.query(ReminderNudge)
        .filter_by(reminder_recipient_id=recipient.id, milestone=milestone)
        .first()
    )
    if existing is None:
        db.add(
            ReminderNudge(
                reminder_recipient_id=recipient.id,
                milestone=milestone,
                message=_nudge_message(recipient.reminder, days),
            )
        )


async def process_reminder_nudges(db: Session, now: datetime | None = None) -> None:
    """Run each minute; persisted deliveries survive app restarts."""
    settings = get_settings()
    now = now or utcnow()
    recipients = (
        db.query(ReminderRecipient)
        .join(Reminder)
        .filter(
            Reminder.direction == ReminderDirection.to_user,
            Reminder.deadline.is_not(None),
            ReminderRecipient.is_done.is_(False),
        )
        .all()
    )
    for recipient in recipients:
        deadline = aware(recipient.reminder.deadline)
        for days in NUDGE_WINDOWS:
            # Send only in the relevant 24-hour window, including after restarts.
            if deadline - timedelta(days=days) <= now < deadline - timedelta(days=days - 1):
                queue_nudge(db, recipient, days)
    db.commit()

    pending = db.query(ReminderNudge).filter(ReminderNudge.sent_at.is_(None)).all()
    for nudge in pending:
        recipient = nudge.recipient
        if recipient.is_done:
            continue
        deadline = aware(recipient.reminder.deadline)
        # Recheck the window in case delivery was delayed past it.
        if now >= deadline - timedelta(days=int(nudge.milestone) - 1):
            continue
        try:
            if not settings.telegram_bot_token:
                continue
            await send_message(recipient.user.telegram_id, nudge.message, raise_on_error=True)
            nudge.sent_at = now
            db.commit()
        except Exception:
            db.rollback()
            logger.warning("Reminder nudge delivery failed; will retry", exc_info=True)
