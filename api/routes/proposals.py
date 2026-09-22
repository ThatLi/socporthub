import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from fastapi import Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.auth import get_current_user
from api.config import get_settings
from api.database import get_db
from api.models import (
    PROPOSAL_STATUS_TRANSITIONS,
    CalendarEvent,
    Committee,
    Proposal, EmailDraft,
    ProposalComment,
    ProposalCommentRead,
    ProposalStatusHistory,
    ProposalCategory, ProposalStatus,
    User,
    UserRole,
)
from api.portfolio import (
    admin_committee_filter,
    category_allowed_for_committee,
    committee_portfolio,
    sends_confirmation_email as should_send_confirmation_email,
)
from api.schemas import (
    ProposalCommentCreate,
    ProposalCommentOut,
    ProposalStatusHistoryOut,
    ProposalCreateRequest,
    ProposalOut,
    ProposalStatusCounts,
    ProposalUpdateRequest,
)
from bot.notifications import (
    notify_admins_new_proposal,
    notify_admins_new_comment,
    notify_user_new_comment,
    notify_user_status_change,
    send_proposal_announcement,
)
from api.routes.email import _committee_ccs, _generated, _without_links
from api.services.google_docs import download_google_doc_pdf, proposal_pdf_filename
from api.services.document_links import verify_document_token
from api.services.email_attachments import proposal_pdf_attachment
from api.services.resend_email import send_email
from api.services.grading import RUBRICS
from api.services.google_drive import provision_evidence

router = APIRouter(prefix="/api/proposals", tags=["proposals"])


def _utc_naive(value):
    """Normalize DB timestamps before comparing legacy naive and new aware values."""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _to_out(p: Proposal, *, latest_comment_at=None, unread_comment_count: int = 0) -> ProposalOut:
    try:
        requested_ccas = json.loads(p.requested_ccas or "[]")
    except json.JSONDecodeError:
        requested_ccas = []
    try:
        external_form_data = json.loads(p.external_form_data or "{}")
    except json.JSONDecodeError:
        external_form_data = {}
    return ProposalOut(
        grading_available=get_settings().grading_enabled and p.category.value in RUBRICS,
        id=p.id,
        committee_id=p.committee_id,
        committee_name=p.committee.name,
        portfolio=committee_portfolio(p.committee),
        submitted_by=p.submitted_by,
        submitter_name=p.submitter.display_name,
        submitter_telegram_username=p.submitter.telegram_username,
        title=p.title,
        category=p.category,
        description=p.description,
        doc_link=p.doc_link,
        blast_message=p.blast_message,
        poster_filename=p.poster_filename,
        poster_content_type=p.poster_content_type,
        status=p.status,
        event_date=p.event_date,
        event_time=p.event_time.isoformat(timespec="minutes") if p.event_time else None,
        created_at=p.created_at,
        updated_at=p.updated_at,
        latest_comment_at=latest_comment_at,
        unread_comment_count=unread_comment_count,
        requested_ccas=requested_ccas if isinstance(requested_ccas, list) else [],
        external_form_data=external_form_data if isinstance(external_form_data, dict) else {},
    )


def _visible_query(db: Session, user: User):
    query = db.query(Proposal)
    if user.role != UserRole.admin:
        committee_ids = user.committee_ids
        if not committee_ids:
            return query.filter(False)
        query = query.filter(Proposal.committee_id.in_(committee_ids))
    else:
        query = query.join(Proposal.committee).filter(admin_committee_filter(user))
    return query


