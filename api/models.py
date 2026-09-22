import enum
from datetime import date, datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Time,
    Text,
    func,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.database import Base


class UserRole(str, enum.Enum):
    user = "user"
    admin = "admin"


class UserStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class ProposalStatus(str, enum.Enum):
    draft = "draft"
    needs_action = "needs_action"
    in_review = "in_review"
    submitted = "submitted"
    finished = "finished"
    grading = "grading"
    final = "final"


class ProposalCategory(str, enum.Enum):
    event = "event"
    initiative = "initiative"
    welfare = "welfare"
    decor = "decor"
    pantry_cleaning = "pantry_cleaning"
    merch = "merch"
    pubs = "pubs"


class ReminderTargetType(str, enum.Enum):
    proposal = "proposal"
    disposable = "disposable"
    general = "general"


class ReminderDirection(str, enum.Enum):
    to_admin = "to_admin"
    to_user = "to_user"


class Portfolio(str, enum.Enum):
    social = "social"
    welfare = "welfare"


# Server-side allowed forward transitions for proposal status.
# needs_action is reached only when an admin sends an in-review proposal back — it is
# not a step every submission passes through. A submission (from draft, or fresh) goes
# straight to in_review; the owner resubmits from needs_action back to in_review too.
PROPOSAL_STATUS_TRANSITIONS: dict[ProposalStatus, set[ProposalStatus]] = {
    ProposalStatus.draft: {ProposalStatus.in_review},
    ProposalStatus.needs_action: {ProposalStatus.in_review},
    ProposalStatus.in_review: {ProposalStatus.submitted, ProposalStatus.needs_action, ProposalStatus.finished},
    ProposalStatus.submitted: {ProposalStatus.finished},
    # Grading transitions are handled by the grading routes, which validate forms.
    ProposalStatus.finished: set(),
    ProposalStatus.grading: set(),
    ProposalStatus.final: set(),
}


class Committee(Base):
    __tablename__ = "committees"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    color: Mapped[str] = mapped_column(String(7), nullable=False)
    portfolio: Mapped[Portfolio | None] = mapped_column(Enum(Portfolio, native_enum=False), nullable=True)
    # Nullable in the ORM so lightweight test/dev databases created from metadata
    # remain compatible; migration 0008 makes these required in PostgreSQL.
    rf_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rf_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    memberships: Mapped[list["UserCommittee"]] = relationship(back_populates="committee")
    proposals: Mapped[list["Proposal"]] = relationship(back_populates="committee")
    calendar_events: Mapped[list["CalendarEvent"]] = relationship(back_populates="committee")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    telegram_username: Mapped[str | None] = mapped_column(String(33), unique=True, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(100))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, native_enum=False), default=UserRole.user)
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus, native_enum=False), default=UserStatus.pending)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    committee_memberships: Mapped[list["UserCommittee"]] = relationship(back_populates="user")
    proposals: Mapped[list["Proposal"]] = relationship(back_populates="submitter")

    @property
    def committee_ids(self) -> set[int]:
        return {m.committee_id for m in self.committee_memberships}


class UserCommittee(Base):
    __tablename__ = "user_committees"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    committee_id: Mapped[int] = mapped_column(ForeignKey("committees.id"), primary_key=True)

    user: Mapped["User"] = relationship(back_populates="committee_memberships")
    committee: Mapped["Committee"] = relationship(back_populates="memberships")


class Proposal(Base):
    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    committee_id: Mapped[int] = mapped_column(ForeignKey("committees.id"), nullable=False)
    submitted_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[ProposalCategory] = mapped_column(
        Enum(ProposalCategory, native_enum=False), default=ProposalCategory.event, nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text)
    doc_link: Mapped[str | None] = mapped_column(Text)
    blast_message: Mapped[str | None] = mapped_column(Text)
    poster_filename: Mapped[str | None] = mapped_column(String(255))
    poster_content_type: Mapped[str | None] = mapped_column(String(100))
    poster_data: Mapped[bytes | None] = mapped_column(LargeBinary)
    drive_folder_id: Mapped[str | None] = mapped_column(String(255))
    drive_pdf_id: Mapped[str | None] = mapped_column(String(255))
    drive_ready: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    drive_error: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ProposalStatus] = mapped_column(
        Enum(ProposalStatus, native_enum=False), default=ProposalStatus.needs_action
    )
    event_date: Mapped[date | None] = mapped_column(Date)
    event_time: Mapped[time | None] = mapped_column(Time)
    # JSON-encoded names of external CCAs whose forms are part of this request.
    requested_ccas: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON-encoded manual values for configured external form fields.
    external_form_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    committee: Mapped["Committee"] = relationship(back_populates="proposals")
    submitter: Mapped["User"] = relationship(back_populates="proposals")
    comments: Mapped[list["ProposalComment"]] = relationship(
        back_populates="proposal", order_by="ProposalComment.created_at"
    )
    disposable_request: Mapped["DisposableRequest | None"] = relationship(back_populates="proposal")


