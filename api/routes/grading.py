import json
from datetime import time as time_of_day, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.auth import get_current_user
from api.config import get_settings
from api.database import get_db
from api.models import GradingNotification, Proposal, ProposalGrading, ProposalStatus, ProposalStatusHistory, User, UserRole
from api.routes.proposals import get_visible_proposal
from api.services.google_drive import folder_url, provision_evidence
from api.services.grading import AssessmentRequest, GradingExtendRequest, RUBRICS, aware, process_grading_notifications, queue_notice, utcnow, validate_assessment

router = APIRouter(prefix="/api/proposals", tags=["grading"])


def enabled():
    if not get_settings().grading_enabled:
        raise HTTPException(404, "Grading is not enabled")


def can_grade_proposal(user: User, proposal: Proposal) -> bool:
    return user.role == UserRole.admin or (
        user.id != proposal.submitted_by and proposal.committee_id in user.committee_ids
    )


def _normalize_deadline(deadline):
    """A bare <input type="date"> round-trips as midnight naive — treat that as "by
    end of that day" rather than midnight, then store everything as UTC."""
    if deadline.tzinfo is None:
        if deadline.time() == time_of_day.min:
            deadline = deadline.replace(hour=23, minute=59, second=59)
        deadline = deadline.replace(tzinfo=ZoneInfo(get_settings().app_timezone))
    return deadline.astimezone(ZoneInfo("UTC"))


def grading_out(db, proposal, user):
    grading = db.get(ProposalGrading, proposal.id)
    grader = can_grade_proposal(user, proposal)
    owner = user.id == proposal.submitted_by
    return {
        "rubric": RUBRICS.get(proposal.category.value),
        "status": proposal.status.value,
        "can_grade": grader,
        "can_start": user.role == UserRole.admin and proposal.status == ProposalStatus.finished and proposal.category.value in RUBRICS,
        "can_edit_user": owner and not grader and proposal.status == ProposalStatus.grading,
        "can_edit_admin": grader and grading is not None and grading.admin_submitted_at is None,
        "started_at": aware(grading.started_at).isoformat() if grading else None,
        "deadline": aware(grading.deadline).isoformat() if grading else None,
        "user_assessment": json.loads(grading.user_data) if grading and (owner or grading.user_submitted_at) else None,
        "admin_assessment": json.loads(grading.admin_data) if grading and (grader or grading.admin_submitted_at) else None,
        "user_submitted_at": aware(grading.user_submitted_at).isoformat() if grading and grading.user_submitted_at else None,
        "admin_submitted_at": aware(grading.admin_submitted_at).isoformat() if grading and grading.admin_submitted_at else None,
        "drive_url": folder_url(proposal),
        "drive_error": proposal.drive_error,
        "evidence_done_at": aware(grading.evidence_done_at).isoformat() if grading and grading.evidence_done_at else None,
    }


