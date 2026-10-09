import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.auth import get_current_admin, get_current_user
from api.database import get_db
from api.models import DisposableStatus, EmailDraft, Portfolio, Proposal, ProposalStatus, User, UserRole
from api.portfolio import committee_portfolio, sends_confirmation_email
from api.schemas import EmailDraftOut, EmailDraftUpdate
from api.services.resend_email import send_email
from api.config import get_settings
from api.services.email_attachments import proposal_pdf_attachment
from bot.notifications import notify_user_email_sent

router = APIRouter(prefix="/api/email", tags=["email"])
URL_PATTERN = re.compile(r"(?i)\b(?:https?://|www\.)\S+")


def _without_links(value: str) -> str:
    return URL_PATTERN.sub("", value).strip()


def _committee_ccs(proposal: Proposal) -> list[str]:
    """Return unique committee-member CCs plus the Social Director address."""
    addresses = [
        membership.user.email
        for membership in proposal.committee.memberships
        if membership.user.email != proposal.submitter.email
    ]
    settings = get_settings()
    addresses.append(
        settings.welfare_resend_cc_email
        if committee_portfolio(proposal.committee) == Portfolio.welfare
        else settings.resend_cc_email
    )
    return list(dict.fromkeys(addresses))


def _proposal(db: Session, proposal_id: int, user: User) -> Proposal:
    proposal = db.get(Proposal, proposal_id)
    if not proposal:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Proposal not found")
    if user.role != UserRole.admin and (proposal.committee_id not in user.committee_ids or proposal.submitted_by != user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Proposal not found")
    if proposal.status != ProposalStatus.in_review:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Email can only be prepared for an in-review proposal")
    if not sends_confirmation_email(proposal.committee, proposal.category):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Confirmation emails are not enabled for this proposal")
    return proposal


def _generated(proposal: Proposal) -> tuple[str, str]:
    disposable = proposal.disposable_request
    committee = proposal.committee
    if not committee.rf_name or not committee.rf_email:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"RF contact details have not been configured for {committee.name}",
        )
    subject = f"[{committee.name}] Event Proposal — {proposal.title}"
    lines = [
        f"Dear {proposal.submitter.display_name or proposal.submitter.email},",
        "",
        f"Your event proposal for {proposal.title} for {committee.name} has been reviewed and submitted. Here is a summary:",
        "",
        f"Event: {proposal.title}",
        f"Date: {proposal.event_date or 'Not set'}",
        f"Time: {proposal.event_time.strftime('%H:%M') if proposal.event_time else 'Not set'}",
        f"Description: {proposal.description or 'Not provided'}",
    ]
    if disposable and disposable.status == DisposableStatus.approved:
        lines += [
            "",
            f"Hall disposables have been approved for collection on {disposable.collection_date}:",
            f"  - Plates: {disposable.plates}",
            f"  - Cups: {disposable.cups}",
            f"  - Bowls: {disposable.bowls}",
            f"  - Forks: {disposable.forks}",
            f"  - Spoons: {disposable.spoons}",
        ]
    lines += [
        "",
        "Please copy and paste everything below the line into a new email to your RF. Please reattach the PDF below.",
        "",
        "-" * 72,
        "",
        f"To: {committee.rf_email}",
        f"CC: {', '.join(_committee_ccs(proposal))}",
        f"Subject: {subject}",
        "",
        f"Dear {committee.rf_name},",
        "",
        f"Here is the proposal for {proposal.title} happening on {proposal.event_date or 'a date to be confirmed'} at {proposal.event_time.strftime('%H:%M') if proposal.event_time else 'a time to be confirmed'} for your approval! Do let me know your comments. Many thanks!",
        "",
        "Best regards,",
        proposal.submitter.display_name or proposal.submitter.email,
        committee.name,
    ]
    return subject, "\n".join(lines)


def _get_or_create(db: Session, proposal: Proposal) -> EmailDraft:
    draft = db.query(EmailDraft).filter(EmailDraft.proposal_id == proposal.id).first()
    if draft:
        return draft
    subject, body = _generated(proposal)
    draft = EmailDraft(proposal_id=proposal.id, recipient=proposal.submitter.email, subject=subject, body=body)
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


@router.get("/preview/{proposal_id}", response_model=EmailDraftOut)
def preview_email(proposal_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> EmailDraft:
    return _get_or_create(db, _proposal(db, proposal_id, user))


@router.patch("/preview/{proposal_id}", response_model=EmailDraftOut)
def edit_email(
    proposal_id: int,
    req: EmailDraftUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> EmailDraft:
    proposal = _proposal(db, proposal_id, _admin)
    draft = _get_or_create(db, proposal)
    draft.subject = req.subject
    draft.body = req.body
    db.commit()
    db.refresh(draft)
    return draft


@router.post("/send/{proposal_id}", response_model=EmailDraftOut)
async def send_proposal_email(
    proposal_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> EmailDraft:
    proposal = _proposal(db, proposal_id, admin)
    draft = _get_or_create(db, proposal)
    # Keep outbound mail link-free to reduce the chance of organisational filtering.
    # Apply this at send time as well as in the generated template because admins can
    # edit drafts before sending.
    safe_subject = _without_links(draft.subject)
    safe_body = _without_links(draft.body)
    if safe_subject != draft.subject or safe_body != draft.body:
        draft.subject = safe_subject
        draft.body = safe_body
        db.commit()
        db.refresh(draft)
    attachment, large_pdf_link = await proposal_pdf_attachment(proposal)
    body = draft.body
    if large_pdf_link:
        body += f"\n\nThe proposal PDF is too large to attach. Download it here: {large_pdf_link}"
    await send_email(
        to=draft.recipient,
        subject=draft.subject,
        body=body,
        cc=_committee_ccs(proposal),
        attachment=attachment,
    )
    proposal.status = ProposalStatus.submitted
    db.commit()
    db.refresh(draft)
    await notify_user_email_sent(proposal.submitter.telegram_id, proposal.title)
    return draft
