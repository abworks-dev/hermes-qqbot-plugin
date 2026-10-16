"""QQ passive split delivery, regression for #126451 and #44294.

Use the real adapter and base retry path with a fake QQ REST transport.
"""
import re
import httpx
import pytest
from unittest.mock import AsyncMock

from gateway.config import PlatformConfig
from hermes_qqbot import QQAdapter


def make_adapter(monkeypatch, chat_type="group"):
    adapter = QQAdapter(PlatformConfig(enabled=True, extra={
        "app_id": "test", "client_secret": "test", "markdown_support": False,
    }))
    monkeypatch.setattr(adapter, "_ensure_connected", AsyncMock(return_value=True))
    monkeypatch.setattr(adapter, "_guess_chat_type", lambda _: chat_type)
    monkeypatch.setattr(adapter, "truncate_message", lambda *_: ["head", "middle", "tail"])
    return adapter


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_type", ["group", "c2c"])
async def test_all_chunks_use_inbound_id_and_distinct_sequence(monkeypatch, chat_type):
    adapter = make_adapter(monkeypatch, chat_type)
    sent = []

    async def post(method, path, body):
        sent.append(body.copy())
        assert body.get("msg_id") == "inbound"
        return {"id": f"sent-{len(sent)}"}

    monkeypatch.setattr(adapter, "_api_request", post)
    result = await adapter._send_with_retry("target", "long answer", reply_to="inbound")
    assert result.success
    assert [b["content"] for b in sent] == ["head", "middle", "tail"]
    assert len({b["msg_seq"] for b in sent}) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit,expected", [(None, "metadata-id"), ("explicit-id", "explicit-id")])
async def test_gateway_metadata_anchor_and_explicit_precedence(monkeypatch, explicit, expected):
    adapter = make_adapter(monkeypatch)
    post = AsyncMock(return_value={"id": "sent"})
    monkeypatch.setattr(adapter, "_api_request", post)
    result = await adapter.send("target", "long", reply_to=explicit,
                                metadata={"reply_to_message_id": "metadata-id"})
    assert result.success
    assert all(call.args[2]["msg_id"] == expected for call in post.call_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    "QQ Bot API error [400] /v2/groups/target/messages: 主动消息失败, 无权限",
    "QQ Bot API error [400] /v2/groups/target/messages: 被动回复时间或次数超限",
    "QQ Bot API error [403] /v2/groups/target/messages: denied",
    "QQ Bot API error [502] /v2/groups/target/messages: upstream error",
])
@pytest.mark.parametrize("fail_on", ["head", "middle"])
async def test_refusal_or_ambiguous_error_never_replays_visible_head(monkeypatch, error, fail_on):
    adapter = make_adapter(monkeypatch)
    visible = []
    attempted = []

    async def post(method, path, body):
        attempted.append(body["content"])
        if body["content"] == fail_on:
            raise RuntimeError(error)
        visible.append(body["content"])
        return {"id": f"sent-{len(visible)}"}

    monkeypatch.setattr(adapter, "_api_request", post)
    result = await adapter._send_with_retry("target", "long", reply_to="inbound", base_delay=0)
    assert not result.success
    assert attempted == (["head"] if fail_on == "head" else ["head", "middle"])
    assert visible == ([] if fail_on == "head" else ["head"])
    assert not result.retryable
    if fail_on == "middle":
        assert result.raw_response["partial_overflow"]
        assert result.raw_response["undelivered_chunks"] == ["middle", "tail"]
        assert result.raw_response["delivered_message_ids"] == ["sent-1"]


@pytest.mark.asyncio
async def test_read_timeout_after_possible_acceptance_not_retried(monkeypatch):
    adapter = make_adapter(monkeypatch)
    attempted = []

    async def post(method, path, body):
        attempted.append(body["content"])
        if body["content"] == "middle":
            raise httpx.ReadTimeout("")
        return {"id": "sent-head"}

    monkeypatch.setattr(adapter, "_api_request", post)
    result = await adapter._send_with_retry("target", "long", reply_to="inbound", base_delay=0)
    assert not result.success
    assert "ReadTimeout" in result.error
    assert attempted == ["head", "middle"]


