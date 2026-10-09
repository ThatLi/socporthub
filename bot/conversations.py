"""Persistent Telegram conversations for registration and disposable requests."""

import json
import re
from datetime import date, datetime, time
from html import escape
from zoneinfo import ZoneInfo

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.config import get_settings
from api.models import (
    BotConversation,
    Committee,
    DisposableRequest,
    DisposableStatus,
    User,
    UserCommittee,
    UserRole,
    UserStatus,
)
from api.portfolio import admin_can_access_committee, committee_portfolio
from api.services.disposables import decide_disposable
from api.services.telegram import answer_callback_query, send_message
from bot.notifications import (
    notify_admins_new_disposable_request,
    notify_admins_registration_request,
    notify_user_disposable_approved,
    notify_user_disposable_rejected,
    notify_user_registration_approved,
    notify_user_registration_rejected_with_reason,
)

ITEMS = ("cups", "bowls", "spoons", "forks", "plates")
PAGE_SIZE = 8
EMAIL = TypeAdapter(EmailStr)
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _payload(conversation: BotConversation) -> dict:
    try:
        value = json.loads(conversation.payload)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _save(conversation: BotConversation, data: dict) -> None:
    conversation.payload = json.dumps(data)


def _cancel_markup() -> dict:
    return {"inline_keyboard": [[{"text": "Cancel", "callback_data": "conv:cancel"}]]}


def _confirmation_markup(flow: str) -> dict:
    prefix = "reg" if flow == "register" else "disp"
    return {
        "inline_keyboard": [
            [
                {"text": "Edit", "callback_data": f"{prefix}:edit-menu"},
                {"text": "Submit", "callback_data": f"{prefix}:submit"},
            ],
            [{"text": "Cancel", "callback_data": "conv:cancel"}],
        ]
    }


def _conversation(db: Session, telegram_id: int) -> BotConversation | None:
    return db.get(BotConversation, telegram_id)


async def cancel_conversation(db: Session, telegram_id: int) -> bool:
    conversation = _conversation(db, telegram_id)
    if not conversation:
        await send_message(telegram_id, "There is no active request to cancel.")
        return False
    db.delete(conversation)
    db.commit()
    await send_message(telegram_id, "Cancelled. You can start again at any time.")
    return True


async def start_registration(db: Session, telegram_id: int, username: str | None) -> None:
    active = _conversation(db, telegram_id)
    if active:
        await send_message(telegram_id, "You already have an active flow. Continue it or use /cancel first.", _cancel_markup())
        return
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if user and user.status == UserStatus.approved:
        await send_message(telegram_id, "You are already registered and approved.")
        return
    if user and user.status == UserStatus.pending:
        await send_message(telegram_id, "Your registration is still pending administrator approval.")
        return
    conversation = BotConversation(
        telegram_id=telegram_id,
        flow="register",
        step="name",
        payload=json.dumps({"telegram_username": f"@{username}" if username else None}),
    )
    db.add(conversation)
    db.commit()
    await send_message(telegram_id, "What is your name?", _cancel_markup())


async def start_disposable(db: Session, telegram_id: int) -> None:
    active = _conversation(db, telegram_id)
    if active:
        await send_message(telegram_id, "You already have an active flow. Continue it or use /cancel first.", _cancel_markup())
        return
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if not user or user.status != UserStatus.approved:
        await send_message(telegram_id, "You need an approved account before requesting disposables. Use /register first.")
        return
    committees = [membership.committee for membership in user.committee_memberships]
    if not committees:
        await send_message(telegram_id, "Your account has no CCA assigned. Ask an administrator to update it.")
        return
    data: dict = {}
    if len(committees) == 1:
        data["committee_id"] = committees[0].id
        step = "activity_name"
    else:
        step = "committee"
    conversation = BotConversation(
        telegram_id=telegram_id,
        flow="disposable",
        step=step,
        payload=json.dumps(data),
    )
    db.add(conversation)
    db.commit()
    if step == "committee":
        await _send_committee_picker(telegram_id, committees, "disp", 0)
    else:
        await send_message(telegram_id, "What is the activity or purpose of this request?", _cancel_markup())


