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
