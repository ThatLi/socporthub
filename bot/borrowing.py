"""Telegram flows for borrowing the shared foldable tables and cambro.

/borrow   - borrower requests items (committee, activity, dates, items, description)
/return   - borrower sends a photo as proof the items are back
/borrowed - admins see where every item is right now
Admins approve / reject requests and confirm returns with inline buttons.
"""

import json
from datetime import date, datetime
from html import escape

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api.models import BorrowRequest, BorrowStatus, BotConversation, Committee, User, UserRole, UserStatus
from api.services.borrowing import (
    ITEM_TOTALS,
    app_today,
    availability,
    decide_borrow,
    decide_return,
    describe_items,
    is_overdue,
    out_now,
    shortfall,
    submit_return_proof,
)
from api.services.telegram import answer_callback_query, download_file, send_message
from bot.conversations import _cancel_markup, _conversation, _payload, _save, _send_committee_picker
from bot.notifications import (
    notify_admins_new_borrow_request,
    notify_admins_return_proof,
    notify_user_borrow_approved,
    notify_user_borrow_rejected,
    notify_user_return_confirmed,
    notify_user_return_not_confirmed,
)

EDITABLE = ("committee", "activity_name", "borrow_date", "return_date", "tables", "cambro", "description")
EDIT_LABELS = {
    "committee": "CCA",
    "activity_name": "Activity",
    "borrow_date": "Borrow date",
    "return_date": "Return date",
    "tables": "Tables",
    "cambro": "Cambro",
    "description": "Description",
}
ADMIN_ONLY = "You are not authorized to perform this action."


def _parse_date(value: str) -> date | None:
    for pattern in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            pass
    return None


def _user(db: Session, telegram_id: int) -> User | None:
    return db.query(User).filter(User.telegram_id == telegram_id).first()


def _is_admin(user: User | None) -> bool:
    return bool(user and user.role == UserRole.admin and user.status == UserStatus.approved)


def _confirm_markup() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "Edit", "callback_data": "borrow:edit-menu"},
                {"text": "Submit", "callback_data": "borrow:submit"},
            ],
            [{"text": "Cancel", "callback_data": "conv:cancel"}],
        ]
    }


# --- Borrower: /borrow -------------------------------------------------------


async def start_borrow(db: Session, telegram_id: int) -> None:
    if _conversation(db, telegram_id):
        await send_message(telegram_id, "You already have an active flow. Continue it or use /cancel first.", _cancel_markup())
        return
    user = _user(db, telegram_id)
    if not user or user.status != UserStatus.approved:
        await send_message(telegram_id, "You need an approved account before borrowing. Use /register first.")
        return
    committees = [m.committee for m in user.committee_memberships]
    if not committees:
        await send_message(telegram_id, "Your account has no CCA assigned. Ask an administrator to update it.")
        return
    data: dict = {}
    step = "committee"
    if len(committees) == 1:
        data["committee_id"] = committees[0].id
        step = "activity_name"
    conversation = BotConversation(telegram_id=telegram_id, flow="borrow", step=step, payload=json.dumps(data))
    db.add(conversation)
    db.commit()
    await send_message(
        telegram_id,
        "📦 You can borrow <b>foldable tables</b> (4 available) and the <b>cambro</b> drink dispenser.",
    )
    await _prompt(db, conversation)


