"""Handles incoming Telegram webhook updates (commands only, no WebApp interaction —
that goes through the REST API instead)."""

import logging
import re
from datetime import date, time
from html import escape

from api.config import get_settings
from api.database import SessionLocal
from api.models import Committee, DisposableRequest, Proposal, ProposalStatus, Reminder, ReminderTargetType, User, UserCommittee, UserRole, UserStatus
from api.portfolio import admin_committee_filter, committee_portfolio
from api.services.telegram import send_message, webapp_open_markup
from bot.notifications import notify_admins_new_disposable_request

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "<b>Social Port Hub</b>\n\n"
    "/start — Open the app\n"
    "/register — Register your name, Telegram handle and CCA\n"
    "/disposable — Request disposable items without opening the app\n"
    "/status — Show your proposal status\n"
    "/remind <message> — Nudge the admin\n"
    "/help — Show this message\n\n"
    "Use the app to submit event proposals, track their status, and manage your "
    "committee's activity."
)

_conversation: dict[int, dict] = {}
_ITEMS = ("plates", "cups", "bowls", "forks", "spoons")
_HANDLE_RE = re.compile(r"^@?[A-Za-z0-9_]{5,32}$")


async def handle_update(update: dict) -> None:
    message = update.get("message")
    if not message:
        return  # ignore non-message updates (e.g. callback_query) for now

    chat_id = message.get("chat", {}).get("id")
    text = (message.get("text") or "").strip()
    if chat_id is None:
        return

    command, _, argument = text.partition(" ")
    if command == "/register":
        await _handle_register_start(chat_id)
    elif command == "/disposable":
        await _handle_disposable_start(chat_id)
    elif chat_id in _conversation and text and not text.startswith("/"):
        await _handle_conversation(chat_id, text)
    elif command == "/start":
        await _handle_start(chat_id)
    elif command == "/help":
        await send_message(chat_id, HELP_TEXT)
    elif command == "/status":
        await _handle_status(chat_id)
    elif command == "/remind":
        await _handle_remind(chat_id, argument.strip())
    elif command == "/pending":
        await _handle_pending(chat_id)
    elif command == "/approve":
        await _handle_approve(chat_id, argument.strip())
    elif command == "/reject":
        await _handle_reject(chat_id, argument.strip())


async def _handle_start(chat_id: int) -> None:
    settings = get_settings()
    if settings.telegram_webapp_url:
        await send_message(
            chat_id,
            "Use the button below to acces the Social Port Hub!\n\n💡 Tip: Pin this message so you can easily open the app anytime.",
            reply_markup=webapp_open_markup(settings.telegram_webapp_url),
        )
    else:
        await send_message(chat_id, "Welcome to Social Port Hub! The app link isn't configured yet.")


def _user(db, chat_id: int):
    return db.query(User).filter(User.telegram_id == chat_id).first()


async def _handle_register_start(chat_id: int) -> None:
    with SessionLocal() as db:
        existing = _user(db, chat_id)
        if existing:
            await send_message(chat_id, f"You are already registered as <b>{escape(existing.display_name or 'user')}</b> ({existing.status.value}).")
            return
    _conversation[chat_id] = {"flow": "register", "step": "name"}
    await send_message(chat_id, "Let's register you. What is your full name?")


async def _handle_disposable_start(chat_id: int) -> None:
    with SessionLocal() as db:
        user = _user(db, chat_id)
        if not user:
            await send_message(chat_id, "Please use /register first.")
            return
        if user.status != UserStatus.approved:
            await send_message(chat_id, f"Your registration is {user.status.value}. You can request disposables once an admin approves it.")
            return
    _conversation[chat_id] = {"flow": "disposable", "step": "quantities"}
    await send_message(chat_id, "Send quantities in this order: plates, cups, bowls, forks, spoons. Example: 20 30 0 25 25")


async def _handle_conversation(chat_id: int, text: str) -> None:
    state = _conversation.get(chat_id)
    if not state:
        return
    if state["flow"] == "register":
        await _handle_registration_step(chat_id, state, text)
    else:
        await _handle_disposable_step(chat_id, state, text)