@pytest.mark.asyncio
@pytest.mark.parametrize("exhaust", [False, True])
async def test_429_retries_only_rejected_chunk_not_visible_head(monkeypatch, exhaust):
    adapter = make_adapter(monkeypatch)
    monkeypatch.setattr("hermes_qqbot.adapter.asyncio.sleep", AsyncMock())
    attempted = []
    visible = []
    refusals = 0

    async def post(method, path, body):
        nonlocal refusals
        attempted.append(body["content"])
        if body["content"] == "middle" and (exhaust or refusals == 0):
            refusals += 1
            raise RuntimeError("QQ Bot API error [429] /messages: rate limited")
        visible.append(body["content"])
        return {"id": f"sent-{len(visible)}"}

    monkeypatch.setattr(adapter, "_api_request", post)
    result = await adapter._send_with_retry("target", "long", reply_to="inbound", base_delay=0)
    assert result.success is not exhaust
    assert visible == (["head"] if exhaust else ["head", "middle", "tail"])
    assert attempted == (["head", "middle", "middle", "middle"] if exhaust else
                         ["head", "middle", "middle", "tail"])


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout_class,exhaust,expected_calls", [
    (httpx.ConnectTimeout, False, 2), (httpx.ConnectTimeout, True, 3),
    (httpx.ReadTimeout, False, 1),
])
async def test_real_api_timeout_wrapper_preserves_safe_retry_boundary(monkeypatch, timeout_class, exhaust, expected_calls):
    adapter = make_adapter(monkeypatch)
    monkeypatch.setattr(adapter, "truncate_message", lambda *_: ["head"])
    monkeypatch.setattr(adapter, "_auth_headers", AsyncMock(return_value={}))
    monkeypatch.setattr("hermes_qqbot.adapter.asyncio.sleep", AsyncMock())
    response = httpx.Response(200, json={"id": "sent"})
    client = AsyncMock()
    client.request.side_effect = [timeout_class("")] * 3 if exhaust else [timeout_class(""), response]
    monkeypatch.setattr(adapter, "_http_client", client)
    result = await adapter._send_with_retry("target", "long", reply_to="inbound", base_delay=0)
    assert result.success is (timeout_class is httpx.ConnectTimeout and not exhaust)
    assert client.request.call_count == expected_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("markdown", [True, False])
async def test_real_long_answer_split_keeps_entire_tail(monkeypatch, markdown):
    adapter = QQAdapter(PlatformConfig(enabled=True, extra={
        "app_id": "test", "client_secret": "test", "markdown_support": markdown,
    }))
    monkeypatch.setattr(adapter, "_ensure_connected", AsyncMock(return_value=True))
    monkeypatch.setattr(adapter, "_guess_chat_type", lambda _: "group")
    post = AsyncMock(return_value={"id": "sent"})
    monkeypatch.setattr(adapter, "_api_request", post)
    content = "甲" * 4500 + "乙" * 4500 + "最后一行"
    result = await adapter._send_with_retry("target", content, reply_to="inbound")
    assert result.success
    bodies = [call.args[2] for call in post.call_args_list]
    assert 2 <= len(bodies) <= 5
    assert all(b["msg_id"] == "inbound" for b in bodies)
    assert len({b["msg_seq"] for b in bodies}) == len(bodies)
    texts = [b["markdown"]["content"] if markdown else b["content"] for b in bodies]
    assert all(len(text) <= adapter.MAX_MESSAGE_LENGTH for text in texts)
    without_indicators = [re.sub(r"\s*\(\d+/\d+\)$", "", text) for text in texts]
    assert "".join(without_indicators) == content