@router.get("", response_model=list[ProposalOut])
def list_proposals(
    committee_id: int | None = None,
    proposal_status: ProposalStatus | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ProposalOut]:
    query = _visible_query(db, user)
    if committee_id is not None:
        query = query.filter(Proposal.committee_id == committee_id)
    if proposal_status is not None:
        query = query.filter(Proposal.status == proposal_status)
    proposals = query.order_by(Proposal.created_at.desc()).all()
    proposal_ids = [p.id for p in proposals]
    # A status change (approval, resubmission, grading opening, ...) should also
    # bump a proposal to the top, same as a new comment does.
    latest_status_changes = dict(
        db.query(ProposalStatusHistory.proposal_id, func.max(ProposalStatusHistory.created_at))
        .filter(ProposalStatusHistory.proposal_id.in_(proposal_ids))
        .group_by(ProposalStatusHistory.proposal_id)
        .all()
    ) if proposal_ids else {}
    enriched = []
    for proposal in proposals:
        comments = proposal.comments
        latest_comment_at = max((_utc_naive(comment.created_at) for comment in comments), default=None)
        read = db.get(ProposalCommentRead, (user.id, proposal.id))
        read_at = _utc_naive(read.read_at) if read else None
        unread = sum(
            1
            for comment in comments
            if comment.author_id != user.id
            and (
                user.role == UserRole.admin
                or (proposal.submitted_by == user.id and comment.author.role == UserRole.admin)
            )
            and (read_at is None or _utc_naive(comment.created_at) > read_at)
        )
        latest_status_at = _utc_naive(latest_status_changes.get(proposal.id))
        activity_at = max(
            t for t in (_utc_naive(proposal.created_at), latest_comment_at, latest_status_at) if t is not None
        )
        enriched.append((activity_at, proposal, unread, latest_comment_at))
    enriched.sort(key=lambda item: item[0], reverse=True)
    return [_to_out(p, latest_comment_at=latest, unread_comment_count=unread) for _, p, unread, latest in enriched]


@router.get("/summary", response_model=ProposalStatusCounts)
def status_summary(
    committee_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProposalStatusCounts:
    query = _visible_query(db, user)
    if committee_id is not None:
        query = query.filter(Proposal.committee_id == committee_id)
    counts = ProposalStatusCounts()
    for p in query.all():
        setattr(counts, p.status.value, getattr(counts, p.status.value) + 1)
    return counts


def _validate_category_requirements(
    category: ProposalCategory,
    *,
    event_date,
    event_time,
    doc_link: str | None,
    blast_message: str | None,
    poster_data: bytes | None,
    check_poster: bool,
) -> None:
    """Shared field requirements for a proposal about to become in_review.

    check_poster is False at creation time — a poster upload is a separate follow-up
    call after the proposal exists, so it can't be checked yet there. The frontend
    still enforces it client-side before allowing that follow-up call to be skipped.
    """
    if category in (ProposalCategory.event, ProposalCategory.initiative, ProposalCategory.pantry_cleaning) and not event_date:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "An event date is required for this category")
    if check_poster and category in (ProposalCategory.event, ProposalCategory.initiative, ProposalCategory.welfare, ProposalCategory.merch) and not poster_data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A poster is required for this category")
    if category in (ProposalCategory.event, ProposalCategory.merch) and not doc_link:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A link or PDF URL is required for this category")
    if category in (ProposalCategory.event, ProposalCategory.initiative, ProposalCategory.welfare) and not blast_message:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A blast message is required for this category")


