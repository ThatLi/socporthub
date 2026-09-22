from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from api.models import Portfolio, ProposalCategory, ProposalStatus, ReminderDirection, ReminderTargetType, UserRole, UserStatus


# --- Auth ---


class ValidateResponse(BaseModel):
    registered: bool
    user: "UserOut | None" = None


class RegisterRequest(BaseModel):
    email: EmailStr
    telegram_username: str
    display_name: str | None = None


class BugReportCreate(BaseModel):
    message: str


# --- Users ---


class CommitteeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    color: str
    portfolio: Portfolio | None = None
    rf_name: str | None = None
    rf_email: EmailStr | None = None


class CommitteeFormOut(BaseModel):
    form_key: str
    committee_id: int | None = None
    committee_name: str
    url: str | None = None
    fields: dict[str, dict] = {}
    sections: list[str] = []


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    telegram_id: int
    telegram_username: str | None
    email: str
    display_name: str | None
    role: UserRole
    status: UserStatus
    created_at: datetime
    committees: list[CommitteeOut] = []


class UserUpdateRequest(BaseModel):
    email: EmailStr | None = None
    telegram_username: str | None = None
    status: UserStatus | None = None
    committee_ids: list[int] | None = None


# --- Proposals ---


class ProposalCreateRequest(BaseModel):
    title: str
    category: ProposalCategory = ProposalCategory.event
    description: str | None = None
    doc_link: str | None = None
    blast_message: str | None = None
    save_draft: bool = False
    event_date: date | None = None
    event_time: time | None = None
    requested_ccas: list[str] = []
    external_form_data: dict[str, dict] = {}


class ProposalUpdateRequest(BaseModel):
    category: ProposalCategory | None = None
    title: str | None = None
    description: str | None = None
    doc_link: str | None = None
    blast_message: str | None = None
    event_date: date | None = None
    event_time: time | None = None
    status: ProposalStatus | None = None
    # Admin-only escape hatch for submitting a proposal when the email/PDF
    # workflow is unavailable (for example, because the generated PDF is too large).
    send_email: bool = True
    # Optional note attached to a status change (e.g. why it was sent back to
    # needs_action) — stored as a comment and included in the Telegram notification.
    comment: str | None = None
    requested_ccas: list[str] | None = None
    external_form_data: dict[str, dict] | None = None


class ProposalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    committee_id: int
    committee_name: str
    portfolio: Portfolio
    submitted_by: int
    submitter_name: str | None
    submitter_telegram_username: str | None
    title: str
    category: ProposalCategory
    description: str | None
    doc_link: str | None
    blast_message: str | None
    poster_filename: str | None
    poster_content_type: str | None
    status: ProposalStatus
    event_date: date | None
    event_time: str | None
    created_at: datetime
    updated_at: datetime
    latest_comment_at: datetime | None = None
    unread_comment_count: int = 0
    requested_ccas: list[str] = []
    external_form_data: dict[str, dict] = {}
    grading_available: bool = False


class AiReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_title: str
    proposal_category: str
    proposal_description: str = ""
    document_text: str = Field(min_length=1)


class ProposalStatusCounts(BaseModel):
    draft: int = 0
    needs_action: int = 0
    in_review: int = 0
    submitted: int = 0
    finished: int = 0
    grading: int = 0
    final: int = 0


# --- Proposal comments ---


class ProposalCommentCreate(BaseModel):
    body: str
    reply_to_comment_id: int | None = None


class ProposalCommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    proposal_id: int
    author_id: int
    author_name: str | None = None
    author_role: UserRole | None = None
    reply_to_comment_id: int | None = None
    body: str
    created_at: datetime


class ProposalStatusHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    proposal_id: int
    changed_by: int
    changer_name: str | None = None
    from_status: ProposalStatus | None
    to_status: ProposalStatus
    created_at: datetime


# --- Disposables ---


class DisposableRequestUpsert(BaseModel):
    plates: int = 0
    cups: int = 0
    bowls: int = 0
    forks: int = 0
    spoons: int = 0
    collection_date: date
    collection_time: str | None = None


class DisposableRequestUpdate(BaseModel):
    approved: bool


class DisposableRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    proposal_id: int
    proposal_title: str
    committee_name: str
    requested_by: int
    requester_name: str | None
    plates: int
    cups: int
    bowls: int
    forks: int
    spoons: int
    collection_date: date
    collection_time: str | None
    approved: bool
    created_at: datetime


# --- Calendar ---


class CalendarEventCreate(BaseModel):
    title: str
    description: str | None = None
    event_date: date
    committee_id: int | None = None  # required for admins, who have no committee of their own


class CalendarEventUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    event_date: date | None = None


class CalendarEventOut(BaseModel):
    id: int
    title: str
    description: str | None
    date: str
    committee_id: int
    committee_name: str
    committee_color: str


class CalendarFeedUrlOut(BaseModel):
    url: str


class EmailDraftUpdate(BaseModel):
    subject: str
    body: str


class EmailDraftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    proposal_id: int
    recipient: str
    subject: str
    body: str
    created_at: datetime
    updated_at: datetime


class ReminderCreate(BaseModel):
    message: str
    target_type: ReminderTargetType = ReminderTargetType.general
    target_id: int | None = None


class ReminderBroadcastCreate(BaseModel):
    message: str
    committee_ids: list[int] = Field(min_length=1)
    deadline: datetime | None = None


class ReminderCommitteeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class ReminderRecipientOut(BaseModel):
    user_id: int
    name: str
    is_read: bool
    is_done: bool
    completed_at: datetime | None


class ReminderOut(BaseModel):
    id: int
    direction: ReminderDirection
    from_user: int
    sender_name: str | None
    message: str
    target_type: ReminderTargetType
    target_id: int | None
    is_read: bool
    is_done: bool | None = None
    deadline: datetime | None = None
    committees: list[ReminderCommitteeOut] = []
    recipients: list[ReminderRecipientOut] | None = None
    created_at: datetime


class ReminderUnreadCount(BaseModel):
    count: int