async def _prompt(db: Session, conversation: BotConversation) -> None:
    """Send the prompt for conversation.step. May advance past a step that needs no input."""
    tid = conversation.telegram_id
    data = _payload(conversation)
    step = conversation.step
    if step == "committee":
        user = _user(db, tid)
        await _send_committee_picker(tid, [m.committee for m in user.committee_memberships] if user else [], "borrow", 0)
    elif step == "activity_name":
        await send_message(tid, "What is the activity or purpose of this request?", _cancel_markup())
    elif step == "borrow_date":
        await send_message(tid, "When will you collect the items? Use DD/MM/YYYY or YYYY-MM-DD.", _cancel_markup())
    elif step == "return_date":
        await send_message(tid, "When will you return them? Use DD/MM/YYYY or YYYY-MM-DD.", _cancel_markup())
    elif step == "tables":
        free = availability(db, date.fromisoformat(data["borrow_date"]), date.fromisoformat(data["return_date"]))
        limit = free["tables"]
        buttons = [{"text": str(n), "callback_data": f"borrow:tables:{n}"} for n in range(0, limit + 1)]
        rows = [buttons[i : i + 5] for i in range(0, len(buttons), 5)]
        rows.append([{"text": "Cancel", "callback_data": "conv:cancel"}])
        await send_message(
            tid,
            f"How many foldable tables do you need? ({limit} of {ITEM_TOTALS['tables']} free for those dates)",
            {"inline_keyboard": rows},
        )
    elif step == "cambro":
        free = availability(db, date.fromisoformat(data["borrow_date"]), date.fromisoformat(data["return_date"]))
        if not free["cambro"]:
            data["cambro"] = 0
            await send_message(tid, "The cambro is not free for those dates, so it has been left out.")
            await _advance(db, conversation, data, "description")
            return
        await send_message(
            tid,
            "Do you need the cambro (drink dispenser)?",
            {"inline_keyboard": [
                [{"text": "Yes", "callback_data": "borrow:cambro:1"}, {"text": "No", "callback_data": "borrow:cambro:0"}],
                [{"text": "Cancel", "callback_data": "conv:cancel"}],
            ]},
        )
    elif step == "description":
        await send_message(
            tid,
            "Add a description, or select Skip.",
            {"inline_keyboard": [
                [{"text": "Skip", "callback_data": "borrow:skip-description"}],
                [{"text": "Cancel", "callback_data": "conv:cancel"}],
            ]},
        )
    elif step == "confirm":
        await _send_confirmation(db, conversation)


async def _advance(db: Session, conversation: BotConversation, data: dict, next_step: str) -> None:
    """Persist data and move on; an edit returns straight to the confirmation screen."""
    if data.pop("_editing", False):
        next_step = "confirm"
    conversation.step = next_step
    _save(conversation, data)
    db.commit()
    await _prompt(db, conversation)


async def _send_confirmation(db: Session, conversation: BotConversation) -> None:
    data = _payload(conversation)
    committee = db.get(Committee, data.get("committee_id"))
    items = describe_items({"tables": data.get("tables", 0), "cambro": data.get("cambro", 0)})
    text = (
        "Please confirm your borrow request:\n\n"
        f"CCA: {escape(committee.name if committee else '')}\n"
        f"Activity: <b>{escape(data.get('activity_name', ''))}</b>\n"
        f"Items: <b>{escape(items)}</b>\n"
        f"Collect: {data.get('borrow_date', '')}\n"
        f"Return: {data.get('return_date', '')}\n"
        f"Description: {escape(data.get('description') or 'None')}"
    )
    await send_message(conversation.telegram_id, text, _confirm_markup())


