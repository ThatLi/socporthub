"""Outbound Telegram notification templates, one function per trigger in the spec's
notification flow table. Routes call these instead of building message text inline."""

from html import escape

from api.config import get_settings
from api.services.telegram import send_message, send_photo
from api.models import Portfolio, User

# Telegram's sendPhoto caption limit; longer blast messages go as a follow-up
# text message instead of being silently truncated.
TELEGRAM_CAPTION_LIMIT = 1024


def _admin_ids(portfolio: Portfolio | None = None) -> set[int]:
    settings = get_settings()
    if portfolio == Portfolio.welfare:
        return settings.welfare_admin_telegram_id_set
    if portfolio == Portfolio.social:
        return settings.social_admin_telegram_id_set
    return settings.all_admin_telegram_id_set


async def notify_admins_new_registration(display_name: str | None, email: str) -> None:
    text = f"\U0001f4dd New registration pending approval: <b>{display_name or email}</b> ({email})"
    for admin_id in _admin_ids():
        await send_message(admin_id, text)


async def notify_admins_bug_report(user: User, message: str) -> None:
    """Bug reports intentionally go to the legacy ADMIN_TELEGRAM_IDS list."""
    text = (
        "🐛 <b>New bug report</b>\n\n"
        f"From: {escape(user.display_name or user.email)}\n"
        f"Email: {escape(user.email)}\n"
        f"Telegram ID: <code>{user.telegram_id}</code>\n\n"
        f"{escape(message)}"
    )
    settings = get_settings()
    for admin_id in settings.admin_telegram_id_set:
        await send_message(admin_id, text)


async def notify_user_registration_approved(telegram_id: int) -> None:
    await send_message(
        telegram_id,
        "✅ Your access to Social Port Hub has been approved. Open the app from the menu button to get started.",
    )


async def notify_user_registration_rejected(telegram_id: int) -> None:
    await send_message(
        telegram_id,
        "Your Social Port Hub registration was not approved. Contact the Social Director for details.",
    )


async def notify_admins_new_proposal(proposal_title: str, submitter_name: str, portfolio: Portfolio) -> None:
    text = f"\U0001f4cb New proposal <b>{proposal_title}</b> submitted by {submitter_name}"
    for admin_id in _admin_ids(portfolio):
        await send_message(admin_id, text)


async def notify_user_status_change(
    telegram_id: int, proposal_title: str, new_status: str, comment: str | None = None
) -> None:
    text = f"\U0001f4e2 Your proposal <b>{proposal_title}</b> status changed to <b>{new_status}</b>"
    if comment:
        text += f"\n\n\U0001f4ac {comment}"
    await send_message(telegram_id, text)


async def notify_admins_new_comment(
    proposal_title: str, author_name: str, comment: str, portfolio: Portfolio
) -> None:
    text = (
        f"💬 New comment on <b>{escape(proposal_title)}</b>\n"
        f"From: <b>{escape(author_name)}</b>\n\n{escape(comment)}"
    )
    for admin_id in _admin_ids(portfolio):
        await send_message(admin_id, text)


async def notify_user_new_comment(
    telegram_id: int, proposal_title: str, author_name: str, comment: str
) -> None:
    await send_message(
        telegram_id,
        f"💬 New comment on <b>{escape(proposal_title)}</b> from <b>{escape(author_name)}</b>\n\n{escape(comment)}",
    )


async def notify_admins_new_disposable_request(
    proposal_title: str, requester_name: str, portfolio: Portfolio,
    *, telegram_username: str | None = None, cca: str | None = None,
    quantities: dict[str, int] | None = None, collection_date: str | None = None,
    collection_time: str | None = None, description: str | None = None,
) -> None:
    if quantities is None:
        text = f"\U0001f37d New disposables request for <b>{escape(proposal_title)}</b> from {escape(requester_name)}"
    else:
        breakdown = ", ".join(f"{amount} {item}" for item, amount in quantities.items() if amount) or "none"
        text = (
            "🍽 <b>New disposable request</b>\n\n"
            f"Name: {escape(requester_name)}\n"
            f"Telegram: {escape(telegram_username or 'unknown')}\n"
            f"CCA: {escape(cca or 'unknown')}\n"
            f"Amount: {escape(breakdown)}\n"
            f"Collection: {escape(collection_date or '—')} {escape(collection_time or '')}\n"
            f"Description: {escape(description or '—')}"
        )
    for admin_id in _admin_ids(portfolio):
        await send_message(admin_id, text)