async def handle_conversation_message(db: Session, telegram_id: int, text: str) -> bool:
    conversation = _conversation(db, telegram_id)
    if not conversation:
        return False
    data = _payload(conversation)
    value = text.strip()

    if conversation.flow == "admin_reject":
        if len(value) > 1000:
            await send_message(telegram_id, "The reason must be 1,000 characters or fewer.", _cancel_markup())
            return True
        await _finish_admin_rejection(db, conversation, value or None)
        return True
    if conversation.flow == "register":
        await _registration_message(db, conversation, data, value)
        return True
    if conversation.flow == "disposable":
        await _disposable_message(db, conversation, data, value)
        return True
    return False


async def _registration_message(db: Session, conversation: BotConversation, data: dict, value: str) -> None:
    editing = bool(data.pop("_editing", False))
    if conversation.step == "name":
        if not value or len(value) > 100:
            await send_message(conversation.telegram_id, "Enter a name between 1 and 100 characters.", _cancel_markup())
            return
        data["display_name"] = value
        conversation.step = "confirm" if editing else "email"
        _save(conversation, data)
        db.commit()
        if editing:
            await _send_registration_confirmation(db, conversation)
        else:
            await send_message(conversation.telegram_id, "What is your email address?", _cancel_markup())
        return
    if conversation.step == "email":
        try:
            email = str(EMAIL.validate_python(value))
        except ValidationError:
            await send_message(conversation.telegram_id, "Enter a valid email address.", _cancel_markup())
            return
        existing = db.query(User).filter(User.email == email, User.telegram_id != conversation.telegram_id).first()
        if existing:
            await send_message(conversation.telegram_id, "That email is already registered.", _cancel_markup())
            return
        data["email"] = email
        conversation.step = "confirm" if editing else "committee"
        _save(conversation, data)
        db.commit()
        if editing:
            await _send_registration_confirmation(db, conversation)
        else:
            await _send_committee_picker(conversation.telegram_id, db.query(Committee).order_by(Committee.name).all(), "reg", 0)
        return
    await send_message(conversation.telegram_id, "Use the buttons shown for this step, or /cancel.", _cancel_markup())


async def _disposable_message(db: Session, conversation: BotConversation, data: dict, value: str) -> None:
    step = conversation.step
    editing = bool(data.pop("_editing", False))
    if step == "activity_name":
        if not value or len(value) > 255:
            await send_message(conversation.telegram_id, "Enter an activity name between 1 and 255 characters.", _cancel_markup())
            return
        data[step] = value
        conversation.step = "confirm" if editing else ITEMS[0]
    elif step in ITEMS:
        try:
            quantity = int(value)
            if quantity < 0:
                raise ValueError
        except ValueError:
            await send_message(conversation.telegram_id, "Enter a whole number of 0 or more.", _cancel_markup())
            return
        data[step] = quantity
        index = ITEMS.index(step)
        conversation.step = "confirm" if editing else (ITEMS[index + 1] if index + 1 < len(ITEMS) else "collection_date")
    elif step == "collection_date":
        parsed = None
        for pattern in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(value, pattern).date()
                break
            except ValueError:
                pass
        today = datetime.now(ZoneInfo(get_settings().app_timezone)).date()
        if not parsed or parsed < today:
            await send_message(conversation.telegram_id, "Enter today or a future date as DD/MM/YYYY or YYYY-MM-DD.", _cancel_markup())
            return
        if not any(data.get(item, 0) > 0 for item in ITEMS):
            conversation.step = ITEMS[0]
            db.commit()
            await send_message(conversation.telegram_id, "At least one quantity must be greater than zero. Enter cups again.", _cancel_markup())
            return
        data[step] = parsed.isoformat()
        conversation.step = "confirm" if editing else "collection_time"
    elif step == "collection_time":
        if not TIME_RE.fullmatch(value):
            await send_message(conversation.telegram_id, "Enter time in 24-hour HH:MM format.", _cancel_markup())
            return
        parsed_time = time.fromisoformat(value)
        data[step] = parsed_time.isoformat(timespec="minutes")
        conversation.step = "confirm" if editing else "description"
    elif step == "description":
        if len(value) > 2000:
            await send_message(conversation.telegram_id, "Description must be 2,000 characters or fewer.", _cancel_markup())
            return
        data[step] = value or None
        conversation.step = "confirm"
    else:
        await send_message(conversation.telegram_id, "Use the buttons shown for this step, or /cancel.", _cancel_markup())
        return

    _save(conversation, data)
    db.commit()
    if conversation.step == "confirm":
        await _send_disposable_confirmation(db, conversation)
    else:
        await _prompt_disposable_step(conversation.telegram_id, conversation.step)


