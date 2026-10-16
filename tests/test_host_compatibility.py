"""Real host consumer + plugin transport: run on current and frozen gateways."""
from unittest.mock import AsyncMock

import httpx
import pytest

from gateway.config import PlatformConfig
from gateway.stream_consumer import GatewayStreamConsumer, StreamConsumerConfig
from hermes_qqbot import QQAdapter


def bot():
    adapter = QQAdapter(PlatformConfig(enabled=True, extra={
        "app_id": "test", "client_secret": "test", "markdown_support": True,
    }))
    adapter._chat_type_map["user"] = "c2c"
    adapter._last_msg_id["user"] = "inbound"
    adapter._ensure_connected = AsyncMock(return_value=True)
    adapter._auth_headers = AsyncMock(return_value={})
    adapter._http_client = AsyncMock()
    adapter._http_client.request.return_value = httpx.Response(200, json={"id": "remote"})
    return adapter


def consumer(adapter):
    result = GatewayStreamConsumer(
        adapter, "user", StreamConsumerConfig(chat_type="dm", cursor=" ▉"),
        metadata={"qq_chat_type": "c2c"}, initial_reply_to_id="inbound")
    result._use_native_streaming = result._resolve_native_streaming()
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["北京。", "Example ```py\nprint(1)\n```", "✅"])
async def test_final_without_deltas_has_one_real_delivery(answer):
    adapter = bot()
    sc = consumer(adapter)
    sc.finish(answer)
    await sc.run()
    if not sc.final_content_delivered:
        sent = await adapter.send("user", answer, "inbound", {"turn_id": sc._turn_id})
        assert sent.success
    calls = adapter._http_client.request.call_args_list
    assert len(calls) == 1
    body = calls[0].kwargs["json"]
    assert (body.get("content_raw") or body.get("content") or
            body.get("markdown", {}).get("content")) == answer
    assert body.get("msg_id") == "inbound"
    assert adapter._message_rules.remaining("c2c", "user", "inbound") == 3


@pytest.mark.asyncio
async def test_delta_code_fences_and_final_do_not_create_duplicate_or_cursor():
    adapter = bot()
    sc = consumer(adapter)
    sc.on_delta("Example ```py\n")
    sc.on_delta("print(1)\n```")
    sc.finish("Example ```py\nprint(1)\n```")
    await sc.run()
    if not sc.final_content_delivered:
        sent = await adapter.send("user", "Example ```py\nprint(1)\n```", "inbound",
                                  {"turn_id": sc._turn_id})
        assert sent.success
    calls = adapter._http_client.request.call_args_list
    bodies = [call.kwargs["json"] for call in calls]
    assert bodies
    assert all("▉" not in body.get("content_raw", body.get("content", "")) for body in bodies)
    ordinary = [call for call in calls if call.args[1].endswith("/messages")]
    streams = [call for call in calls if call.args[1].endswith("/stream_messages")]
    assert not (ordinary and streams), "must not replay native delivery as an ordinary copy"
    if streams:
        assert sum(call.kwargs["json"]["input_state"] == 10 for call in streams) == 1
    else:
        assert len(ordinary) == 1


@pytest.mark.asyncio
async def test_unknown_first_post_is_terminal_no_second_wire_send():
    adapter = bot()
    adapter._http_client.request.side_effect = httpx.ReadTimeout("accepted response lost")
    sent = await adapter.send("user", "北京。", "inbound")
    assert not sent.success
    assert not sent.retryable
    assert sent.raw_response.get("delivery_unknown") or sent.raw_response.get("qq_send_terminal")
    assert adapter._http_client.request.await_count == 1