async def _borrow_message(db: Session, conversation: BotConversation, data: dict, value: str) -> None:
    tid = conversation.telegram_id
    step = conversation.step
    today = app_today()
    if step == "activity_name":
        if not value or len(value) > 255:
            await send_message(tid, "Enter an activity name between 1 and 255 characters.", _cancel_markup())
            return
        data[step] = value
        await _advance(db, conversation, data, "borrow_date")
    elif step == "borrow_date":
        parsed = _parse_date(value)
        if not parsed or parsed < today:
            await send_message(tid, "Enter today or a future date as DD/MM/YYYY or YYYY-MM-DD.", _cancel_markup())
            return
        data[step] = parsed.isoformat()
        if data.get("return_date") and date.fromisoformat(data["return_date"]) < parsed:
            data.pop("return_date")
            data.pop("_editing", None)
            conversation.step = "return_date"
            _save(conversation, data)
            db.commit()
            await send_message(tid, "Your return date is now before the borrow date. Enter a new return date.", _cancel_markup())
            return
        await _advance(db, conversation, data, "return_date")
    elif step == "return_date":
        parsed = _parse_date(value)
        start = date.fromisoformat(data["borrow_date"])
        if not parsed or parsed < start:
            await send_message(tid, "Enter a return date on or after the borrow date (DD/MM/YYYY or YYYY-MM-DD).", _cancel_markup())
            return
        data[step] = parsed.isoformat()
        await _advance(db, conversation, data, "tables")
    elif step == "tables":
        try:
            quantity = int(value)
        except ValueError:
            await send_message(tid, "Use the buttons, or enter a whole number.", _cancel_markup())
            return
        await _set_tables(db, conversation, data, quantity)
    elif step == "description":
        if len(value) > 2000:
            await send_message(tid, "Description must be 2,000 characters or fewer.", _cancel_markup())
            return
        data[step] = value or None
        await _advance(db, conversation, data, "confirm")
    else:
        await send_message(tid, "Use the buttons shown for this step, or /cancel.", _cancel_markup())


async def _set_tables(db: Session, conversation: BotConversation, data: dict, quantity: int) -> None:
    free = availability(db, date.fromisoformat(data["borrow_date"]), date.fromisoformat(data["return_date"]))["tables"]
    if quantity < 0 or quantity > free:
        await send_message(
            conversation.telegram_id,
            f"Enter a number from 0 to {free} (that is how many tables are free for those dates).",
            _cancel_markup(),
        )
        return
    data["tables"] = quantity
    await _advance(db, conversation, data, "cambro")


async def handle_message(db: Session, telegram_id: int, text: str) -> bool:
    """Handle plain text for borrow-related flows. Returns False if this isn't one."""
    conversation = _conversation(db, telegram_id)
    if not conversation:
        return False
    value = text.strip()
    if conversation.flow == "borrow":
        await _borrow_message(db, conversation, _payload(conversation), value)
        return True
    if conversation.flow == "borrow_reject":
        if len(value) > 1000:
            await send_message(telegram_id, "The reason must be 1,000 characters or fewer.", _cancel_markup())
            return True
        await _finish_rejection(db, conversation, value or None)
        return True
    if conversation.flow == "return":
        await send_message(telegram_id, "Please send a <b>photo</b> as proof of the return, or /cancel.", _cancel_markup())
        return True
    return False


async def handle_callback(db: Session, callback: dict) -> None:
    """Callbacks with the `borrow:` prefix (borrower side)."""
    callback_id = callback.get("id")
    actor_id = callback.get("from", {}).get("id")
    data = callback.get("data") or ""
    if callback_id:
        await answer_callback_query(callback_id)
    if actor_id is None:
        return
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    if action == "return":
        await start_return(db, actor_id, int(parts[2]) if len(parts) == 3 and parts[2].isdigit() else None)
        return
    conversation = _conversation(db, actor_id)
    if not conversation or conversation.flow != "borrow":
        await send_message(actor_id, "This action is no longer active. Start again with /borrow.")
        return
    payload = _payload(conversation)
    step = conversation.step
    if action == "committee-page" and step == "committee" and len(parts) == 3 and parts[2].isdigit():
        user = _user(db, actor_id)
        await _send_committee_picker(actor_id, [m.committee for m in user.committee_memberships] if user else [], "borrow", int(parts[2]))
    elif action == "committee" and step == "committee" and len(parts) == 3 and parts[2].isdigit():
        user = _user(db, actor_id)
        committee = db.get(Committee, int(parts[2]))
        if not user or not committee or committee.id not in user.committee_ids:
            await send_message(actor_id, "That CCA is not assigned to your account.")
            return
        payload["committee_id"] = committee.id
        await _advance(db, conversation, payload, "activity_name")
    elif action == "tables" and step == "tables" and len(parts) == 3 and parts[2].isdigit():
        await _set_tables(db, conversation, payload, int(parts[2]))
    elif action == "cambro" and step == "cambro" and len(parts) == 3 and parts[2] in ("0", "1"):
        payload["cambro"] = int(parts[2])
        await _advance(db, conversation, payload, "description")
    elif action == "skip-description" and step == "description":
        payload["description"] = None
        await _advance(db, conversation, payload, "confirm")
    elif action == "edit-menu" and step == "confirm":
        rows = [[{"text": EDIT_LABELS[f], "callback_data": f"borrow:edit:{f}"}] for f in EDITABLE]
        rows.append([{"text": "Cancel request", "callback_data": "conv:cancel"}])
        await send_message(actor_id, "Which field would you like to edit?", {"inline_keyboard": rows})
    elif action == "edit" and step == "confirm" and len(parts) == 3 and parts[2] in EDITABLE:
        field = parts[2]
        payload["_editing"] = True
        conversation.step = field
        _save(conversation, payload)
        db.commit()
        await _prompt(db, conversation)
    elif action == "submit" and step == "confirm":
        await _submit(db, conversation)
    else:
        await send_message(actor_id, "That button is no longer active.")


