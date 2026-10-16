"""QQ authority/route regression contracts through the real HTTP boundary."""
import time
from unittest.mock import AsyncMock

import httpx
import pytest

from gateway.config import PlatformConfig
from hermes_qqbot import QQAdapter
from hermes_qqbot.message_rules import QQMessageRules


def make_adapter(kind="c2c"):
    adapter = QQAdapter(PlatformConfig(enabled=True, extra={
        "app_id": "test", "client_secret": "test", "markdown_support": False,
        "dm_policy": "allowlist", "allow_from": ["user"],
    }))
    adapter._chat_type_map["target"] = kind
    adapter._ensure_connected = AsyncMock(return_value=True)
    adapter._auth_headers = AsyncMock(return_value={"Authorization": "QQBot fake"})
    client = AsyncMock()
    client.request.return_value = httpx.Response(200, json={"id": "sent"})
    adapter._http_client = client
    return adapter, client


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,cap", [("c2c", 4), ("group", 5)])
async def test_quota_shared_by_separate_text_keyboard_and_media_calls(kind, cap):
    adapter, client = make_adapter(kind)
    for _ in range(cap):
        assert (await adapter.send("target", "reply", reply_to="msg")).success
    assert not (await adapter.send("target", "one too many", reply_to="msg")).success
    from hermes_qqbot.keyboards import build_update_prompt_keyboard
    assert not (await adapter.send_with_keyboard("target", "confirm", build_update_prompt_keyboard(), "msg")).success
    adapter._upload_media = AsyncMock(return_value={"file_info": "uploaded"})
    assert not (await adapter._send_media("target", "https://example.com/a.png", 1, "image", reply_to="msg")).success
    assert client.request.call_count == cap
    assert all(c.args[1].startswith("https://api.bot.qq.com/") for c in client.request.call_args_list)
    assert all(c.kwargs["json"]["msg_id"] == "msg" for c in client.request.call_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,ttl", [("c2c", 3600), ("group", 300), ("guild", 300), ("dm", 300)])
async def test_expired_passive_authority_never_becomes_active(monkeypatch, kind, ttl):
    adapter, client = make_adapter(kind)
    monkeypatch.setattr(time, "time", lambda: 10000)
    adapter._message_rules.remember(kind, "target", "expired", 10000 - ttl)
    result = await adapter.send("target", "late answer", reply_to="expired")
    assert not result.success
    client.request.assert_not_awaited()
    # A deliberate active send is distinct, not globally disabled.
    assert (await adapter.send("target", "notification")).success
    assert "msg_id" not in client.request.call_args.kwargs["json"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,cap", [("c2c", 4), ("group", 5), ("dm", 7)])
async def test_split_budget_uses_scene_and_keeps_undelivered_tail(monkeypatch, kind, cap):
    adapter, client = make_adapter(kind)
    monkeypatch.setattr(adapter, "truncate_message", lambda *_: [str(i) for i in range(7)])
    result = await adapter.send("target", "long answer", reply_to="msg")
    assert client.request.call_count == cap
    assert result.success is (kind == "dm")
    if kind != "dm":
        assert result.raw_response["undelivered_chunks"] == [str(i) for i in range(cap - 1, 7)]
        assert not result.retryable


@pytest.mark.asyncio
async def test_prior_reply_reduces_next_long_answer_budget(monkeypatch):
    adapter, client = make_adapter()
    assert (await adapter.send("target", "ack", reply_to="msg")).success
    monkeypatch.setattr(adapter, "truncate_message", lambda *_: [str(i) for i in range(5)])
    result = await adapter.send("target", "long", reply_to="msg")
    assert not result.success
    assert client.request.call_count == 4
    assert result.raw_response["undelivered_chunks"] == ["2", "3", "4"]


@pytest.mark.asyncio
async def test_known_rejection_refunds_but_unknown_timeout_consumes():
    adapter, client = make_adapter()
    client.request.side_effect = [httpx.Response(429, json={"message": "slow"}), httpx.ReadTimeout("lost")]
    with pytest.raises(RuntimeError):
        await adapter._api_request("POST", "/v2/users/target/messages", {"msg_id": "m"})
    assert adapter._message_rules.remaining("c2c", "target", "m") == 4
    with pytest.raises(RuntimeError):
        await adapter._api_request("POST", "/v2/users/target/messages", {"msg_id": "m"})
    assert adapter._message_rules.remaining("c2c", "target", "m") == 3


@pytest.mark.asyncio
async def test_real_channel_dm_ingress_routes_response_not_group(monkeypatch):
    adapter, client = make_adapter()
    async def respond(event):
        assert event.source.chat_id == "dm-guild"
        assert event.source.chat_type == "dm"
        result = await adapter.send(event.source.chat_id, "answer", reply_to=event.message_id)
        assert result.success
    monkeypatch.setattr(adapter, "handle_message", respond)
    await adapter._on_message("DIRECT_MESSAGE_CREATE", {
        "id": "msg", "guild_id": "dm-guild", "content": "hello", "author": {"id": "user"},
    })
    assert client.request.call_args.args[:2] == ("POST", "https://api.bot.qq.com/dms/dm-guild/messages")
    assert client.request.call_args.kwargs["json"] == {"content": "answer", "msg_id": "msg"}
    result = await adapter.send_document("dm-guild", "ignored")
    assert not result.success
    assert client.request.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata,reply,success", [
    ({"event_id": "event"}, None, True), ({"is_wakeup": True}, None, True),
    ({"is_wakeup": True}, "msg", False), ({"event_id": "event"}, "msg", False),
    ({"event_id": "event", "is_wakeup": True}, None, False),
])
async def test_explicit_authorities_are_mutually_exclusive(metadata, reply, success):
    adapter, client = make_adapter()
    result = await adapter.send("target", "hello", reply_to=reply, metadata=metadata)
    assert result.success is success
    if success:
        body = client.request.call_args.kwargs["json"]
        assert "msg_id" not in body
        assert all(body[k] == v for k, v in metadata.items())
    else:
        client.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_business_error_http_200_is_not_success():
    adapter, client = make_adapter()
    client.request.return_value = httpx.Response(200, json={"code": "40034105", "message": "permission denied"})
    result = await adapter.send("target", "active notification")
    assert not result.success
    assert not result.retryable
    assert client.request.call_count == 1