@router.get("/{proposal_id}/grading", dependencies=[Depends(enabled)])
def get_grading(proposal_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return grading_out(db, get_visible_proposal(db, user, proposal_id), user)


@router.post("/{proposal_id}/grading/start", dependencies=[Depends(enabled)])
async def start_grading(proposal_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    proposal = get_visible_proposal(db, user, proposal_id)
    if user.role != UserRole.admin:
        raise HTTPException(403, "Only admins can open grading")
    if proposal.category.value not in RUBRICS:
        raise HTTPException(400, "No grading rubric is configured for this category")
    changed = db.query(Proposal).filter_by(id=proposal.id, status=ProposalStatus.finished).update({"status": ProposalStatus.grading})
    if not changed:
        raise HTTPException(409, "Only finished proposals can enter grading")
    now = utcnow()
    grading = ProposalGrading(proposal_id=proposal.id, started_by=user.id, started_at=now, deadline=now + timedelta(days=14))
    db.add(grading)
    db.add(ProposalStatusHistory(proposal_id=proposal.id, changed_by=user.id, from_status=ProposalStatus.finished, to_status=ProposalStatus.grading))
    db.flush()
    queue_notice(db, grading, "started")
    db.commit()
    await process_grading_notifications(db)
    await provision_evidence(db, proposal.id)
    db.refresh(proposal)
    return grading_out(db, proposal, user)


@router.put("/{proposal_id}/grading", dependencies=[Depends(enabled)])
async def save_grading(proposal_id: int, req: AssessmentRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    proposal = get_visible_proposal(db, user, proposal_id)
    grading = db.query(ProposalGrading).filter_by(proposal_id=proposal.id).with_for_update().first()
    if grading is None:
        raise HTTPException(409, "An admin must open grading first")
    grader = can_grade_proposal(user, proposal)
    if not grader and user.id != proposal.submitted_by:
        raise HTTPException(403, "Only the proposal submitter can edit the self-assessment")
    if grader:
        if grading.admin_submitted_at:
            raise HTTPException(409, "The admin assessment has already been submitted")
        if req.submit and not grading.user_submitted_at:
            raise HTTPException(409, "Wait for the self-assessment before submitting the admin grade")
    elif proposal.status != ProposalStatus.grading or grading.user_submitted_at:
        raise HTTPException(409, "The self-assessment has already been submitted")
    data = validate_assessment(req, proposal.category.value)
    if grader:
        grading.admin_data = data
        grading.admin_author_id = user.id
        if req.submit:
            grading.admin_submitted_at = utcnow()
    else:
        grading.user_data = data
        if req.submit:
            grading.user_submitted_at = utcnow()
            proposal.status = ProposalStatus.final
            db.add(ProposalStatusHistory(proposal_id=proposal.id, changed_by=user.id, from_status=ProposalStatus.grading, to_status=ProposalStatus.final))
            queue_notice(db, grading, "submitted")
    db.commit()
    await process_grading_notifications(db)
    return grading_out(db, proposal, user)


@router.post("/{proposal_id}/grading/extend", dependencies=[Depends(enabled)])
async def extend_grading_deadline(
    proposal_id: int, req: GradingExtendRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    proposal = get_visible_proposal(db, user, proposal_id)
    if user.role != UserRole.admin:
        raise HTTPException(403, "Only admins can extend a grading deadline")
    grading = db.query(ProposalGrading).filter_by(proposal_id=proposal.id).with_for_update().first()
    if grading is None:
        raise HTTPException(409, "Grading has not been opened for this proposal")
    if grading.user_submitted_at is not None:
        raise HTTPException(409, "The self-assessment has already been submitted; there is nothing to extend")
    new_deadline = _normalize_deadline(req.deadline)
    if new_deadline <= aware(grading.deadline):
        raise HTTPException(400, "The new deadline must be later than the current deadline")
    grading.deadline = new_deadline
    # Drop 7/3/1-day reminders queued against the old deadline (sent or not) so they
    # get recomputed, and can fire again, relative to the new one.
    db.query(GradingNotification).filter(
        GradingNotification.proposal_id == proposal.id,
        GradingNotification.milestone.in_(["7", "3", "1"]),
    ).delete(synchronize_session=False)
    db.commit()
    await process_grading_notifications(db)
    db.refresh(proposal)
    return grading_out(db, proposal, user)


@router.post("/{proposal_id}/grading/evidence", dependencies=[Depends(enabled)])
async def retry_evidence(proposal_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    proposal = get_visible_proposal(db, user, proposal_id)
    if user.role != UserRole.admin and user.id != proposal.submitted_by:
        raise HTTPException(403, "Only the submitter or admin can prepare evidence")
    if proposal.status == ProposalStatus.draft:
        raise HTTPException(409, "Submit the proposal before preparing evidence")
    await provision_evidence(db, proposal.id)
    db.refresh(proposal)
    return grading_out(db, proposal, user)


@router.post("/{proposal_id}/grading/evidence/done", dependencies=[Depends(enabled)])
async def mark_evidence_done(proposal_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    proposal = get_visible_proposal(db, user, proposal_id)
    if not can_grade_proposal(user, proposal) and user.id != proposal.submitted_by:
        raise HTTPException(403, "Only the submitter or committee grader can mark evidence done")
    grading = db.query(ProposalGrading).filter_by(proposal_id=proposal.id).with_for_update().first()
    if grading is None:
        raise HTTPException(409, "An admin must open grading first")
    if not folder_url(proposal):
        raise HTTPException(409, "Prepare the Google Drive folder first")
    if not grading.evidence_done_at:
        grading.evidence_done_at = utcnow()
        db.commit()
    return grading_out(db, proposal, user)
