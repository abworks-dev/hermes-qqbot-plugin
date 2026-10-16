"""QQ native protocol and immutable-prefix fallback through the real consumer."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.platforms.base import BasePlatformAdapter, SendResult
from hermes_qqbot.streaming import QQNativeStreamingMixin
from hermes_qqbot.message_rules import QQMessageRules
from gateway.stream_consumer import GatewayStreamConsumer, StreamConsumerConfig

# These exercise the modern host's progressive transport contract. The frozen
# legacy host is covered separately by adapter-only and real-consumer fallback
# tests; it intentionally does not advertise progressive native streaming.
pytestmark = pytest.mark.skipif(
    not callable(getattr(GatewayStreamConsumer, "close_for_approval_prompt_sync", None)),
    reason="Legacy host: progressive native contract unavailable; test buffered compatibility separately",
)


class _Adapter(QQNativeStreamingMixin, BasePlatformAdapter):
    # Exercise legacy seeded transport compatibility as well as real QQ below.
    NATIVE_STREAM_CONTENT_FIRST = False
    SUPPORTS_MESSAGE_EDITING = False
    MAX_MESSAGE_LENGTH = 4096

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        result = await self._reconcile_native_send(chat_id, content, reply_to, metadata)
        if result is not None:
            return result
        return await self._send_chunk(chat_id, content, reply_to)


_Adapter.__abstractmethods__ = frozenset()


def adapter():
    a = _Adapter.__new__(_Adapter)
    a.config = SimpleNamespace(extra={})
    a._markdown_support = True
    a._message_rules = QQMessageRules()
    a._typing_paused = set()
    a._fatal_error_message = None
    a._guess_chat_type = lambda chat: "c2c" if chat == "user" else "dm"
    a._ensure_connected = AsyncMock(return_value=True)
    a._next_msg_seq = lambda reply: 7
    a._api_request = AsyncMock(return_value={"id": "first"})
    a._send_chunk = AsyncMock(return_value=SendResult(success=True, message_id="tail"))
    a.edit_message = AsyncMock()
    return a


async def frame(a, text, **kwargs):
    return await a.send_stream_frame(text, chat_id="user", reply_to="inbound", turn_id="turn", **kwargs)


def consumer(a):
    sc = GatewayStreamConsumer(a, "user", StreamConsumerConfig(chat_type="dm", cursor=" ▉"),
                               metadata={"qq_chat_type": "c2c"}, initial_reply_to_id="inbound")
    sc._use_native_streaming = sc._resolve_native_streaming()
    return sc


@pytest.mark.asyncio
async def test_wire_sequence_first_id_and_final_ack():
    a = adapter()
    a._api_request.side_effect = [{"id": "first"}, {"id": "different"}, {"id": "last"}]
    assert await frame(a, "") is True  # local initialization, not a remote receipt
    a._api_request.assert_not_called()
    assert await frame(a, "Hello") == "first"
    assert await frame(a, "Hello world", finalize=True) == "first"
    bodies = [c.args[2] for c in a._api_request.call_args_list]
    assert [b["index"] for b in bodies] == [0, 1]
    assert [b["input_state"] for b in bodies] == [1, 10]
    assert all(b["msg_seq"] == 7 and b["input_mode"] == "replace" for b in bodies)
    assert "stream_msg_id" not in bodies[0]
    assert [b["stream_msg_id"] for b in bodies[1:]] == ["first"]
    assert all(c.args[:2] == ("POST", "/v2/users/user/stream_messages")
               for c in a._api_request.call_args_list)


@pytest.mark.asyncio
async def test_real_consumer_preserves_fences_and_final_delivery():
    a = adapter()
    sc = consumer(a)
    await sc._send_or_edit("Example ```py\nprint(1) ▉")
    await sc._send_or_edit("Example ```py\nprint(1)\n```", finalize=True)
    bodies = [c.args[2] for c in a._api_request.call_args_list]
    assert bodies[0]["content_raw"] == "Example ```py\nprint(1)"
    assert len(bodies) == 2  # no remote empty seed
    assert bodies[-1]["content_raw"] == "Example ```py\nprint(1)\n```"
    assert sc.final_content_delivered
    a._send_chunk.assert_not_called()
    a.edit_message.assert_not_called()


@pytest.mark.asyncio
async def test_real_consumer_definitive_failure_sends_only_residual():
    a = adapter()
    sc = consumer(a)
    await sc._send_or_edit("Visible prefix ▉")
    a._api_request.side_effect = RuntimeError("QQ Bot API error [400] rejected")
    assert not await sc._send_or_edit("Visible prefix more ▉")
    assert sc.cfg.buffer_only
    assert await sc._send_or_edit("Visible prefix final", finalize=True)
    a._send_chunk.assert_awaited_once_with("user", " final", "inbound")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("timeout"), RuntimeError("QQ Bot API error [500] maybe")])
async def test_unknown_acceptance_never_replays(failure):
    a = adapter()
    assert await frame(a, "Visible")
    a._api_request.side_effect = failure
    assert not await frame(a, "Visible uncertain", finalize=True)
    assert not await frame(a, "Visible uncertain", finalize=True)
    result = await a.send("user", "Visible uncertain", "inbound")
    assert not result.success and not result.retryable
    assert result.raw_response["delivery_unknown"]
    a._send_chunk.assert_not_called()
    assert a._api_request.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("divergent", [False, True])
async def test_clarify_resume_final_never_reposts_visible_answer(divergent):
    a = real_adapter()
    sc = consumer(a)
    sc._append_accumulated("等待你的选择")
    await sc._send_or_edit("等待你的选择 ▉")
    assert await sc._finalize_boundary_stream("Clarify")
    a.native_stream_boundary("user", reply_to="inbound", turn_id=sc._turn_id)
    sc._close_native_state()
    sc._reset_segment_state()
    prefix = "你选择了北京。这次计划旅行几天？预算是多少"
    await sc._send_or_edit(prefix + (">" if divergent else "") + " ▉")
    final = prefix + "，是否包含往返交通？"
    await sc._finalize_edit(final)
    assert sc.final_content_delivered is (not divergent)
    assert all(call.args[1].endswith("/stream_messages")
               for call in a._http_client.request.call_args_list)
    # An outer final retry must also refuse a full-copy replacement of visible text.
    result = await a.send("user", final, "inbound")
    assert result.success is (not divergent)
    if divergent:
        assert result.raw_response["delivered_prefix"].endswith(prefix + ">")


@pytest.mark.asyncio
async def test_changed_final_is_honest_partial_not_silent_truncation():
    a = adapter()
    assert await frame(a, "Original")
    assert not await frame(a, "Rewritten", finalize=True)
    result = await a.send("user", "Rewritten", "inbound")
    assert not result.success
    assert result.raw_response["delivered_prefix"] == "Original"
    a._send_chunk.assert_not_called()


@pytest.mark.asyncio
async def test_interim_and_other_turn_do_not_consume_receipt():
    a = adapter()
    assert await frame(a, "Visible")
    assert await a._reconcile_native_send("user", "comment", "inbound", {"_interim_send": True}) is None
    assert await a._reconcile_native_send("user", "other", "inbound", {"turn_id": "other"}) is None
    assert await a._reconcile_native_send("user", "other", "new-inbound") is None
    assert not await frame(a, "changed")
    r = await a._reconcile_native_send("user", "Visible tail", "inbound")
    assert r.success
    assert (await a._reconcile_native_send("user", "Visible tail", "inbound")).success
    a._send_chunk.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_id_and_cancel_are_unknown_no_retries():
    import asyncio
    for outcome in ({}, asyncio.CancelledError()):
        a = adapter()
        a._api_request.side_effect = [outcome]
        if isinstance(outcome, BaseException):
            with pytest.raises(asyncio.CancelledError):
                await frame(a, "Maybe")
        else:
            assert not await frame(a, "Maybe")
        result = await a.send("user", "Maybe final", "inbound")
        assert not result.success and result.raw_response["delivery_unknown"]
        a._send_chunk.assert_not_called()


@pytest.mark.asyncio
async def test_abandon_seals_known_snapshot_and_blocks_stale_fallback():
    a = adapter()
    assert await frame(a, "Known")
    await a.abandon_native_stream("user", "untrusted replacement", metadata={
        "turn_id": "turn", "reply_to_message_id": "inbound"})
    assert a._api_request.call_args.args[2]["content_raw"] == "Known"
    assert a._api_request.call_args.args[2]["input_state"] == 10
    assert not (await a.send("user", "Known tail", "inbound")).success
    a._send_chunk.assert_not_called()


def test_probe_c2c_only_and_config_toggle():
    a = adapter()
    assert a.supports_native_streaming(metadata={"qq_chat_type": "c2c"})
    assert a.supports_native_streaming(metadata={"chat_id": "user"})
    assert a.supports_native_streaming(chat_type="dm")  # route checked before actual HTTP
    assert not a.supports_native_streaming(chat_type="dm", metadata={"qq_chat_type": "dm"})
    assert not a.supports_native_streaming(chat_type="group")
    a.config.extra["native_streaming"] = False
    assert not a.supports_native_streaming(chat_type="c2c")


@pytest.mark.asyncio
async def test_capacity_refuses_new_stream_without_evicting_unsafe_prefix():
    a = adapter()
    a._NATIVE_STREAM_LIMIT = 1
    assert await frame(a, "Known")
    assert not await a.send_stream_frame("new", chat_id="user", reply_to="next", turn_id="next")
    assert len(a._native_states()) == 1
    assert not (await a.send("user", "Changed", "inbound")).success


@pytest.mark.asyncio
@pytest.mark.parametrize("code,rejected", [(40007, True), (50001, False)])
async def test_real_http_business_rejection_evidence_controls_replay(code, rejected):
    import httpx
    a = real_adapter()
    a._http_client.request.return_value = httpx.Response(200, json={"code": code, "message": "business"})
    assert not await frame(a, "seed")
    state = a._native_states()[("user", "inbound", "turn")]
    assert state.unknown is not rejected
    assert a._message_rules.remaining("c2c", "user", "inbound") == (4 if rejected else 3)


def real_adapter():
    import httpx
    from gateway.config import PlatformConfig
    from hermes_qqbot import QQAdapter
    a = QQAdapter(PlatformConfig(enabled=True, extra={
        "app_id": "test", "client_secret": "test", "markdown_support": False}))
    a._chat_type_map["user"] = "c2c"
    a._ensure_connected = AsyncMock(return_value=True)
    a._auth_headers = AsyncMock(return_value={})
    a._http_client = AsyncMock()
    a._http_client.request.return_value = httpx.Response(200, json={"id": "remote"})
    return a


@pytest.mark.asyncio
async def test_real_adapter_stream_reserves_once_and_residual_shares_quota():
    import httpx
    a = real_adapter()
    assert await frame(a, "")
    assert await frame(a, "Known")
    assert a._message_rules.remaining("c2c", "user", "inbound") == 3
    a._http_client.request.return_value = httpx.Response(400, json={"message": "rejected"})
    assert not await frame(a, "Known tail", finalize=True)
    assert a._message_rules.remaining("c2c", "user", "inbound") == 3
    a._http_client.request.return_value = httpx.Response(200, json={"id": "tail"})
    assert (await a.send("user", "Known tail", "inbound")).success
    assert a._message_rules.remaining("c2c", "user", "inbound") == 2
    body = a._http_client.request.call_args.kwargs["json"]
    assert body["content"] == " tail"


@pytest.mark.asyncio
async def test_real_adapter_expiry_prunes_receipts_without_reviving_authority(monkeypatch):
    import time
    now = [10000]
    monkeypatch.setattr(time, "time", lambda: now[0])
    a = real_adapter()
    a._NATIVE_STREAM_LIMIT = 1
    assert await frame(a, "Known")
    now[0] += 3601
    assert not (await a.send("user", "Known late", "inbound")).success
    assert not a._native_states()
    assert await a.send_stream_frame("fresh", chat_id="user", reply_to="fresh", turn_id="fresh")
    assert len(a._native_states()) == 1


@pytest.mark.asyncio
async def test_real_adapter_initial_rejection_refunds_but_unknown_keeps_reservation():
    import httpx
    a = real_adapter()
    a._http_client.request.return_value = httpx.Response(400, json={"message": "rejected"})
    assert not await frame(a, "seed")
    assert a._message_rules.remaining("c2c", "user", "inbound") == 4
    a = real_adapter()
    a._http_client.request.side_effect = httpx.ReadTimeout("lost ack")
    assert not await frame(a, "seed")
    assert a._message_rules.remaining("c2c", "user", "inbound") == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_tail", [False, True])
async def test_real_consumer_boundary_reopens_new_stream_and_preserves_heads(fail_tail):
    import httpx
    a = real_adapter()
    sc = consumer(a)
    sc._append_accumulated("Head text")
    await sc._send_or_edit("Head text ▉")
    assert await sc._finalize_boundary_stream("Clarify")
    a.native_stream_boundary("user", reply_to="inbound", turn_id=sc._turn_id)
    sc._close_native_state()
    sc._reset_segment_state()
    await sc._send_or_edit("Tail text ▉")
    bodies = [call.kwargs["json"] for call in a._http_client.request.call_args_list]
    seeds = [body for body in bodies if body.get("index") == 0]
    assert len(seeds) == 2
    assert seeds[0]["msg_seq"] != seeds[1]["msg_seq"]
    assert a._message_rules.remaining("c2c", "user", "inbound") == 2
    if fail_tail:
        a._http_client.request.return_value = httpx.Response(400, json={"message": "rejected"})
        assert not await sc._send_or_edit("Tail text extra ▉")
        a._http_client.request.return_value = httpx.Response(200, json={"id": "residual"})
    assert await sc._finalize_edit("Tail text final")
    assert sc.final_content_delivered
    if fail_tail:
        assert a._http_client.request.call_args.kwargs["json"]["content"] == " final"
    # The outer authoritative payload may acknowledge the full ledger, never a
    # segment-shaped rewrite of a head already visible before the boundary.
    assert all(body["content_raw"] for body in bodies)  # no empty opening frame
    result = await a.send("user", "Tail text final", "inbound")
    assert result.success
    assert not (await a.send("user", "Changed final", "inbound")).success


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_boundary", [False, True])
async def test_real_approval_boundary_does_not_freeze_next_buffered_final(fail_boundary):
    import asyncio
    import httpx
    a = real_adapter()
    sc = consumer(a)
    sc._append_accumulated("Head text")
    await sc._send_or_edit("Head text ▉")
    if fail_boundary:
        sc._append_accumulated(" extra")
        a._http_client.request.side_effect = [
            httpx.Response(400, json={"message": "rejected"}),
            httpx.Response(200, json={"id": "closed-known-head"}),
            httpx.Response(200, json={"id": "boundary-tail"}),
        ]
    future = asyncio.get_running_loop().create_future()
    await sc._handle_approval_boundary(future)
    assert future.result()
    assert sc.cfg.buffer_only and not sc._use_native_streaming
    a._http_client.request.side_effect = None
    sc._append_accumulated("New final")
    assert await sc._finalize_edit("New final")
    assert sc.final_content_delivered
    head = "Head text extra" if fail_boundary else "Head text"
    # The real boundary owner declares that the outer final is the new segment,
    # not a concatenation of the earlier approval prompt and this answer.
    assert (await a.send("user", "New final", "inbound")).success
    assert (await a.send("user", head + "New final", "inbound", metadata={
        "_native_stream_segment": False})).success
    bodies = [call.kwargs["json"] for call in a._http_client.request.call_args_list]
    assert bodies[-1]["content"] == "New final"


@pytest.mark.asyncio
async def test_residual_chunks_track_source_offsets_not_display_indicators():
    a = adapter()
    a.MAX_MESSAGE_LENGTH = 3
    assert await frame(a, "Head")
    assert not await frame(a, "Changed")
    a.truncate_message = lambda *_: ["WRONG DISPLAY INDICATOR"]
    assert (await a.send("user", "Headabcdef", "inbound")).success
    assert [c.args[1] for c in a._send_chunk.call_args_list] == ["abc", "def"]
    assert a._native_states()[("user", "inbound", "turn")].text == "Headabcdef"


@pytest.mark.asyncio
@pytest.mark.parametrize("divergent", [False, True])
async def test_explicit_clarify_boundary_outer_final_uses_answer_segment(divergent):
    import asyncio
    a = adapter()
    sc = consumer(a)
    sc._boundary_reopen = True
    sc._append_accumulated("Choose a city")
    await sc._send_or_edit("Choose a city ▉")
    future = asyncio.get_running_loop().create_future()
    await sc._handle_approval_boundary(future)
    assert future.result()
    await sc._eager_reopen_seed()
    assert await a.send_stream_frame("你>" if divergent else "你", chat_id="user",
                                     reply_to="inbound", turn_id=sc._turn_id)
    # A proven server refusal permits residual-only delivery, never replay.
    a._api_request.side_effect = RuntimeError("QQ Bot API error [400] rejected")
    assert not await sc._send_or_edit("你选择了北京。 ▉")
    result = await a.send("user", "你选择了北京。", "inbound")
    assert result.success is (not divergent)
    if divergent:
        a._send_chunk.assert_not_called()
    else:
        a._send_chunk.assert_awaited_once_with("user", "选择了北京。", "inbound")
        assert (await a.send("user", "你选择了北京。", "inbound")).success
        a._send_chunk.assert_awaited_once()


@pytest.mark.asyncio
async def test_explicit_boundary_unknown_ack_still_blocks_outer_replay(caplog):
    a = adapter()
    assert await frame(a, "Private prompt", finalize=True)
    a.native_stream_boundary("user", reply_to="inbound", turn_id="turn")
    assert await frame(a, "")
    assert await frame(a, "Private answer prefix")
    a._api_request.side_effect = RuntimeError("timeout containing private content")
    assert not await frame(a, "Private answer prefix tail", finalize=True)
    result = await a.send("user", "Private answer prefix tail", "inbound")
    assert not result.success and result.raw_response["delivery_unknown"]
    a._send_chunk.assert_not_called()
    assert "acceptance_unknown=True" in caplog.text
    assert "Private prompt" not in caplog.text
    assert "Private answer" not in caplog.text
    assert "timeout containing private content" not in caplog.text


@pytest.mark.asyncio
async def test_multiple_explicit_boundaries_keep_current_segment_scope():
    a = adapter()
    for text in ("First prompt", "Second prompt"):
        assert await frame(a, "")
        assert await frame(a, text, finalize=True)
        a.native_stream_boundary("user", reply_to="inbound", turn_id="turn")
    assert await frame(a, "")
    assert await frame(a, "Final answer", finalize=True)
    a._send_chunk.reset_mock()
    assert (await a.send("user", "Final answer", "inbound")).success
    assert (await a.send("user", "Final answer", "inbound")).success
    a._send_chunk.assert_not_called()
    state = a._native_states()[("user", "inbound", "turn")]
    assert state.head == "First promptSecond prompt"
    assert state.text == "Final answer"


@pytest.mark.asyncio
async def test_divergent_final_seals_exact_acked_snapshot_without_claiming_answer(caplog):
    a = adapter()
    visible = "你选择了北京。\n\n"
    assert await frame(a, visible)
    assert not await frame(a, "你选择了北京。", finalize=True)
    body = a._api_request.call_args.args[2]
    assert body["input_state"] == 10 and body["content_raw"] == visible
    state = a._native_states()[("user", "inbound", "turn")]
    assert state.finalized and not state.unknown
    result = await a.send("user", "你选择了北京。", "inbound")
    assert not result.success and result.raw_response["stream_closed"]
    assert not result.raw_response["delivery_unknown"]
    assert a._api_request.await_count == 2  # one content + one close, no retries
    a._send_chunk.assert_not_called()
    assert "first_diff=7" in caplog.text
    assert visible not in caplog.text


@pytest.mark.asyncio
async def test_close_unknown_never_retries_or_duplicates():
    a = adapter()
    assert await frame(a, "Visible snapshot")
    a._api_request.side_effect = RuntimeError("lost close ACK")
    assert not await frame(a, "Changed", finalize=True)
    for _ in range(2):
        result = await a.send("user", "Changed", "inbound")
        assert not result.success and result.raw_response["delivery_unknown"]
        assert not result.raw_response["stream_closed"]
    assert a._api_request.await_count == 2
    a._send_chunk.assert_not_called()


@pytest.mark.asyncio
async def test_residual_content_success_does_not_claim_rejected_close():
    a = adapter()
    assert await frame(a, "Visible")
    a._api_request.side_effect = RuntimeError("QQ Bot API error [400] rejected")
    assert not await frame(a, "Visible tail", finalize=True)
    result = await a.send("user", "Visible tail", "inbound")
    assert result.success and result.raw_response["native_residual"]
    assert result.raw_response["stream_closed"] is False
    a._send_chunk.assert_awaited_once_with("user", " tail", "inbound")