async def _handle_registration_step(chat_id: int, state: dict, text: str) -> None:
    if state["step"] == "name":
        if not 1 <= len(text) <= 100:
            await send_message(chat_id, "Please send a name between 1 and 100 characters.")
            return
        state.update(name=text, step="username")
        await send_message(chat_id, "What is your Telegram username? Include the @, for example @jane_doe.")
        return
    if state["step"] == "username":
        if not _HANDLE_RE.fullmatch(text):
            await send_message(chat_id, "That doesn't look like a valid Telegram handle. Try @username.")
            return
        state.update(username=text if text.startswith("@") else f"@{text}", step="cca")
        await send_message(chat_id, "What is your CCA? Send the CCA name exactly as listed in Social Port Hub.")
        return

    with SessionLocal() as db:
        committee = db.query(Committee).filter(Committee.name.ilike(text)).first()
        if not committee:
            names = ", ".join(c.name for c in db.query(Committee).order_by(Committee.name).all())
            await send_message(chat_id, f"I couldn't find that CCA. Available CCAs: {escape(names)}")
            return
        settings = get_settings()
        email = f"telegram-{chat_id}@telegram.local"
        user = User(
            telegram_id=chat_id, email=email, telegram_username=state["username"],
            display_name=state["name"], role=UserRole.admin if chat_id in settings.all_admin_telegram_id_set else UserRole.user,
            status=UserStatus.approved if chat_id in settings.all_admin_telegram_id_set else UserStatus.pending,
        )
        db.add(user)
        db.flush()
        db.add(UserCommittee(user_id=user.id, committee_id=committee.id))
        db.commit()
    _conversation.pop(chat_id, None)
    if user.status == UserStatus.pending:
        for admin_id in settings.admin_telegram_id_set:
            await send_message(admin_id, f"📝 <b>New Telegram registration</b>\nName: {escape(user.display_name)}\nTelegram: {escape(user.telegram_username)}\nCCA: {escape(committee.name)}")
        await send_message(chat_id, "Registration submitted. An admin will review it and message you when approved.")
    else:
        await send_message(chat_id, "Registration complete. You can now use /disposable.")


async def _handle_disposable_step(chat_id: int, state: dict, text: str) -> None:
    step = state["step"]
    if step == "quantities":
        try:
            values = [int(part) for part in text.replace(",", " ").split()]
            if len(values) != len(_ITEMS) or any(value < 0 for value in values):
                raise ValueError
        except ValueError:
            await send_message(chat_id, "Send exactly five non-negative whole numbers: plates cups bowls forks spoons.")
            return
        state.update(quantities=dict(zip(_ITEMS, values)), step="date")
        await send_message(chat_id, "What date is collection? Use YYYY-MM-DD.")
    elif step == "date":
        try:
            state["date"] = date.fromisoformat(text)
        except ValueError:
            await send_message(chat_id, "Please use YYYY-MM-DD, for example 2026-10-15.")
            return
        state["step"] = "time"
        await send_message(chat_id, "What time is collection? Use HH:MM, or send - if no specific time.")
    elif step == "time":
        if text == "-":
            state["time"] = None
        else:
            try:
                state["time"] = time.fromisoformat(text)
            except ValueError:
                await send_message(chat_id, "Please use HH:MM, for example 18:30, or send -.")
                return
        state["step"] = "description"
        await send_message(chat_id, "Finally, send a short description explaining what the disposables are for. This will be sent to the admin for vetting.")
    else:
        if not text.strip():
            await send_message(chat_id, "Please include a description for the admin to vet.")
            return
        await _save_telegram_disposable(chat_id, state, text.strip())
        _conversation.pop(chat_id, None)


async def _save_telegram_disposable(chat_id: int, state: dict, description: str) -> None:
    with SessionLocal() as db:
        user = _user(db, chat_id)
        if not user or user.status != UserStatus.approved:
            await send_message(chat_id, "Your account is no longer approved. Please contact an admin.")
            return
        cca = user.committee_memberships[0].committee.name if user.committee_memberships else None
        request = DisposableRequest(
            requested_by=user.id, plates=state["quantities"]["plates"], cups=state["quantities"]["cups"],
            bowls=state["quantities"]["bowls"], forks=state["quantities"]["forks"], spoons=state["quantities"]["spoons"],
            collection_date=state["date"], collection_time=state["time"], requester_cca=cca, description=description,
        )
        db.add(request)
        db.commit()
        requester_name, username = user.display_name or user.email, user.telegram_username
    await notify_admins_new_disposable_request(
        "Standalone Telegram request", requester_name, committee_portfolio(user.committee_memberships[0].committee) if user.committee_memberships else None,
        telegram_username=username, cca=cca, quantities=state["quantities"], collection_date=state["date"].isoformat(),
        collection_time=state["time"].isoformat() if state["time"] else None, description=description,
    )
    await send_message(chat_id, "Your disposable request was sent to the admin for review.")