@router.post("", response_model=ProposalOut, status_code=status.HTTP_201_CREATED)
async def create_proposal(
    req: ProposalCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProposalOut:
    if not user.committee_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You are not assigned to a committee yet")
    if not req.save_draft:
        _validate_category_requirements(
            req.category,
            event_date=req.event_date,
            event_time=req.event_time,
            doc_link=req.doc_link,
            blast_message=req.blast_message,
            poster_data=None,
            check_poster=False,
        )
    # A user may belong to multiple committees; submit under the first for MVP simplicity.
    committee_id = sorted(user.committee_ids)[0]
    committee = db.get(Committee, committee_id)
    if not category_allowed_for_committee(committee, req.category):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That category is not available for this portfolio")

    proposal = Proposal(
        committee_id=committee_id,
        submitted_by=user.id,
        title=req.title,
        category=req.category,
        description=req.description,
        doc_link=req.doc_link,
        blast_message=req.blast_message,
        event_date=req.event_date,
        event_time=req.event_time,
        requested_ccas=json.dumps(req.requested_ccas),
        external_form_data=json.dumps(req.external_form_data),
        status=ProposalStatus.draft if req.save_draft else ProposalStatus.in_review,
    )
    db.add(proposal)
    db.commit()
    db.refresh(proposal)

    if not req.save_draft:
        await provision_evidence(db, proposal.id)
        await notify_admins_new_proposal(
            proposal.title,
            user.display_name or user.email,
            committee_portfolio(proposal.committee),
        )

    return _to_out(proposal)


def get_visible_proposal(db: Session, user: User, proposal_id: int) -> Proposal:
    proposal = _visible_query(db, user).filter(Proposal.id == proposal_id).first()
    if not proposal:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Proposal not found")
    return proposal


@router.get("/{proposal_id}", response_model=ProposalOut)
def get_proposal(
    proposal_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProposalOut:
    return _to_out(get_visible_proposal(db, user, proposal_id))


@router.patch("/{proposal_id}", response_model=ProposalOut)
async def update_proposal(
    proposal_id: int,
    req: ProposalUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProposalOut:
    proposal = get_visible_proposal(db, user, proposal_id)
    is_admin = user.role == UserRole.admin
    is_owner = proposal.submitted_by == user.id

    if req.status is not None:
        # Owner can submit a draft, and can resubmit after being sent back to
        # needs_action — both land on in_review. Every other transition is admin-only.
        owner_can_submit = (
            is_owner
            and proposal.status in (ProposalStatus.draft, ProposalStatus.needs_action)
            and req.status == ProposalStatus.in_review
        )
        if not is_admin and not owner_can_submit:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins can change status")
        if not req.send_email and (not is_admin or req.status != ProposalStatus.submitted):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins can submit without sending an email")
        allowed = PROPOSAL_STATUS_TRANSITIONS.get(proposal.status, set())
        if req.status not in allowed:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Cannot move status from {proposal.status.value} to {req.status.value}",
            )
        if (
            proposal.status == ProposalStatus.in_review
            and req.status == ProposalStatus.finished
            and should_send_confirmation_email(
                proposal.committee, proposal.category
            )
        ):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Event proposals must be approved through the email workflow",
            )
        was_awaiting_review = proposal.status in (ProposalStatus.draft, ProposalStatus.needs_action)
        previous_status = proposal.status
        proposal.status = req.status
        db.add(ProposalStatusHistory(
            proposal_id=proposal.id,
            changed_by=user.id,
            from_status=previous_status,
            to_status=req.status,
        ))

    content_fields = ("category", "title", "description", "doc_link", "blast_message", "event_date", "event_time", "requested_ccas", "external_form_data")
    # Use model_fields_set (not "is not None") so a client can explicitly clear a
    # nullable field — e.g. {"event_date": null} — by including the key in the payload.
    # Omitting the key entirely means "leave this field alone".
    provided_content_fields = req.model_fields_set & set(content_fields)
    if "category" in provided_content_fields and proposal.status in (ProposalStatus.grading, ProposalStatus.final):
        raise HTTPException(409, "The category cannot change once grading has started")
    if provided_content_fields:
        if not is_admin and not (is_owner and proposal.status in (ProposalStatus.draft, ProposalStatus.needs_action)):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Proposal can only be edited by its owner while in needs_action",
            )
        if "title" in provided_content_fields and req.title is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "title cannot be cleared")
        if "category" in provided_content_fields and req.category is not None:
            if not category_allowed_for_committee(proposal.committee, req.category):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "That category is not available for this portfolio")
        for field in provided_content_fields:
            value = getattr(req, field)
            setattr(proposal, field, json.dumps(value) if field in ("requested_ccas", "external_form_data") else value)

    if req.status == ProposalStatus.in_review:
        _validate_category_requirements(
            proposal.category,
            event_date=proposal.event_date,
            event_time=proposal.event_time,
            doc_link=proposal.doc_link,
            blast_message=proposal.blast_message,
            poster_data=proposal.poster_data,
            check_poster=True,
        )

    if req.comment:
        db.add(ProposalComment(proposal_id=proposal.id, author_id=user.id, body=req.comment))

    if req.status == ProposalStatus.finished and proposal.event_date is not None:
        # Finished proposals graduate from a "proposal date" marker to a real, shared
        # calendar entry — visible with the committee color and in the .ics feed.
        db.add(
            CalendarEvent(
                committee_id=proposal.committee_id,
                created_by=user.id,
                title=proposal.title,
                description=proposal.description,
                event_date=proposal.event_date,
            )
        )

    # Approval has an external side effect (PDF export + email). Keep the status
    # change pending until that side effect succeeds, so a failed email does not
    # falsely tell the admin that the proposal was submitted.
    sends_confirmation_email = req.status == ProposalStatus.submitted and req.send_email and should_send_confirmation_email(
        proposal.committee, proposal.category
    )
    if not sends_confirmation_email:
        db.commit()
        db.refresh(proposal)

    if req.status == ProposalStatus.in_review and was_awaiting_review:
        await provision_evidence(db, proposal.id)
        # A fresh draft submission or a resubmission after needs_action both mean
        # "there's something for an admin to review now" — same signal as a new proposal.
        await notify_admins_new_proposal(
            proposal.title,
            proposal.submitter.display_name or proposal.submitter.email,
            committee_portfolio(proposal.committee),
        )

    if sends_confirmation_email:
        draft = db.query(EmailDraft).filter(EmailDraft.proposal_id == proposal.id).first()
        if draft:
            subject, body = draft.subject, draft.body
        else:
            subject, body = _generated(proposal)
        attachment, large_pdf_link = await proposal_pdf_attachment(proposal)
        safe_body = _without_links(body)
        if large_pdf_link:
            safe_body += f"\n\nThe proposal PDF is too large to attach. Download it here: {large_pdf_link}"
        await send_email(
            to=proposal.submitter.email,
            subject=_without_links(subject),
            body=safe_body,
            cc=_committee_ccs(proposal),
            attachment=attachment,
        )
        db.commit()
        db.refresh(proposal)

    if req.status is not None:
        await notify_user_status_change(
            proposal.submitter.telegram_id, proposal.title, proposal.status.value, req.comment
        )

    return _to_out(proposal)


