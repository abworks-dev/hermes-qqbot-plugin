"""Visual group references travel from real ingress to real REST request bodies."""
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from hermes_qqbot import QQAdapter
from hermes_qqbot.references import group_reference_index


@pytest.mark.parametrize("event,expected", [
    ({"message_scene": {"ext": ["ref_msg_idx=parent", "auth_token=secret", "msg_idx=REFIDX_a=="]}}, "REFIDX_a=="),
    ({"message_scene": {"ext": ["ref_msg_idx=parent"]}}, None),
    ({"message_scene": {"ext": "msg_idx=wrong"}}, None),
    ({"message_scene": None}, None), ({}, None),
])
def test_extracts_only_current_message_index(event, expected):
    assert group_reference_index(event) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("markdown", [True, False])
async def test_ingress_to_first_chunk_quotes_original_and_keeps_all_passive_anchors(monkeypatch, markdown):
    adapter = QQAdapter(PlatformConfig(enabled=True, extra={
        "app_id": "test", "client_secret": "test", "markdown_support": markdown,
    }))
    monkeypatch.setattr(adapter, "_ensure_connected", AsyncMock(return_value=True))
    monkeypatch.setattr(adapter, "handle_message", AsyncMock())
    monkeypatch.setattr(adapter, "_process_attachments", AsyncMock(return_value={
        "voice_transcripts": [], "attachment_info": "", "image_urls": [], "image_media_types": [],
    }))
    monkeypatch.setattr(adapter, "_process_quoted_context", AsyncMock(return_value={"quote_block": "", "image_urls": []}))
    for group, message, reference in [("group-a", "msg-a", "REFIDX_a=="),
                                       ("group-a", "msg-b", "REFIDX_b=="),
                                       ("group-b", "msg-a", "REFIDX_other==")]:
        await adapter._ingest({"message_scene": {"ext": ["ref_msg_idx=parent", f"msg_idx={reference}"]}},
                              message, "question", [], datetime.now().astimezone().isoformat(),
                              chat_id=group, qq_chat_type="group")
    sent = []

    async def post(method, path, body):
        sent.append(body)
        return {"id": str(len(sent))}

    monkeypatch.setattr(adapter, "_api_request", post)
    result = await adapter._send_with_retry("group-a", "甲" * 8500, reply_to="msg-a")
    assert result.success
    assert len(sent) == 3
    assert sent[0]["message_reference"] == {"message_id": "REFIDX_a=="}
    assert all("message_reference" not in body for body in sent[1:])
    assert all(body["msg_id"] == "msg-a" for body in sent)
    assert len({body["msg_seq"] for body in sent}) == 3
    sent.clear()
    assert (await adapter.send("group-a", "no reference", reply_to="unknown")).success
    assert "message_reference" not in sent[0]
