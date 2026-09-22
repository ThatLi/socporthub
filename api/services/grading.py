"""Category rubrics, validation and notification scheduling for grading."""
import json
import logging
from datetime import datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy.orm import Session

from api.config import get_settings
from api.models import GradingNotification, ProposalGrading, ProposalStatus, Portfolio
from api.portfolio import committee_portfolio
from api.services.telegram import send_message

logger = logging.getLogger(__name__)
RUBRICS = {
    "event": {"fields": ["food", "decor", "activities", "creativity"]},
    "initiative": {"fields": ["activities", "creativity"]},
    "decor": {"fields": ["creativity", "visual_quality"], "options": ["noticeboard", "block"], "multiple": True},
    "pubs": {"fields": ["creativity", "visual_quality"], "options": ["tiktok", "ig", "other"], "multiple": True},
    "welfare": {"fields": ["quality", "creativity"], "options": ["arts_crafts", "gifts", "food", "welfare_pack", "other"], "multiple": False},
    "pantry_cleaning": {"fields": ["cleanliness", "decor"]},
}


def utcnow():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class Rating(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: StrictInt | None = Field(default=None, ge=0, le=10)
    justification: str = Field(default="", max_length=5000)


class AssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selections: list[str] = Field(default_factory=list)
    ratings: dict[str, Rating] = Field(default_factory=dict)
    submit: bool = False


class GradingExtendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deadline: datetime


def validate_assessment(req, category):
    rubric = RUBRICS[category]
    if set(req.ratings) - set(rubric["fields"]):
        raise HTTPException(400, "Ratings do not match this category")
    if len(req.selections) != len(set(req.selections)) or set(req.selections) - set(rubric.get("options", [])):
        raise HTTPException(400, "Invalid category selections")
    if not rubric.get("multiple") and len(req.selections) > 1:
        raise HTTPException(400, "Choose only one category option")
    if req.submit:
        if rubric.get("options") and not req.selections:
            raise HTTPException(400, "Choose at least one category option")
        for field in rubric["fields"]:
            rating = req.ratings.get(field)
            if rating is None or rating.score is None or not rating.justification.strip():
                raise HTTPException(400, f"Provide a score and justification for {field.replace('_', ' ')} (including N/A scores)")
    return json.dumps({
        "selections": req.selections,
        "ratings": {key: {"score": val.score, "justification": val.justification.strip()} for key, val in req.ratings.items()},
    })


def admin_ids(proposal):
    settings = get_settings()
    # Exclude welfare IDs even if mistakenly repeated in the legacy social list.
    if committee_portfolio(proposal.committee) == Portfolio.welfare:
        return settings.welfare_admin_telegram_id_set
    return settings.social_admin_telegram_id_set - settings.welfare_admin_telegram_id_set


def queue_notice(db: Session, grading, milestone):
    proposal = grading.proposal
    recipients = admin_ids(proposal)
    if milestone != "submitted":
        recipients = (recipients if get_settings().grading_remind_admins else set()) | {proposal.submitter.telegram_id}
    due = aware(grading.deadline).astimezone(ZoneInfo(get_settings().app_timezone)).strftime("%d %b %Y, %H:%M %Z")
    title = escape(proposal.title)
    if milestone == "submitted":
        message = f"✅ Self-assessment submitted: <b>{title}</b>\n{escape(proposal.committee.name)} · Ready for admin grading."
    else:
        heading = "Grading is open — you have 14 days" if milestone == "started" else f"Grading reminder — {milestone} day(s) until the deadline"
        message = f"📝 {heading}\n<b>{title}</b> · {escape(proposal.committee.name)}\nDue: {due}\nSubmit your self-assessment for <b>{title}</b>."
    for recipient in recipients:
        existing = db.query(GradingNotification).filter_by(proposal_id=proposal.id, milestone=milestone, telegram_id=recipient).first()
        if existing is None:
            db.add(GradingNotification(proposal_id=proposal.id, milestone=milestone, telegram_id=recipient, message=message))
    db.flush()


async def process_grading_notifications(db: Session, now=None):
    """Run each minute; persisted deliveries survive app restarts."""
    settings = get_settings()
    now = now or utcnow()
    active = db.query(ProposalGrading).all()
    for grading in active:
        if grading.proposal.status != ProposalStatus.grading:
            continue
        deadline = aware(grading.deadline)
        for days in (7, 3, 1):
            # Send only in the relevant 24-hour window, including after restarts.
            if deadline - timedelta(days=days) <= now < deadline - timedelta(days=days - 1):
                queue_notice(db, grading, str(days))
    db.commit()
    pending = db.query(GradingNotification).filter(GradingNotification.sent_at.is_(None)).all()
    for notice in pending:
        grading = db.get(ProposalGrading, notice.proposal_id)
        if notice.milestone != "submitted":
            if grading.proposal.status != ProposalStatus.grading or now >= aware(grading.deadline):
                continue
            if notice.milestone != "started" and now >= aware(grading.deadline) - timedelta(days=int(notice.milestone) - 1):
                continue
        # Recheck portfolio recipients in case configuration changed while queued.
        allowed = admin_ids(grading.proposal)
        if notice.milestone != "submitted":
            allowed = (allowed if settings.grading_remind_admins else set()) | {grading.proposal.submitter.telegram_id}
        if notice.telegram_id not in allowed:
            continue
        try:
            if not settings.telegram_bot_token:
                continue
            await send_message(notice.telegram_id, notice.message, raise_on_error=True)
            notice.sent_at = now
            db.commit()
        except Exception:
            db.rollback()
            logger.warning("Grading notification delivery failed; will retry", exc_info=True)