async def _submit(db: Session, conversation: BotConversation) -> None:
    tid = conversation.telegram_id
    data = _payload(conversation)
    user = _user(db, tid)
    committee = db.get(Committee, data.get("committee_id"))
    if not user or user.status != UserStatus.approved or not committee or committee.id not in user.committee_ids:
        await send_message(tid, "Your account or CCA assignment changed. Please cancel and start again.")
        return
    tables, cambro = int(data.get("tables", 0)), int(data.get("cambro", 0))
    if tables + cambro == 0:
        await send_message(tid, "Choose at least one item. Edit the tables or cambro.", _confirm_markup())
        return
    borrow_date, return_date = date.fromisoformat(data["borrow_date"]), date.fromisoformat(data["return_date"])
    if borrow_date < app_today():
        await send_message(tid, "The borrow date has passed. Edit the dates.", _confirm_markup())
        return
    problem = shortfall(db, {"tables": tables, "cambro": cambro, "borrow_date": borrow_date, "return_date": return_date})
    if problem:
        await send_message(tid, f"{problem} Edit the items or dates.", _confirm_markup())
        return
    request = BorrowRequest(
        committee_id=committee.id,
        requested_by=user.id,
        activity_name=data["activity_name"],
        description=data.get("description"),
        tables=tables,
        cambro=cambro,
        borrow_date=borrow_date,
        return_date=return_date,
        status=BorrowStatus.pending,
    )
    db.add(request)
    db.delete(conversation)
    db.commit()
    db.refresh(request)
    await notify_admins_new_borrow_request(
        request.id, user.display_name or user.email, user.telegram_username, committee.name, request.activity_name,
        describe_items(request), borrow_date.isoformat(), return_date.isoformat(), request.description,
    )
    await send_message(tid, f"Borrow request #{request.id} submitted for approval.")


# --- Borrower: /return -------------------------------------------------------