@pytest.mark.asyncio
async def test_typing_indicator_does_not_spend_visible_reply_budget():
    adapter, client = make_adapter()
    for _ in range(6):
        await adapter._api_request("POST", "/v2/users/target/messages", {
            "msg_type": 6, "msg_id": "m", "input_notify": {"input_type": 1, "input_second": 60}})
    assert adapter._message_rules.remaining("c2c", "target", "m") == 4
    assert (await adapter.send("target", "answer", reply_to="m")).success
    assert client.request.call_count == 7


@pytest.mark.asyncio
async def test_business_refusals_refund_local_passive_budget():
    adapter, client = make_adapter()
    client.request.return_value = httpx.Response(200, json={"code": 40034105, "message": "no permission"})
    for _ in range(5):
        assert not (await adapter.send("target", "refused", reply_to="m")).success
    assert adapter._message_rules.remaining("c2c", "target", "m") == 4
    client.request.return_value = httpx.Response(200, json={"id": "accepted"})
    assert (await adapter.send("target", "allowed", reply_to="m")).success


@pytest.mark.asyncio
async def test_native_receipt_does_not_bypass_authority_validation():
    adapter, _ = make_adapter()
    adapter._reconcile_native_send = AsyncMock(return_value=None)
    result = await adapter.send("target", "final", reply_to="m", metadata={"event_id": "e"})
    assert not result.success
    adapter._reconcile_native_send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx.ReadTimeout("lost"), RuntimeError("QQ Bot API error [502] unknown")])
async def test_image_unknown_delivery_never_falls_back_to_second_message(failure):
    adapter, client = make_adapter()
    adapter._upload_media = AsyncMock(return_value={"file_info": "media"})
    client.request.side_effect = failure
    result = await adapter.send_image("target", "https://example.com/image.png", reply_to="m")
    assert not result.success
    assert result.raw_response["delivery_unknown"]
    assert client.request.call_count == 1


def test_rule_keys_isolate_destination_and_scene():
    rules = QQMessageRules()
    rules.reserve("c2c", "a", "same")
    assert rules.remaining("c2c", "a", "same") == 3
    assert rules.remaining("c2c", "b", "same") == 4
    assert rules.remaining("group", "a", "same") == 5
