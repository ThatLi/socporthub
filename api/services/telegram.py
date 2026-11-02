"""Thin wrapper around the raw Telegram Bot API (send-message and send-photo)."""

import logging

import httpx

from api.config import get_settings

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"


def _api_url(method: str) -> str:
    token = get_settings().telegram_bot_token
    return f"{TELEGRAM_API_BASE.format(token=token)}/{method}"


async def send_message(
    chat_id: int, text: str, reply_markup: dict | None = None, *, raise_on_error: bool = False
) -> None:
    """By default a fire-and-forget notification send: logs and swallows errors so a
    Telegram outage never fails the DB operation that triggered the notification.
    Pass raise_on_error=True for a caller where the send itself is the point (e.g. a
    user-triggered announcement) and the caller needs to know it failed."""
    settings = get_settings()
    if not settings.telegram_bot_token:
        logger.info("No TELEGRAM_BOT_TOKEN configured; skipping message to %s: %s", chat_id, text)
        return

    payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(_api_url("sendMessage"), json=payload)
            resp.raise_for_status()
    except httpx.HTTPError:
        logger.exception("Failed to send Telegram message to %s", chat_id)
        if raise_on_error:
            raise


async def send_photo(
    chat_id: int,
    photo: bytes,
    filename: str,
    caption: str | None = None,
    *,
    raise_on_error: bool = False,
) -> None:
    """See send_message for the raise_on_error contract."""
    settings = get_settings()
    if not settings.telegram_bot_token:
        logger.info("No TELEGRAM_BOT_TOKEN configured; skipping photo to %s", chat_id)
        return

    data: dict = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
        data["parse_mode"] = "HTML"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                _api_url("sendPhoto"), data=data, files={"photo": (filename, photo)}
            )
            resp.raise_for_status()
    except httpx.HTTPError:
        logger.exception("Failed to send Telegram photo to %s", chat_id)
        if raise_on_error:
            raise


async def send_photo_by_file_id(
    chat_id: int, file_id: str, caption: str | None = None, reply_markup: dict | None = None
) -> None:
    """Forward a photo already on Telegram's servers (fire-and-forget, like send_message)."""
    settings = get_settings()
    if not settings.telegram_bot_token:
        logger.info("No TELEGRAM_BOT_TOKEN configured; skipping photo to %s", chat_id)
        return
    payload: dict = {"chat_id": chat_id, "photo": file_id}
    if caption:
        payload["caption"] = caption
        payload["parse_mode"] = "HTML"
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(_api_url("sendPhoto"), json=payload)
            resp.raise_for_status()
    except httpx.HTTPError:
        logger.exception("Failed to send Telegram photo to %s", chat_id)


async def download_file(file_id: str, max_bytes: int = 10 * 1024 * 1024) -> bytes | None:
    """Download a file a user sent to the bot. Returns None on any failure or when the
    file is too large; callers keep working from the file id alone. Errors are logged
    by type only because httpx's message includes the URL, which contains the bot token."""
    settings = get_settings()
    if not settings.telegram_bot_token:
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            info = await client.post(_api_url("getFile"), json={"file_id": file_id})
            info.raise_for_status()
            file = info.json().get("result") or {}
            path = file.get("file_path")
            if not path or int(file.get("file_size") or 0) > max_bytes:
                return None
            token = settings.telegram_bot_token
            resp = await client.get(f"https://api.telegram.org/file/bot{token}/{path}")
            resp.raise_for_status()
            return resp.content if len(resp.content) <= max_bytes else None
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Could not download Telegram file %s (%s)", file_id, type(exc).__name__)
        return None


async def answer_callback_query(callback_query_id: str, text: str | None = None) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return
    payload: dict = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(_api_url("answerCallbackQuery"), json=payload)
            response.raise_for_status()
    except httpx.HTTPError:
        logger.exception("Failed to answer Telegram callback query %s", callback_query_id)


def webapp_open_markup(url: str, label: str = "Open Social Port Hub") -> dict:
    return {"inline_keyboard": [[{"text": label, "web_app": {"url": url}}]]}