async def start_return(db: Session, telegram_id: int, request_id: int | None = None) -> None:
    if _conversation(db, telegram_id):
        await send_message(telegram_id, "You already have an active flow. Continue it or use /cancel first.", _cancel_markup())
        return
    user = _user(db, telegram_id)
    if not user or user.status != UserStatus.approved:
        await send_message(telegram_id, "You need an approved account first. Use /register.")
        return
    mine = (
        db.query(BorrowRequest)
        .filter(BorrowRequest.requested_by == user.id, BorrowRequest.status == BorrowStatus.approved)
        .order_by(BorrowRequest.return_date)
        .all()
    )
    if request_id is not None:
        mine = [r for r in mine if r.id == request_id]
    if not mine:
        waiting = db.query(BorrowRequest).filter(
            BorrowRequest.requested_by == user.id, BorrowRequest.status == BorrowStatus.return_submitted
        ).count()
        await send_message(
            telegram_id,
            "Your return photo is waiting for the owner to confirm." if waiting else "You have no borrowed items to return.",
        )
        return
    if len(mine) > 1:
        rows = [[{"text": f"#{r.id} {describe_items(r)} — {r.activity_name}"[:60], "callback_data": f"borrow:return:{r.id}"}] for r in mine]
        await send_message(telegram_id, "Which borrow are you returning?", {"inline_keyboard": rows})
        return
    request = mine[0]
    db.add(BotConversation(telegram_id=telegram_id, flow="return", step="photo", payload=json.dumps({"request_id": request.id})))
    db.commit()
    await send_message(
        telegram_id,
        f"Send one <b>photo</b> showing {escape(describe_items(request))} back where it belongs "
        f"(request #{request.id}, {escape(request.activity_name)}).",
        _cancel_markup(),
    )


def _extract_image(message: dict) -> tuple[str, str, int] | None:
    """(file_id, content_type, size) of the image in a Telegram message, if any."""
    photos = message.get("photo")
    if photos:
        best = photos[-1]  # Telegram lists sizes smallest to largest
        return best["file_id"], "image/jpeg", int(best.get("file_size") or 0)
    document = message.get("document") or {}
    mime = document.get("mime_type") or ""
    if mime.startswith("image/"):
        return document["file_id"], mime, int(document.get("file_size") or 0)
    return None


async def handle_photo(db: Session, telegram_id: int, message: dict) -> None:
    conversation = _conversation(db, telegram_id)
    if not conversation:
        await send_message(telegram_id, "To send return proof, start with /return.")
        return
    if conversation.flow != "return" or conversation.step != "photo":
        await send_message(telegram_id, "Finish or cancel your current flow first.", _cancel_markup())
        return
    image = _extract_image(message)
    if not image:
        await send_message(telegram_id, "That isn't a photo. Please send an image.", _cancel_markup())
        return
    file_id, content_type, size = image
    if size > 10 * 1024 * 1024:
        await send_message(telegram_id, "That photo is too large (10 MB max). Send a smaller one.", _cancel_markup())
        return
    user = _user(db, telegram_id)
    if not user:
        await send_message(telegram_id, "You need an approved account first. Use /register.")
        return
    request_id = _payload(conversation).get("request_id")
    data = await download_file(file_id)
    try:
        request = submit_return_proof(db, user, request_id, file_id, content_type, data)
    except HTTPException as exc:
        db.delete(conversation)
        db.commit()
        await send_message(telegram_id, str(exc.detail))
        return
    db.delete(conversation)
    db.commit()
    await notify_admins_return_proof(
        request.id, user.display_name or user.email, request.activity_name, describe_items(request), file_id
    )
    await send_message(telegram_id, "Thanks! The owner has been sent your photo and will confirm the return.")


# --- Admin ---------------------------------------------------------------