async def notify_user_disposable_approved(telegram_id: int, proposal_title: str, collection_date: str) -> None:
    await send_message(
        telegram_id,
        f"✅ Disposables approved for <b>{proposal_title}</b> — collect on {collection_date}.",
    )


async def notify_user_disposable_rejected(telegram_id: int, request_label: str) -> None:
    await send_message(telegram_id, f"❌ Your disposable request for <b>{escape(request_label)}</b> was rejected.")


async def notify_user_email_sent(telegram_id: int, proposal_title: str) -> None:
    await send_message(
        telegram_id,
        f"✅ Your proposal <b>{proposal_title}</b> has been submitted. The confirmation email is on its way.",
    )


async def notify_admins_reminder(sender_name: str, message: str, portfolio: Portfolio | None = None) -> None:
    text = f"🔔 Reminder from <b>{escape(sender_name)}</b>\n\n{escape(message)}"
    for admin_id in _admin_ids(portfolio):
        await send_message(admin_id, text)


async def notify_user_reminder_broadcast(
    telegram_id: int, sender_name: str, message: str, deadline_text: str | None = None
) -> None:
    text = f"🔔 Reminder from <b>{escape(sender_name)}</b>\n\n{escape(message)}"
    if deadline_text:
        text += f"\n\n⏰ Due: {escape(deadline_text)}"
    await send_message(telegram_id, text)


async def send_proposal_announcement(
    chat_id: int,
    poster_data: bytes | None,
    poster_filename: str | None,
    blast_message: str | None,
) -> None:
    """Sends a finished proposal's poster + blast message to the announcement
    relay. Raises on failure — unlike the notifications above, this send IS the
    action the caller asked for, so the caller needs to know if it didn't happen."""
    if not poster_data and not blast_message:
        raise ValueError("Nothing to announce: proposal has no poster or blast message")

    text = escape(blast_message) if blast_message else None
    if poster_data and text and len(text) <= TELEGRAM_CAPTION_LIMIT:
        await send_photo(chat_id, poster_data, poster_filename or "poster.jpg", text, raise_on_error=True)
    elif poster_data:
        await send_photo(chat_id, poster_data, poster_filename or "poster.jpg", raise_on_error=True)
        if text:
            await send_message(chat_id, text, raise_on_error=True)
    else:
        await send_message(chat_id, text, raise_on_error=True)


async def notify_admins_todays_collections(
    collections: list[dict], portfolio: Portfolio
) -> None:
    if not collections:
        return
    quantities = ("plates", "cups", "bowls", "forks", "spoons")
    grouped: dict[str, list[dict]] = {}
    for collection in collections:
        grouped.setdefault(collection["committee"], []).append(collection)

    lines = [f"🌅 <b>Today's disposable collections — {escape(portfolio.value.title())}</b>"]
    grand_total = {item: 0 for item in quantities}
    for committee, committee_collections in sorted(grouped.items()):
        lines.append(f"\n<b>{escape(committee)}</b>")
        committee_total = {item: 0 for item in quantities}
        for collection in sorted(committee_collections, key=lambda item: (item["time"], item["title"])):
            amounts = []
            for item in quantities:
                amount = collection[item]
                committee_total[item] += amount
                grand_total[item] += amount
                if amount:
                    amounts.append(f"{amount} {item}")
            detail = ", ".join(amounts) or "no items"
            lines.append(f"• {escape(collection['time'])} — <b>{escape(collection['title'])}</b>: {detail}")
        subtotal = ", ".join(f"{committee_total[item]} {item}" for item in quantities if committee_total[item]) or "no items"
        lines.append(f"<i>Committee total: {subtotal}</i>")

    total = ", ".join(f"{grand_total[item]} {item}" for item in quantities if grand_total[item]) or "no items"
    lines.append(f"\n<b>Total for {escape(portfolio.value.title())}: {total}</b>")
    for admin_id in _admin_ids(portfolio):
        await send_message(admin_id, "\n".join(lines))
