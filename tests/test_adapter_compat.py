"""Adapter-only compatibility contracts; no live transport or profile writes."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx

import pytest

from hermes_qqbot.streaming import QQNativeStreamingMixin, _Stream


class Adapter(QQNativeStreamingMixin):
    _native_progressive_host = staticmethod(lambda: True)

    def __init__(self):
        self.config = SimpleNamespace(extra={})
        self._markdown_support = False
        self._guess_chat_type = lambda chat: "c2c" if chat == "dm" else "guild"
        self._api_request = AsyncMock(return_value={"id": "remote"})
        self._ensure_connected = AsyncMock(return_value=True)
        self._message_rules = SimpleNamespace(remaining=lambda *args: 5)


@pytest.mark.asyncio
async def test_legacy_seed_is_local_not_a_remote_receipt():
    adapter = Adapter()
    assert adapter.supports_native_streaming(chat_type=None)
    assert adapter.supports_native_streaming(chat_type="dm")
    result = await adapter.send_stream_frame("", chat_id="dm", reply_to="r", turn_id="t")
    assert result is True
    state = adapter._native_states()["dm", "r", "t"]
    assert state.message_id is None and state.reservation is None
    adapter._api_request.assert_not_called()
    adapter._ensure_connected.assert_not_called()
    assert not await adapter.send_stream_frame("", finalize=True, chat_id="dm", reply_to="r", turn_id="t")
    assert not await adapter.send_stream_frame("hello", chat_id="guild", reply_to="r", turn_id="t")
    adapter._api_request.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_decorated_drafts_stay_local_until_clean_final():
    adapter = Adapter()
    adapter._native_progressive_host = lambda: False
    assert not adapter.supports_native_streaming(chat_type=None)
    assert not adapter.supports_native_streaming(chat_type="dm")
    adapter._message_rules.reserve = lambda *args: ("reservation",)
    adapter._next_msg_seq = lambda reply: 1
    adapter._markdown_support = False
    for draft in ("hello ▌", "hello world ▌", "hello world\n```python\nx\n```"):
        assert await adapter.send_stream_frame(draft, chat_id="dm", reply_to="r", turn_id="t") is True
    adapter._api_request.assert_not_called()
    assert await adapter.send_stream_frame("hello world", finalize=True, chat_id="dm", reply_to="r", turn_id="t") == "remote"
    assert adapter._api_request.await_count == 1
    assert adapter._api_request.call_args.args[2]["content_raw"] == "hello world"
    assert await adapter.send_stream_frame("hello world", finalize=True, chat_id="dm", reply_to="r", turn_id="t") == "remote"
    assert adapter._api_request.await_count == 1


@pytest.mark.asyncio
async def test_unknown_acceptance_never_finalizes_again():
    adapter = Adapter()
    adapter._native_states()["dm", "r", "t"] = _Stream(seq=1, unknown=True, message_id="remote", text="hello")
    assert not await adapter.send_stream_frame("hello", finalize=True, chat_id="dm", reply_to="r", turn_id="t")
    assert not await adapter._native_prompt_boundary("dm", "r")
    adapter._api_request.assert_not_called()


@pytest.mark.asyncio
async def test_prompt_seal_archives_once_and_approval_metadata_is_interim():
    adapter = Adapter()
    state = _Stream(seq=1, message_id="remote", text="hello", index=1)
    adapter._native_states()["dm", "r", "t"] = state
    assert await adapter._native_prompt_boundary("dm", "r")
    assert state.head == "hello" and state.text == "" and state.segment_final
    assert await adapter._native_prompt_boundary("dm", "r")
    assert adapter._api_request.await_count == 1
    assert await adapter._reconcile_native_send("dm", "prompt", "r", {"is_approval_prompt": True}) is None


@pytest.mark.asyncio
async def test_prompt_boundary_does_not_guess_between_concurrent_turns():
    adapter = Adapter()
    for turn in ("a", "b"):
        adapter._native_states()["dm", "r", turn] = _Stream(seq=1, message_id=turn, text=turn)
    assert not await adapter._native_prompt_boundary("dm", "r")
    adapter._api_request.assert_not_called()

@pytest.mark.asyncio
@pytest.mark.parametrize("error", [httpx.ConnectError("offline"), httpx.ConnectTimeout("offline")])
async def test_native_connect_rejection_refunds_and_allows_one_final(error):
    from gateway.platforms.base import SendResult
    bot = Adapter()
    bot.MAX_MESSAGE_LENGTH = 2000
    bot._next_msg_seq = lambda reply: 1
    bot._message_rules.reserve = Mock(return_value=("reservation",))
    bot._message_rules.refund = Mock()
    bot._api_request.side_effect = error
    bot._send_chunk = AsyncMock(return_value=SendResult(success=True, message_id="final"))
    assert not await bot.send_stream_frame("answer", chat_id="dm", reply_to="r", turn_id="t")
    bot._message_rules.refund.assert_called_once_with(("reservation",))
    state = bot._native_states()["dm", "r", "t"]
    assert not state.unknown and state.reservation is None
    for _ in range(2):
        assert (await bot._reconcile_native_send("dm", "answer", "r", {"turn_id": "t"})).success
    bot._send_chunk.assert_awaited_once_with("dm", "answer", "r")


def real_adapter():
    from gateway.config import PlatformConfig
    from hermes_qqbot.adapter import QQAdapter
    bot = QQAdapter(PlatformConfig(enabled=True, extra={"app_id": "test", "client_secret": "test"}))
    bot._chat_type_map["target"] = "c2c"
    bot._ensure_connected = AsyncMock(return_value=True)
    bot._api_request = AsyncMock(return_value={"id": "receipt"})
    return bot


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [httpx.ReadTimeout("lost ACK"), {}, {"bad": "receipt"}])
async def test_approval_ambiguous_receipt_never_claims_remote_delivery(outcome):
    from hermes_qqbot.keyboards import ApprovalRequest
    bot = real_adapter()
    if isinstance(outcome, Exception):
        bot._api_request.side_effect = outcome
    else:
        bot._api_request.return_value = outcome
    req = ApprovalRequest(session_key="agent:main:qqbot:dm:target", title="Approve?", description="command")
    result = await bot.send_approval_request("target", req, reply_to="r")
    assert not result.success and not result.retryable
    assert result.raw_response["ambiguous"] and result.message_id is None
    assert bot._api_request.await_count == 1


@pytest.mark.asyncio
async def test_approval_preserves_original_turn_not_newer_inbound():
    bot = real_adapter()
    bot._last_msg_id["target"] = "newer-inbound"
    bot.send_with_keyboard = AsyncMock()
    original = _Stream(seq=1, message_id="original", text="A", index=1)
    newer = _Stream(seq=2, message_id="newer", text="B", index=1)
    bot._native_states()["target", "original-inbound", "turn-A"] = original
    bot._native_states()["target", "newer-inbound", "turn-B"] = newer
    prompt = SimpleNamespace(chat_id="target", session_key="agent:main:qqbot:dm:target", description="command", smart_denied=False, command="echo ok", choices=["once"], metadata={"reply_to_message_id": "original-inbound", "turn_id": "turn-A"})
    await bot._send_exec_approval_prompt(prompt)
    assert original.head == "A" and not original.text
    assert newer.text == "B" and not newer.finalized
    assert bot._api_request.call_args.args[2]["msg_id"] == "original-inbound"
    assert bot.send_with_keyboard.call_args.kwargs["reply_to"] == "original-inbound"


@pytest.mark.asyncio
async def test_only_owned_pending_working_heartbeat_is_suppressed(monkeypatch):
    from agent.i18n import t
    from gateway.platforms.base import SendResult
    from tools import clarify_gateway as cg
    import threading
    bot = real_adapter()
    entry = SimpleNamespace(session_key="agent:main:qqbot:dm:target", event=threading.Event())
    monkeypatch.setitem(cg._entries, "owned", entry)
    bot._qq_clarify_waits = {"owned": (entry, "target")}
    bot._send_standard_text = AsyncMock(return_value=SendResult(success=True, message_id="sent"))
    heartbeat = t("gateway.progress.working_heartbeat", minutes=3, detail=" — processing")
    result = await bot.send("target", heartbeat, metadata={"_interim_send": True})
    assert result.success and result.raw_response["suppressed"] and result.message_id is None
    bot._api_request.assert_not_called()
    bot._send_standard_text.assert_not_called()
    for content, metadata in [
        ("Gateway draining for restart", {"_interim_send": True}),
        (heartbeat, {"_interim_send": True, "is_approval_prompt": True}),
        (heartbeat, {"_interim_send": True, "session_key": "agent:foreign:qqbot:dm:target"}),
        (heartbeat, {}),
    ]:
        assert (await bot.send("target", content, metadata=metadata)).message_id == "sent"
    assert bot._send_standard_text.await_count == 4
    entry.event.set()
    assert (await bot.send("target", heartbeat, metadata={"_interim_send": True})).message_id == "sent"
    assert not bot._qq_clarify_waits
