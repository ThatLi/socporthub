import asyncio
from html import unescape
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from api.services import telegram
from bot import handlers


class HelpHTMLParser(HTMLParser):
    def handle_starttag(self, tag, attrs):
        assert tag == "b", f"Unsupported help tag: {tag}"


@pytest.mark.parametrize("text", ["/help", "  /help  ", "/help details"])
def test_help_sends_valid_html_without_username_lookup(monkeypatch, text):
    send = AsyncMock()
    lookup = AsyncMock()
    monkeypatch.setattr(handlers, "send_message", send)
    monkeypatch.setattr(handlers, "get_bot_username", lookup)
    asyncio.run(handlers.handle_update({"message": {"chat": {"id": 123}, "text": text}}))
    send.assert_awaited_once_with(123, handlers.HELP_TEXT)
    lookup.assert_not_awaited()
    HelpHTMLParser().feed(send.call_args.args[1])
    for usage in ("/approve <id>", "/reject <id>", "/remind <message>"):
        assert usage in unescape(send.call_args.args[1])


@pytest.mark.parametrize("recipient,username,replies", [
    ("SocialPortHubBot", "SocialPortHubBot", 1),
    ("socialporthubbot", "SocialPortHubBot", 1),
    ("OtherBot", "SocialPortHubBot", 0),
    ("SocialPortHubBot", None, 0),
])
def test_addressed_help_checks_recipient_and_preserves_conversation(monkeypatch, recipient, username, replies):
    send = AsyncMock()
    monkeypatch.setattr(handlers, "send_message", send)
    monkeypatch.setattr(handlers, "get_bot_username", AsyncMock(return_value=username))
    state = {"flow": "register", "step": "name"}
    monkeypatch.setattr(handlers, "_conversation", {123: state.copy()})
    asyncio.run(handlers.handle_update({"message": {
        "chat": {"id": 123, "type": "supergroup"}, "text": f"/help@{recipient}",
    }}))
    assert send.await_count == replies
    if replies:
        send.assert_awaited_once_with(123, handlers.HELP_TEXT)
    assert handlers._conversation[123] == state


def test_addressed_command_preserves_arguments(monkeypatch):
    remind = AsyncMock()
    monkeypatch.setattr(handlers, "_handle_remind", remind)
    monkeypatch.setattr(handlers, "get_bot_username", AsyncMock(return_value="SocialPortHubBot"))
    asyncio.run(handlers.handle_update({"message": {
        "chat": {"id": 123}, "text": "/remind@SocialPortHubBot Please review my proposal",
    }}))
    remind.assert_awaited_once_with(123, "Please review my proposal")


@pytest.mark.parametrize("status,payload,expected", [
    (200, {"ok": True, "result": {"username": "SocialPortHubBot"}}, "SocialPortHubBot"),
    (401, {"ok": False}, None),
    (200, {"ok": True, "result": {}}, None),
])
def test_get_bot_username(monkeypatch, caplog, status, payload, expected):
    caplog.set_level("WARNING")
    monkeypatch.setattr(telegram, "get_settings", lambda: SimpleNamespace(telegram_bot_token="test-token"))
    real_client = httpx.AsyncClient

    def respond(request):
        assert request.url.path == "/bottest-token/getMe"
        return httpx.Response(status, json=payload)

    monkeypatch.setattr(telegram.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(respond), **kwargs,
    ))
    assert asyncio.run(telegram.get_bot_username()) == expected
    assert "test-token" not in caplog.text


def test_get_bot_username_without_token(monkeypatch):
    monkeypatch.setattr(telegram, "get_settings", lambda: SimpleNamespace(telegram_bot_token=""))
    assert asyncio.run(telegram.get_bot_username()) is None


def test_bot_username_network_failure_is_ignored(monkeypatch):
    monkeypatch.setattr(telegram, "get_settings", lambda: SimpleNamespace(telegram_bot_token="test-token"))
    real_client = httpx.AsyncClient

    def fail(request):
        raise httpx.ConnectError("offline", request=request)

    monkeypatch.setattr(telegram.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(fail), **kwargs,
    ))
    assert asyncio.run(telegram.get_bot_username()) is None


@pytest.mark.parametrize("text", ["/help", "/help@SocialPortHubBot"])
def test_help_telegram_payload(monkeypatch, text):
    import json

    monkeypatch.setattr(telegram, "get_settings", lambda: SimpleNamespace(telegram_bot_token="test-token"))
    real_client = httpx.AsyncClient
    messages = []

    def respond(request):
        if request.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"username": "SocialPortHubBot"}})
        assert request.url.path.endswith("/sendMessage")
        payload = json.loads(request.content)
        assert payload["parse_mode"] == "HTML"
        HelpHTMLParser().feed(payload["text"])
        messages.append(payload)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(telegram.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(respond), **kwargs,
    ))
    asyncio.run(handlers.handle_update({"message": {"chat": {"id": 123}, "text": text}}))
    assert messages == [{"chat_id": 123, "text": handlers.HELP_TEXT, "parse_mode": "HTML"}]