class ProposalComment(Base):
    __tablename__ = "proposal_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("proposals.id"), nullable=False)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    reply_to_comment_id: Mapped[int | None] = mapped_column(ForeignKey("proposal_comments.id"), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    proposal: Mapped["Proposal"] = relationship(back_populates="comments")
    author: Mapped["User"] = relationship()
    reply_to: Mapped["ProposalComment | None"] = relationship(
        remote_side="ProposalComment.id", back_populates="replies"
    )
    replies: Mapped[list["ProposalComment"]] = relationship(back_populates="reply_to")


class ProposalCommentRead(Base):
    __tablename__ = "proposal_comment_reads"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("proposals.id"), primary_key=True)
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProposalStatusHistory(Base):
    __tablename__ = "proposal_status_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("proposals.id"), nullable=False)
    changed_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    from_status: Mapped[ProposalStatus | None] = mapped_column(Enum(ProposalStatus, native_enum=False))
    to_status: Mapped[ProposalStatus] = mapped_column(Enum(ProposalStatus, native_enum=False), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    proposal: Mapped["Proposal"] = relationship()
    changer: Mapped["User"] = relationship()


class ProposalGrading(Base):
    __tablename__ = "proposal_gradings"

    proposal_id: Mapped[int] = mapped_column(ForeignKey("proposals.id"), primary_key=True)
    started_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    user_data: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    admin_data: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    user_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    admin_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    admin_author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    evidence_done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    proposal: Mapped["Proposal"] = relationship()


class GradingNotification(Base):
    """Durable per-recipient reminder delivery."""
    __tablename__ = "grading_notifications"
    __table_args__ = (UniqueConstraint("proposal_id", "milestone", "telegram_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("proposals.id"), nullable=False)
    milestone: Mapped[str] = mapped_column(String(32), nullable=False)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DisposableRequest(Base):
    __tablename__ = "disposable_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("proposals.id"), unique=True, nullable=False)
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    plates: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cups: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bowls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    forks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    spoons: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    collection_date: Mapped[date] = mapped_column(Date, nullable=False)
    collection_time: Mapped[time | None] = mapped_column(Time)
    approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    proposal: Mapped["Proposal"] = relationship(back_populates="disposable_request")
    requester: Mapped["User"] = relationship()


class CalendarEvent(Base):
    __tablename__ = "calendar_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    committee_id: Mapped[int] = mapped_column(ForeignKey("committees.id"), nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    committee: Mapped["Committee"] = relationship(back_populates="calendar_events")
    creator: Mapped["User"] = relationship()


class Reminder(Base):
    """A user->admin nudge (direction=to_admin, the original shape), or an
    admin->CCA broadcast (direction=to_user). A broadcast's actual recipients and
    their per-user read/done state live in ReminderRecipient — this row is the
    composed message plus (for a broadcast) which committees it targeted."""
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(primary_key=True)
    from_user: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[ReminderDirection] = mapped_column(
        Enum(ReminderDirection, native_enum=False), default=ReminderDirection.to_admin, nullable=False
    )
    target_type: Mapped[ReminderTargetType] = mapped_column(
        Enum(ReminderTargetType, native_enum=False), nullable=False
    )
    target_id: Mapped[int | None] = mapped_column(Integer)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Only set on a to_user broadcast; drives the scheduled 3-day/1-day nudges below.
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    sender: Mapped["User"] = relationship()
    target_committees: Mapped[list["ReminderTargetCommittee"]] = relationship(
        back_populates="reminder", cascade="all, delete-orphan"
    )
    recipients: Mapped[list["ReminderRecipient"]] = relationship(
        back_populates="reminder", cascade="all, delete-orphan"
    )


class ReminderTargetCommittee(Base):
    """Which CCAs an admin broadcast (Reminder.direction=to_user) was sent to."""
    __tablename__ = "reminder_target_committees"

    reminder_id: Mapped[int] = mapped_column(ForeignKey("reminders.id"), primary_key=True)
    committee_id: Mapped[int] = mapped_column(ForeignKey("committees.id"), primary_key=True)

    reminder: Mapped["Reminder"] = relationship(back_populates="target_committees")
    committee: Mapped["Committee"] = relationship()


class ReminderRecipient(Base):
    """Per-recipient inbox state for a broadcast reminder: read + done, independent
    of every other recipient's state on the same broadcast."""
    __tablename__ = "reminder_recipients"
    __table_args__ = (UniqueConstraint("reminder_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    reminder_id: Mapped[int] = mapped_column(ForeignKey("reminders.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_done: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    reminder: Mapped["Reminder"] = relationship(back_populates="recipients")
    user: Mapped["User"] = relationship()


class ReminderNudge(Base):
    """Durable per-recipient scheduled nudge delivery (3-day / 1-day windows before
    a broadcast reminder's deadline) — same shape as GradingNotification, so a
    restart or slow scheduler tick can't double-send."""
    __tablename__ = "reminder_nudges"
    __table_args__ = (UniqueConstraint("reminder_recipient_id", "milestone"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    reminder_recipient_id: Mapped[int] = mapped_column(ForeignKey("reminder_recipients.id"), nullable=False)
    milestone: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    recipient: Mapped["ReminderRecipient"] = relationship()


class EmailDraft(Base):
    __tablename__ = "email_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("proposals.id"), unique=True, nullable=False)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    proposal: Mapped["Proposal"] = relationship()
