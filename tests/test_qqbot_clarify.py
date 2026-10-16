"""QQ clarify choice cards, text fallback, identity and waiter lifecycle contracts."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import SendResult
from hermes_qqbot import QQAdapter
from hermes_qqbot.keyboards import InteractionEvent
from gateway.run_turn_runner_clarify_delivery import text_fallback_coro
from tools import clarify_gateway as cg


@pytest.fixture(autouse=True)
def clear_waiters():
    with cg._lock:
        cg._entries.clear()
        cg._session_index.clear()
    yield
    with cg._lock:
        cg._entries.clear()
        cg._session_index.clear()


def adapter(kind="c2c"):
    bot = QQAdapter(PlatformConfig(enabled=True, extra={
        "app_id": "test", "client_secret": "test", "markdown_support": False,
    }))
    bot._chat_type_map["target"] = kind
    bot._last_msg_id["target"] = "inbound"
    bot._ensure_connected = AsyncMock(return_value=True)
    bot._auth_headers = AsyncMock(return_value={"Authorization": "QQBot fake"})
    bot._http_client = AsyncMock()
    bot._http_client.request.return_value = httpx.Response(200, json={"id": "card"})
    bot._http_client.put.return_value = httpx.Response(200, json={})
    return bot


def register(choices=None, *, cid="question", kind="c2c", multi=False):
    key = "agent:secondary:qqbot:dm:target" if kind == "c2c" else f"agent:secondary:qqbot:{kind}:target:user"
    return cg.register(cid, key, "去哪里？", choices, multi_select=multi)


async def send(bot, entry, **kwargs):
    return await bot.send_clarify("target", entry.question, entry.choices, entry.clarify_id,
                                  entry.session_key, **kwargs)


def event(action="1", cid="question", kind="c2c", **changes):
    values = dict(id="interaction", scene=kind, button_data=f"clarify:{cid}:{action}")
    values.update(dict(user_openid="target") if kind == "c2c" else
                  dict(group_openid="target", group_member_openid="user"))
    values.update(changes)
    return InteractionEvent(**values)


async def fallback(bot, entry, **kwargs):
    return await text_fallback_coro(bot, chat_id="target", question=entry.question,
                                    choices=entry.choices, clarify_id=entry.clarify_id,
                                    session_key=entry.session_key, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"", b"{invalid-json"])
async def test_malformed_success_ack_keeps_card_and_waiter(content):
    bot = adapter()
    entry = register(["成都", "重庆"])
    bot._http_client.request.return_value = httpx.Response(200, content=content)
    result = await send(bot, entry)
    assert not result.success and result.raw_response["ambiguous"]
    assert bot._http_client.request.await_count == 1
    assert not entry.event.is_set()
    await bot._dispatch_clarify_interaction(event("2"))
    assert entry.event.is_set() and entry.response == "重庆"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["c2c", "group"])
async def test_native_wire_ack_resolve_and_replay(kind):
    bot = adapter(kind)
    entry = register(["成都（推荐）", "重庆"], kind=kind)
    result = await send(bot, entry, metadata={"reply_to_message_id": "original"})
    assert result.success
    wire = bot._http_client.request.call_args.kwargs["json"]
    assert wire["msg_type"] == 2 and wire["msg_id"] == "original"
    assert "1. 成都（推荐）" in wire["markdown"]["content"]
    buttons = wire["keyboard"]["content"]["rows"][0]["buttons"]
    assert buttons[0]["action"]["data"] == "clarify:question:1"
    assert buttons[0]["action"]["permission"] == {
        "type": 0, "specify_user_ids": ["target" if kind == "c2c" else "user"]}
    raw = {"id": "ack", "chat_type": 2 if kind == "c2c" else 1,
           "user_openid": "target" if kind == "c2c" else "",
           "group_openid": "target" if kind == "group" else "",
           "group_member_openid": "user" if kind == "group" else "",
           "data": {"type": 11, "resolved": {"button_data": "clarify:question:2"}}}
    await bot._on_interaction(raw)
    bot._http_client.put.assert_awaited_once()
    assert entry.response == "重庆" and entry.event.is_set()
    await bot._default_interaction_dispatch(event(kind=kind))
    assert entry.response == "重庆"


@pytest.mark.asyncio
async def test_free_text_prompt_stays_on_interim_lane():
    bot = adapter()
    entry = register()
    original_send = bot.send
    bot.send = AsyncMock(wraps=original_send)
    assert (await send(bot, entry)).success
    metadata = bot.send.call_args.kwargs["metadata"]
    assert metadata["_interim_send"] is True
    assert metadata["reply_to_message_id"] == "inbound"
    wire = bot._http_client.request.call_args.kwargs["json"]
    assert "keyboard" not in wire
    assert cg.resolve_text_response_for_session(entry.session_key, "成都")
    assert entry.response == "成都"


@pytest.mark.asyncio
async def test_numbered_reply_works_even_when_client_hides_keyboard():
    bot = adapter()
    entry = register(["成都", "重庆"])
    assert (await send(bot, entry)).success
    assert cg.attempt_text_response_for_session(entry.session_key, "9") == cg.TEXT_REJECTED_SELECTION
    assert not entry.event.is_set()
    assert cg.resolve_text_response_for_session(entry.session_key, "2")
    assert entry.response == "重庆"
    await bot._default_interaction_dispatch(event())
    assert entry.response == "重庆"


@pytest.mark.asyncio
async def test_other_switches_to_free_text_and_skip_uses_shared_sentinel():
    bot = adapter()
    entry = register(["成都", "重庆"])
    await send(bot, entry)
    await bot._default_interaction_dispatch(event("other"))
    assert entry.awaiting_text and not entry.event.is_set()
    await bot._default_interaction_dispatch(event())
    assert not entry.event.is_set()
    assert cg.resolve_text_response_for_session(entry.session_key, "西安")
    assert entry.response == "西安"
    next_entry = register(["三天", "五天"], cid="next")
    await send(bot, next_entry)
    await bot._default_interaction_dispatch(event("skip", "next"))
    assert next_entry.response == cg.SKIPPED


@pytest.mark.asyncio
async def test_multi_choice_accumulates_idempotently_then_confirms():
    bot = adapter()
    entry = register(["美食", "历史", "自然"], multi=True)
    await send(bot, entry)
    await bot._default_interaction_dispatch(event("submit"))
    assert not entry.event.is_set()
    for choice in ("3", "1", "3"):
        await bot._default_interaction_dispatch(event(choice))
    assert not entry.event.is_set()
    await bot._default_interaction_dispatch(event("submit"))
    assert json.loads(entry.response) == ["美食", "自然"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"group_member_openid": "stranger"}, {"group_openid": "other-group"},
    {"scene": "c2c", "user_openid": "user"},
    {"button_data": "clarify:missing:1"}, {"button_data": "clarify:question:99"},
    {"button_data": "clarify:question:-1"}, {"button_data": "clarify:question:bad:1"},
])
async def test_foreign_and_malformed_clicks_cannot_answer(changes):
    bot = adapter("group")
    entry = register(["成都", "重庆"], kind="group")
    await send(bot, entry)
    await bot._default_interaction_dispatch(event(kind="group", **changes))
    assert not entry.event.is_set()


@pytest.mark.asyncio
async def test_other_adapter_and_retired_or_replaced_waiter_cannot_answer():
    bot = adapter()
    entry = register(["成都", "重庆"])
    await send(bot, entry)
    await adapter()._default_interaction_dispatch(event())
    assert not entry.event.is_set()
    cg.clear_session(entry.session_key)
    newer = register(["三天", "五天"], cid="new")
    await send(bot, newer)
    await bot._default_interaction_dispatch(event())
    assert not newer.event.is_set()
    await bot.retire_clarify_card("new", "expired")
    await bot._default_interaction_dispatch(event(cid="new"))
    assert not newer.event.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,payload", [(403, {"message": "no keyboard permission"}),
                                            (200, {"code": 304023, "message": "keyboard rejected"})])
async def test_definite_keyboard_refusal_uses_shared_single_text_fallback(status, payload):
    bot = adapter()
    entry = register(["成都", "重庆"])
    bot._http_client.request.side_effect = [httpx.Response(status, json=payload),
                                          httpx.Response(200, json={"id": "text"})]
    assert not (await send(bot, entry)).success
    # The same runner seam used in production owns the retry; adapter does not send it twice.
    assert bot._http_client.request.call_count == 1
    bot.send = AsyncMock(wraps=bot.send)
    assert (await fallback(bot, entry, metadata={"reply_to_message_id": "inbound"})).success
    assert bot._http_client.request.call_count == 2
    # Older runners supply only is_approval_prompt; plugin reconciliation treats
    # that existing marker as interim without patching the host helper.
    sent_metadata = bot.send.call_args.kwargs["metadata"]
    assert (sent_metadata.get("_interim_send") is True or
            sent_metadata.get("is_approval_prompt") is True)
    wire = bot._http_client.request.call_args.kwargs["json"]
    assert "keyboard" not in wire and "1." in wire["content"]
    assert wire["msg_id"] == "inbound" and entry.awaiting_text
    assert cg.resolve_text_response_for_session(entry.session_key, "2")
    assert entry.response == "重庆"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx.ReadTimeout("lost ACK"),
                                      httpx.Response(503, json={"message": "unknown write"})])
async def test_ambiguous_native_delivery_keeps_waiter_without_replay(failure):
    bot = adapter()
    entry = register(["成都", "重庆"])
    if isinstance(failure, Exception):
        bot._http_client.request.side_effect = failure
    else:
        bot._http_client.request.return_value = failure
    result = await send(bot, entry)
    assert not result.success and result.raw_response["ambiguous"] is True
    assert bot._http_client.request.call_count == 1
    await bot._default_interaction_dispatch(event("2"))
    assert entry.response == "重庆"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,choices", [("guild", ["成都", "重庆"]),
                                          ("c2c", ["城市"] * 24),
                                          ("c2c", ["字" * 4000])])
async def test_unsupported_scene_size_falls_back_without_orphaned_buttons(kind, choices):
    bot = adapter(kind)
    entry = register(choices, kind=kind)
    assert not (await send(bot, entry)).success
    bot._http_client.request.assert_not_awaited()
    assert (await fallback(bot, entry, metadata={"reply_to_message_id": "inbound"})).success
    assert entry.awaiting_text
    assert all("keyboard" not in call.kwargs["json"] for call in bot._http_client.request.call_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("multi,count", [(False, 3), (True, 3), (False, 8), (True, 6)])
async def test_keyboard_layout_boundaries_preserve_long_labels_in_body(multi, count):
    bot = adapter()
    labels = [f"选项{i}很长的完整中文标签" for i in range(count)]
    entry = register(labels, multi=multi)
    assert (await send(bot, entry)).success
    wire = bot._http_client.request.call_args.kwargs["json"]
    rows = wire["keyboard"]["content"]["rows"]
    assert len(rows) <= 5 and all(len(row["buttons"]) <= 2 for row in rows)
    buttons = [button for row in rows for button in row["buttons"]]
    assert all(len(button["render_data"]["label"]) <= 10 for button in buttons)
    assert all(len(button["render_data"]["visited_label"]) <= 10 for button in buttons)
    assert all(label in wire["markdown"]["content"] for label in labels)
    assert [button["action"]["data"].rsplit(":", 1)[1] for button in rows[-1]["buttons"]] == ["other", "skip"]
    for button in buttons[:count]:
        assert button["render_data"]["visited_label"].startswith("✓ ")
        assert button["render_data"]["label"][:5] in button["render_data"]["visited_label"]
    if multi:
        assert len(rows[-2]["buttons"]) == 1
        assert rows[-2]["buttons"][0]["action"]["data"].endswith(":submit")


@pytest.mark.asyncio
@pytest.mark.parametrize("multi,count", [(False, 9), (True, 7)])
async def test_more_choices_use_numbered_fallback_instead_of_crowded_buttons(multi, count):
    bot = adapter()
    entry = register([f"选项{i}" for i in range(count)], multi=multi)
    assert not (await send(bot, entry)).success
    bot._http_client.request.assert_not_awaited()
    assert (await fallback(bot, entry)).success
    assert entry.awaiting_text


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["c2c", "group"])
async def test_compact_localized_card_short_buttons_keep_original_answers(kind):
    bot = adapter(kind)
    choices = ["北京 (Recommended)", "上海", "广州"]
    entry = register(choices, kind=kind)
    question = "\n 请选择一个城市。\n\n 回复skip可跳过此问题。\n"
    result = await bot.send_clarify("target", question, choices, entry.clarify_id,
                                   entry.session_key, metadata={"reply_to_message_id": "original"})
    assert result.success
    wire = bot._http_client.request.call_args.kwargs["json"]
    text = wire["markdown"]["content"]
    assert text.startswith("❓ 请选择一个城市。\n回复skip可跳过此问题。\n1. 北京（推荐）")
    assert "\n\n" not in text and "Recommended" not in text
    button = wire["keyboard"]["content"]["rows"][0]["buttons"][0]
    assert button["render_data"]["label"] == "1. 北京"
    assert button["render_data"]["visited_label"] == "✓ 1. 北京"
    assert "message_reference" not in wire and wire["msg_id"] == "original"
    if kind == "group":
        await bot._dispatch_clarify_interaction(event(kind="group", group_member_openid="stranger"))
        assert not entry.event.is_set()
    await bot._dispatch_clarify_interaction(event(kind=kind))
    assert entry.response == choices[0]  # presentation must never rewrite the answer
    assert entry.choices == choices


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["c2c", "group"])
async def test_manual_input_hint_is_contextual_once_and_preserves_waiter(kind):
    bot = adapter(kind)
    entry = register(["北京", "上海"], kind=kind)
    await send(bot, entry, metadata={"reply_to_message_id": "original"})
    body = bot._http_client.request.call_args.kwargs["json"]["markdown"]["content"]
    assert ("请 @机器人 后输入答案" if kind == "group" else "请直接发送答案") in body
    bot.send = AsyncMock(return_value=SendResult(success=True))
    await bot._dispatch_clarify_interaction(event("other", kind=kind))
    bot.send.assert_awaited_once()
    hint = bot.send.call_args.args[1]
    assert "已切换为手动输入" in hint
    assert ("@机器人 福建" in hint) is (kind == "group")
    assert bot.send.call_args.kwargs["metadata"]["_interim_send"] is True
    assert bot.send.call_args.kwargs["reply_to"] == "original"
    assert entry.awaiting_text and not entry.event.is_set()
    await bot._dispatch_clarify_interaction(event("other", kind=kind))
    bot.send.assert_awaited_once()  # stale/replayed clicks never post another hint
    cg.attempt_text_response_for_session(entry.session_key, "福建")
    assert entry.event.is_set() and entry.response == "福建"


@pytest.mark.asyncio
async def test_failed_manual_hint_keeps_waiter_and_foreign_click_posts_nothing():
    bot = adapter("group")
    entry = register(["北京", "上海"], kind="group")
    await send(bot, entry)
    bot.send = AsyncMock(side_effect=RuntimeError("send unavailable"))
    await bot._dispatch_clarify_interaction(event("other", kind="group", group_member_openid="stranger"))
    bot.send.assert_not_awaited()
    assert not entry.awaiting_text
    await bot._dispatch_clarify_interaction(event("other", kind="group"))
    assert entry.awaiting_text and not entry.event.is_set()
    cg.attempt_text_response_for_session(entry.session_key, "福建")
    assert entry.event.is_set() and entry.response == "福建"