@router.post("/{proposal_id}/poster", response_model=ProposalOut)
async def upload_poster(
    proposal_id: int,
    poster: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProposalOut:
    proposal = get_visible_proposal(db, user, proposal_id)
    if proposal.submitted_by != user.id or proposal.status not in (ProposalStatus.draft, ProposalStatus.needs_action):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can upload a poster while editing")
    if proposal.category not in (ProposalCategory.event, ProposalCategory.initiative, ProposalCategory.welfare, ProposalCategory.merch):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Posters are only used for Event, Initiative, Welfare, and Merch proposals")
    if poster.content_type not in {"image/jpeg", "image/png", "image/webp", "application/pdf"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Poster must be a PDF, PNG, JPG, or WEBP file")
    data = await poster.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Poster must be 10 MB or smaller")
    proposal.poster_filename = poster.filename or "poster"
    proposal.poster_content_type = poster.content_type
    proposal.poster_data = data
    db.commit()
    db.refresh(proposal)
    return _to_out(proposal)


@router.get("/{proposal_id}/document")
async def download_document(
    proposal_id: int,
    token: str = Query(...),
    db: Session = Depends(get_db),
) -> Response:
    verify_document_token(token, proposal_id)
    proposal = db.get(Proposal, proposal_id)
    if not proposal or not proposal.doc_link:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Supporting document not found")
    _, pdf = await download_google_doc_pdf(proposal.doc_link)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{proposal_pdf_filename(proposal.title, proposal.committee.name)}"'},
    )


@router.get("/{proposal_id}/poster")
def download_poster(proposal_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Response:
    proposal = get_visible_proposal(db, user, proposal_id)
    if not proposal.poster_data or not proposal.poster_content_type:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Poster not found")
    return Response(
        content=proposal.poster_data,
        media_type=proposal.poster_content_type,
        headers={"Content-Disposition": f'inline; filename="{proposal.poster_filename or "poster"}"'},
    )


@router.post("/{proposal_id}/announce", status_code=status.HTTP_204_NO_CONTENT)
async def announce_proposal(
    proposal_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    proposal = get_visible_proposal(db, user, proposal_id)
    if proposal.status != ProposalStatus.finished:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only finished proposals can be announced")
    if proposal.category not in (ProposalCategory.event, ProposalCategory.initiative):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only event and initiative proposals can be announced")

    chat_id = get_settings().announcement_telegram_id
    if not chat_id:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Announcement channel is not configured")

    try:
        await send_proposal_announcement(chat_id, proposal.poster_data, proposal.poster_filename, proposal.blast_message)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    except Exception:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Failed to reach the announcement bot — it may need to be messaged/registered first",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _comment_to_out(c: ProposalComment) -> ProposalCommentOut:
    out = ProposalCommentOut.model_validate(c)
    out.author_name = c.author.display_name or c.author.email
    out.author_role = c.author.role
    return out


def _status_history_to_out(entry: ProposalStatusHistory) -> ProposalStatusHistoryOut:
    out = ProposalStatusHistoryOut.model_validate(entry)
    out.changer_name = entry.changer.display_name or entry.changer.email
    return out


@router.get("/{proposal_id}/comments", response_model=list[ProposalCommentOut])
def list_comments(
    proposal_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ProposalCommentOut]:
    proposal = get_visible_proposal(db, user, proposal_id)
    return [_comment_to_out(c) for c in proposal.comments]


@router.post("/{proposal_id}/comments/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_comments_read(
    proposal_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    proposal = get_visible_proposal(db, user, proposal_id)
    read = db.get(ProposalCommentRead, (user.id, proposal.id))
    now = datetime.now(timezone.utc)
    if read:
        read.read_at = now
    else:
        db.add(ProposalCommentRead(user_id=user.id, proposal_id=proposal.id, read_at=now))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{proposal_id}/status-history", response_model=list[ProposalStatusHistoryOut])
def list_status_history(
    proposal_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ProposalStatusHistoryOut]:
    proposal = get_visible_proposal(db, user, proposal_id)
    entries = (
        db.query(ProposalStatusHistory)
        .filter(ProposalStatusHistory.proposal_id == proposal.id)
        .order_by(ProposalStatusHistory.created_at)
        .all()
    )
    return [_status_history_to_out(entry) for entry in entries]


@router.post("/{proposal_id}/comments", response_model=ProposalCommentOut, status_code=status.HTTP_201_CREATED)
async def add_comment(
    proposal_id: int,
    req: ProposalCommentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProposalCommentOut:
    proposal = get_visible_proposal(db, user, proposal_id)
    if not req.body.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Comment cannot be empty")
    if req.reply_to_comment_id is not None:
        parent = db.get(ProposalComment, req.reply_to_comment_id)
        if not parent or parent.proposal_id != proposal.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "The comment being replied to was not found")
    comment = ProposalComment(
        proposal_id=proposal.id,
        author_id=user.id,
        body=req.body.strip(),
        reply_to_comment_id=req.reply_to_comment_id,
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    if user.role == UserRole.admin:
        await notify_user_new_comment(
            proposal.submitter.telegram_id,
            proposal.title,
            user.display_name or user.email,
            comment.body,
        )
    else:
        await notify_admins_new_comment(
            proposal.title,
            user.display_name or user.email,
            comment.body,
            committee_portfolio(proposal.committee),
        )
    return _comment_to_out(comment)