async def _prompt_disposable_step(telegram_id: int, step: str) -> None:
    prompts = {
        "activity_name": "What is the activity or purpose of this request?",
        "cups": "How many cups are required?",
        "bowls": "How many bowls are required?",
        "spoons": "How many spoons are required?",
        "forks": "How many forks are required?",
        "plates": "How many plates are required?",
        "collection_date": "What is the collection date? Use DD/MM/YYYY or YYYY-MM-DD.",
        "collection_time": "What is the collection time? Use 24-hour HH:MM.",
        "description": "Add a description, or select Skip.",
    }
    markup = _cancel_markup()
    if step == "description":
        markup = {"inline_keyboard": [
            [{"text": "Skip", "callback_data": "disp:skip-description"}],
            [{"text": "Cancel", "callback_data": "conv:cancel"}],
        ]}
    await send_message(telegram_id, prompts[step], markup)


async def _send_committee_picker(telegram_id: int, committees: list[Committee], prefix: str, page: int) -> None:
    total_pages = max(1, (len(committees) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    rows = [[{"text": c.name, "callback_data": f"{prefix}:committee:{c.id}"}]
            for c in committees[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]]
    nav = []
    if page:
        nav.append({"text": "← Previous", "callback_data": f"{prefix}:committee-page:{page - 1}"})
    if page + 1 < total_pages:
        nav.append({"text": "Next →", "callback_data": f"{prefix}:committee-page:{page + 1}"})
    if nav:
        rows.append(nav)
    rows.append([{"text": "Cancel", "callback_data": "conv:cancel"}])
    await send_message(telegram_id, "Select your CCA:" if prefix == "reg" else "Select the CCA for this request:", {"inline_keyboard": rows})


async def _send_registration_confirmation(db: Session, conversation: BotConversation) -> None:
    data = _payload(conversation)
    committee = db.get(Committee, data.get("committee_id"))
    text = (
        "Please confirm your registration:\n\n"
        f"Name: <b>{escape(data.get('display_name', ''))}</b>\n"
        f"Email: {escape(data.get('email', ''))}\n"
        f"CCA: {escape(committee.name if committee else '')}"
    )
    await send_message(conversation.telegram_id, text, _confirmation_markup("register"))


async def _send_disposable_confirmation(db: Session, conversation: BotConversation) -> None:
    data = _payload(conversation)
    committee = db.get(Committee, data.get("committee_id"))
    quantities = ", ".join(f"{item.title()}: {data.get(item, 0)}" for item in ITEMS)
    text = (
        "Please confirm your disposable request:\n\n"
        f"CCA: {escape(committee.name if committee else '')}\n"
        f"Activity: <b>{escape(data.get('activity_name', ''))}</b>\n"
        f"{quantities}\n"
        f"Collection: {data.get('collection_date', '')} at {data.get('collection_time', '')}\n"
        f"Description: {escape(data.get('description') or 'None')}"
    )
    await send_message(conversation.telegram_id, text, _confirmation_markup("disposable"))


async def handle_callback(db: Session, callback: dict) -> None:
    callback_id = callback.get("id")
    actor_id = callback.get("from", {}).get("id")
    data = callback.get("data") or ""
    if callback_id:
        await answer_callback_query(callback_id)
    if actor_id is None:
        return
    if data == "conv:cancel":
        await cancel_conversation(db, actor_id)
        return
    if data == "admin:reject:skip:0":
        await handle_admin_rejection_skip(db, actor_id)
        return
    if data.startswith("admin:"):
        await _admin_callback(db, actor_id, data)
        return
    conversation = _conversation(db, actor_id)
    if not conversation:
        await send_message(actor_id, "This action is no longer active. Start again with /register or /disposable.")
        return
    prefix = "reg" if conversation.flow == "register" else "disp"
    if not data.startswith(prefix + ":"):
        await send_message(actor_id, "Finish or cancel your current flow first.", _cancel_markup())
        return
    await _conversation_callback(db, conversation, data)


async def _conversation_callback(db: Session, conversation: BotConversation, callback_data: str) -> None:
    parts = callback_data.split(":")
    if len(parts) < 2:
        return
    prefix, action = parts[0], parts[1]
    data = _payload(conversation)
    if action == "committee-page":
        if conversation.step != "committee" or len(parts) != 3:
            await send_message(conversation.telegram_id, "That button is no longer active.")
            return
        page = int(parts[2])
        if prefix == "reg":
            committees = db.query(Committee).order_by(Committee.name).all()
        else:
            user = db.query(User).filter(User.telegram_id == conversation.telegram_id).first()
            committees = [m.committee for m in user.committee_memberships] if user else []
        await _send_committee_picker(conversation.telegram_id, committees, prefix, page)
        return
    if action == "committee":
        if conversation.step != "committee" or len(parts) != 3:
            await send_message(conversation.telegram_id, "That button is no longer active.")
            return
        committee = db.get(Committee, int(parts[2]))
        if not committee:
            await send_message(conversation.telegram_id, "That CCA is no longer available.")
            return
        if prefix == "disp":
            user = db.query(User).filter(User.telegram_id == conversation.telegram_id).first()
            if not user or committee.id not in user.committee_ids:
                await send_message(conversation.telegram_id, "That CCA is not assigned to your account.")
                return
        data["committee_id"] = committee.id
        conversation.step = "confirm" if data.get("_editing") else ("confirm" if prefix == "reg" else "activity_name")
        data.pop("_editing", None)
        _save(conversation, data)
        db.commit()
        if conversation.step == "confirm":
            if prefix == "reg":
                await _send_registration_confirmation(db, conversation)
            else:
                await _send_disposable_confirmation(db, conversation)
        else:
            await _prompt_disposable_step(conversation.telegram_id, conversation.step)
        return
    if action == "skip-description" and conversation.step == "description":
        data.pop("_editing", None)
        data["description"] = None
        conversation.step = "confirm"
        _save(conversation, data)
        db.commit()
        await _send_disposable_confirmation(db, conversation)
        return
    if action == "edit-menu":
        if conversation.step != "confirm":
            await send_message(conversation.telegram_id, "That button is no longer active.")
            return
        await _send_edit_menu(conversation)
        return
    if action == "edit":
        if conversation.step != "confirm" or len(parts) != 3:
            await send_message(conversation.telegram_id, "That button is no longer active.")
            return
        field = parts[2]
        allowed = {"name", "email", "committee"} if prefix == "reg" else {"committee", "activity_name", *ITEMS, "collection_date", "collection_time", "description"}
        if field not in allowed:
            return
        data["_editing"] = True
        conversation.step = "name" if field == "name" else field
        _save(conversation, data)
        db.commit()
        if field == "committee":
            if prefix == "reg":
                committees = db.query(Committee).order_by(Committee.name).all()
            else:
                user = db.query(User).filter(User.telegram_id == conversation.telegram_id).first()
                committees = [m.committee for m in user.committee_memberships] if user else []
            await _send_committee_picker(conversation.telegram_id, committees, prefix, 0)
        elif prefix == "reg":
            await send_message(conversation.telegram_id, "Enter the new name:" if field == "name" else "Enter the new email:", _cancel_markup())
        else:
            await _prompt_disposable_step(conversation.telegram_id, field)
        return
    if action == "submit":
        if conversation.step != "confirm":
            await send_message(conversation.telegram_id, "Complete the current step before submitting.", _cancel_markup())
            return
        if prefix == "reg":
            await _submit_registration(db, conversation)
        else:
            await _submit_disposable(db, conversation)


async def _send_edit_menu(conversation: BotConversation) -> None:
    if conversation.flow == "register":
        fields = (("name", "Name"), ("email", "Email"), ("committee", "CCA"))
        prefix = "reg"
    else:
        fields = (("committee", "CCA"), ("activity_name", "Activity"), *((item, item.title()) for item in ITEMS),
                  ("collection_date", "Date"), ("collection_time", "Time"), ("description", "Description"))
        prefix = "disp"
    rows = [[{"text": label, "callback_data": f"{prefix}:edit:{field}"}] for field, label in fields]
    rows.append([{"text": "Cancel request", "callback_data": "conv:cancel"}])
    await send_message(conversation.telegram_id, "Which field would you like to edit?", {"inline_keyboard": rows})


async def _submit_registration(db: Session, conversation: BotConversation) -> None:
    data = _payload(conversation)
    committee = db.get(Committee, data.get("committee_id"))
    if not committee:
        await send_message(conversation.telegram_id, "Select a valid CCA before submitting.")
        return
    duplicate = db.query(User).filter(User.email == data.get("email"), User.telegram_id != conversation.telegram_id).first()
    if duplicate:
        conversation.step = "email"
        db.commit()
        await send_message(conversation.telegram_id, "That email was registered by someone else. Enter another email.", _cancel_markup())
        return
    user = db.query(User).filter(User.telegram_id == conversation.telegram_id).first()
    if not user:
        user = User(telegram_id=conversation.telegram_id, email=data["email"])
        db.add(user)
        db.flush()
    user.email = data["email"]
    user.display_name = data["display_name"]
    user.telegram_username = data.get("telegram_username")
    user.role = UserRole.user
    user.status = UserStatus.pending
    db.query(UserCommittee).filter(UserCommittee.user_id == user.id).delete()
    db.add(UserCommittee(user_id=user.id, committee_id=committee.id))
    db.delete(conversation)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        await send_message(conversation.telegram_id, "That email or Telegram username is already registered.")
        return
    await notify_admins_registration_request(user, committee, committee_portfolio(committee))
    await send_message(user.telegram_id, "Registration submitted. You will receive a message after an administrator reviews it.")


async def _submit_disposable(db: Session, conversation: BotConversation) -> None:
    data = _payload(conversation)
    user = db.query(User).filter(User.telegram_id == conversation.telegram_id).first()
    committee = db.get(Committee, data.get("committee_id"))
    if not user or user.status != UserStatus.approved or not committee or committee.id not in user.committee_ids:
        await send_message(conversation.telegram_id, "Your account or CCA assignment changed. Please cancel and start again.")
        return
    if not any(data.get(item, 0) > 0 for item in ITEMS):
        await send_message(conversation.telegram_id, "At least one quantity must be greater than zero.")
        return
    disposable = DisposableRequest(
        proposal_id=None,
        committee_id=committee.id,
        requested_by=user.id,
        activity_name=data["activity_name"],
        description=data.get("description"),
        collection_date=date.fromisoformat(data["collection_date"]),
        collection_time=time.fromisoformat(data["collection_time"]),
        status=DisposableStatus.pending,
        **{item: data.get(item, 0) for item in ITEMS},
    )
    db.add(disposable)
    db.delete(conversation)
    db.commit()
    db.refresh(disposable)
    await notify_admins_new_disposable_request(
        disposable.activity_name,
        user.display_name or user.email,
        committee_portfolio(committee),
        disposable.id,
    )
    await send_message(user.telegram_id, f"Disposable request #{disposable.id} submitted for approval.")


async def _admin_callback(db: Session, telegram_id: int, callback_data: str) -> None:
    parts = callback_data.split(":")
    if len(parts) != 4:
        return
    _, subject, action, raw_id = parts
    try:
        target_id = int(raw_id)
    except ValueError:
        return
    admin = db.query(User).filter(User.telegram_id == telegram_id).first()
    if not admin or admin.role != UserRole.admin or admin.status != UserStatus.approved:
        await send_message(telegram_id, "You are not authorized to perform this action.")
        return
    if action == "reject":
        if _conversation(db, telegram_id):
            await send_message(telegram_id, "Finish or cancel your current flow before rejecting another item.", _cancel_markup())
            return
        db.add(BotConversation(
            telegram_id=telegram_id,
            flow="admin_reject",
            step="reason",
            payload=json.dumps({"subject": subject, "target_id": target_id}),
        ))
        db.commit()
        markup = {"inline_keyboard": [
            [{"text": "Skip reason", "callback_data": "admin:reject:skip:0"}],
            [{"text": "Cancel", "callback_data": "conv:cancel"}],
        ]}
        await send_message(telegram_id, "Enter an optional rejection reason, or select Skip.", markup)
        return
    if action != "approve":
        return
    if subject == "disp":
        try:
            disposable, changed = decide_disposable(db, admin, target_id, DisposableStatus.approved)
        except Exception as exc:
            await send_message(telegram_id, getattr(exc, "detail", "Could not approve this request."))
            return
        if changed:
            await notify_user_disposable_approved(
                disposable.requester.telegram_id, disposable.activity_name, disposable.collection_date.isoformat()
            )
        await send_message(telegram_id, f"Disposable request #{target_id} is approved.")
    elif subject == "reg":
        user = db.query(User).filter(User.id == target_id).with_for_update().first()
        committee = user.committee_memberships[0].committee if user and user.committee_memberships else None
        if not user or not committee or not admin_can_access_committee(admin, committee):
            await send_message(telegram_id, "Registration not found.")
            return
        if user.status == UserStatus.rejected:
            await send_message(telegram_id, "Registration was already rejected.")
            return
        changed = user.status != UserStatus.approved
        user.status = UserStatus.approved
        db.commit()
        if changed:
            await notify_user_registration_approved(user.telegram_id)
        await send_message(telegram_id, f"Registration for {escape(user.display_name or user.email)} is approved.")


async def handle_admin_rejection_skip(db: Session, telegram_id: int) -> None:
    conversation = _conversation(db, telegram_id)
    if conversation and conversation.flow == "admin_reject":
        await _finish_admin_rejection(db, conversation, None)


async def _finish_admin_rejection(db: Session, conversation: BotConversation, reason: str | None) -> None:
    data = _payload(conversation)
    admin = db.query(User).filter(User.telegram_id == conversation.telegram_id).first()
    subject = data.get("subject")
    target_id = data.get("target_id")
    if not admin or admin.role != UserRole.admin or admin.status != UserStatus.approved:
        db.delete(conversation)
        db.commit()
        return
    if subject == "disp":
        try:
            disposable, changed = decide_disposable(db, admin, target_id, DisposableStatus.rejected, reason)
        except Exception as exc:
            db.delete(conversation)
            db.commit()
            await send_message(admin.telegram_id, getattr(exc, "detail", "Could not reject this request."))
            return
        db.delete(conversation)
        db.commit()
        if changed:
            await notify_user_disposable_rejected(
                disposable.requester.telegram_id, disposable.activity_name, disposable.rejection_reason
            )
        await send_message(admin.telegram_id, f"Disposable request #{target_id} is rejected.")
        return
    user = db.query(User).filter(User.id == target_id).with_for_update().first()
    committee = user.committee_memberships[0].committee if user and user.committee_memberships else None
    if not user or not committee or not admin_can_access_committee(admin, committee):
        db.delete(conversation)
        db.commit()
        await send_message(admin.telegram_id, "Registration not found.")
        return
    if user.status == UserStatus.approved:
        db.delete(conversation)
        db.commit()
        await send_message(admin.telegram_id, "Registration was already approved.")
        return
    changed = user.status != UserStatus.rejected
    user.status = UserStatus.rejected
    db.delete(conversation)
    db.commit()
    if changed:
        await notify_user_registration_rejected_with_reason(user.telegram_id, reason)
    await send_message(admin.telegram_id, f"Registration for {escape(user.display_name or user.email)} is rejected.")