async def handle_admin_callback(db: Session, telegram_id: int, callback_data: str) -> None:
    """admin:borrow:<approve|reject|confirm|notyet|skip>:<request id>"""
    parts = callback_data.split(":")
    if len(parts) != 4 or not parts[3].isdigit():
        return
    _, _, action, raw_id = parts
    request_id = int(raw_id)
    admin = _user(db, telegram_id)
    if not _is_admin(admin):
        await send_message(telegram_id, ADMIN_ONLY)
        return
    if action == "skip":
        conversation = _conversation(db, telegram_id)
        if conversation and conversation.flow == "borrow_reject":
            await _finish_rejection(db, conversation, None)
        return
    if action == "reject":
        if _conversation(db, telegram_id):
            await send_message(telegram_id, "Finish or cancel your current flow before rejecting another item.", _cancel_markup())
            return
        db.add(BotConversation(telegram_id=telegram_id, flow="borrow_reject", step="reason", payload=json.dumps({"target_id": request_id})))
        db.commit()
        await send_message(
            telegram_id,
            "Enter an optional rejection reason, or select Skip.",
            {"inline_keyboard": [
                [{"text": "Skip reason", "callback_data": "admin:borrow:skip:0"}],
                [{"text": "Cancel", "callback_data": "conv:cancel"}],
            ]},
        )
        return
    try:
        if action == "approve":
            request, changed = decide_borrow(db, admin, request_id, BorrowStatus.approved)
            if changed:
                await notify_user_borrow_approved(
                    request.requester.telegram_id, request.activity_name, describe_items(request), request.return_date.isoformat()
                )
            await send_message(telegram_id, f"Borrow request #{request_id} is approved.")
        elif action == "confirm":
            request, changed = decide_return(db, admin, request_id, True)
            if changed:
                await notify_user_return_confirmed(request.requester.telegram_id, request.activity_name)
            await send_message(telegram_id, f"Return for request #{request_id} confirmed. The items are back.")
        elif action == "notyet":
            request, changed = decide_return(db, admin, request_id, False)
            if changed:
                await notify_user_return_not_confirmed(request.requester.telegram_id, request.id, request.activity_name)
            await send_message(telegram_id, f"Request #{request_id} is marked as still out; the borrower was asked to resend.")
    except HTTPException as exc:
        await send_message(telegram_id, str(exc.detail))


async def _finish_rejection(db: Session, conversation: BotConversation, reason: str | None) -> None:
    admin = _user(db, conversation.telegram_id)
    request_id = _payload(conversation).get("target_id")
    db.delete(conversation)
    db.commit()
    if not _is_admin(admin):
        return
    try:
        request, changed = decide_borrow(db, admin, request_id, BorrowStatus.rejected, reason)
    except HTTPException as exc:
        await send_message(admin.telegram_id, str(exc.detail))
        return
    if changed:
        await notify_user_borrow_rejected(request.requester.telegram_id, request.activity_name, request.rejection_reason)
    await send_message(admin.telegram_id, f"Borrow request #{request_id} is rejected.")


async def handle_borrowed(db: Session, telegram_id: int) -> None:
    """/borrowed - where every item is right now (admins only)."""
    if not _is_admin(_user(db, telegram_id)):
        await send_message(telegram_id, ADMIN_ONLY)
        return
    today = app_today()
    out = out_now(db, today)
    lines = ["📦 <b>Where the items are</b>"]
    for key, label in (("tables", "Foldable tables"), ("cambro", "Cambro")):
        taken = min(ITEM_TOTALS[key], sum(getattr(r, key) for r in out))
        lines.append(f"{label}: {taken} out, {ITEM_TOTALS[key] - taken} in store")
    if out:
        lines.append("\n<b>Currently out</b>")
    for r in out:
        flag = ""
        if r.status == BorrowStatus.return_submitted:
            flag = " 📸 proof sent, awaiting your confirmation"
        elif is_overdue(r, today):
            flag = f" ⚠️ overdue {(today - r.return_date).days}d"
        who = escape(r.requester.display_name or r.requester.email)
        if r.requester.telegram_username:
            who += f" ({escape(r.requester.telegram_username)})"
        lines.append(
            f"#{r.id} {escape(describe_items(r))} — {escape(r.activity_name)}\n"
            f"   {who}, {escape(r.committee.name)}; back by {r.return_date.isoformat()}{flag}"
        )
    pending = db.query(BorrowRequest).filter(BorrowRequest.status == BorrowStatus.pending).count()
    upcoming = (
        db.query(BorrowRequest)
        .filter(BorrowRequest.status == BorrowStatus.approved, BorrowRequest.borrow_date > today)
        .count()
    )
    if upcoming:
        lines.append(f"\nApproved and upcoming: {upcoming}")
    if pending:
        lines.append(f"Waiting for your decision: {pending}")
    if not out and not pending and not upcoming:
        lines.append("\nEverything is in store.")
    await send_message(telegram_id, "\n".join(lines))