async def _handle_status(chat_id: int) -> None:
    with SessionLocal() as db:
        user = _user(db, chat_id)
        if not user or user.status.value != "approved":
            await send_message(chat_id, "You don't have an approved Social Port Hub account yet.")
            return
        query = db.query(Proposal)
        if user.role != UserRole.admin:
            query = query.filter(Proposal.committee_id.in_(user.committee_ids))
        else:
            query = query.join(Proposal.committee).filter(admin_committee_filter(user))
        proposals = query.order_by(Proposal.updated_at.desc()).limit(10).all()
        if not proposals:
            await send_message(chat_id, "You have no visible proposals yet.")
            return
        lines = [f"• {p.title}: {p.status.value}" for p in proposals]
    await send_message(chat_id, "<b>Your proposal status</b>\n" + "\n".join(lines))


async def _handle_remind(chat_id: int, message: str) -> None:
    if not message:
        await send_message(chat_id, "Usage: /remind Please review my proposal")
        return
    with SessionLocal() as db:
        user = _user(db, chat_id)
        if not user or user.status.value != "approved":
            await send_message(chat_id, "You need an approved account to send reminders.")
            return
        reminder = Reminder(from_user=user.id, message=message, target_type=ReminderTargetType.general)
        db.add(reminder)
        db.commit()
        sender = user.display_name or user.email
    from bot.notifications import notify_admins_reminder
    portfolio = committee_portfolio(user.committee_memberships[0].committee) if user.committee_memberships else None
    await notify_admins_reminder(sender, message, portfolio)
    await send_message(chat_id, "Your reminder was sent to the admin.")


async def _handle_pending(chat_id: int) -> None:
    with SessionLocal() as db:
        user = _user(db, chat_id)
        if not user or user.role != UserRole.admin or user.status.value != "approved":
            return
        proposals_query = db.query(Proposal).filter(Proposal.status == ProposalStatus.needs_action)
        disposables_query = db.query(DisposableRequest).filter(DisposableRequest.approved.is_(False), DisposableRequest.rejected.is_(False))
        proposals = proposals_query.join(Proposal.committee).filter(admin_committee_filter(user)).all()
        disposables = disposables_query.all()
    lines = [f"• Proposal #{p.id}: {p.title}" for p in proposals]
    lines += [f"• Disposables #{d.id}: {d.proposal.title if d.proposal else 'Standalone Telegram request'}" for d in disposables]
    await send_message(chat_id, "<b>Pending items</b>\n" + ("\n".join(lines) if lines else "Nothing pending."))


async def _handle_approve(chat_id: int, argument: str) -> None:
    try:
        disposable_id = int(argument)
    except ValueError:
        await send_message(chat_id, "Usage: /approve <disposable id>")
        return
    with SessionLocal() as db:
        user = _user(db, chat_id)
        disposable = db.get(DisposableRequest, disposable_id) if user and user.role == UserRole.admin else None
        if not disposable:
            await send_message(chat_id, "Disposable request not found.")
            return
        disposable.approved = True
        disposable.rejected = False
        db.commit()
        recipient_id = disposable.requester.telegram_id
        proposal_title = disposable.proposal.title if disposable.proposal else "your Telegram request"
        collection_date = disposable.collection_date.isoformat()
    from bot.notifications import notify_user_disposable_approved
    await notify_user_disposable_approved(recipient_id, proposal_title, collection_date)
    await send_message(chat_id, f"Disposable request #{disposable_id} approved.")


async def _handle_reject(chat_id: int, argument: str) -> None:
    try:
        disposable_id = int(argument)
    except ValueError:
        await send_message(chat_id, "Usage: /reject <disposable id>")
        return
    with SessionLocal() as db:
        user = _user(db, chat_id)
        disposable = db.get(DisposableRequest, disposable_id) if user and user.role == UserRole.admin else None
        if not disposable:
            await send_message(chat_id, "Disposable request not found.")
            return
        disposable.approved = False
        disposable.rejected = True
        db.commit()
        recipient_id = disposable.requester.telegram_id
        request_label = disposable.proposal.title if disposable.proposal else "your Telegram request"
    from bot.notifications import notify_user_disposable_rejected
    await notify_user_disposable_rejected(recipient_id, request_label)
    await send_message(chat_id, f"Disposable request #{disposable_id} rejected.")
