"""Standalone send behavior with fake transports only."""
import asyncio
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from hermes_qqbot import senders
from hermes_qqbot.api_errors import QQAPIError


def test_media_upload_uses_plugin_uploader_and_file_type(monkeypatch, tmp_path):
    from hermes_qqbot import chunked_upload
    file = tmp_path / "photo.png"
    file.write_bytes(b"photo")
    uploaded = {}

    class Uploader:
        def __init__(self, **kwargs):
            uploaded.update(kwargs)

        async def upload(self, **kwargs):
            uploaded.update(kwargs)
            return {"file_info": "native"}

    monkeypatch.setattr(chunked_upload, "ChunkedUploader", Uploader)
    client = SimpleNamespace(put=AsyncMock())
    result = asyncio.run(senders._qqbot_upload_local_file(client, {}, "c2c", "123", str(file), 1))
    assert result == {"file_info": "native"}
    assert uploaded["chat_type"] == "c2c"
    assert uploaded["file_type"] == 1
    assert uploaded["http_put"] is client.put


def test_uncertain_media_send_does_not_try_another_target_kind(monkeypatch):
    upload = AsyncMock(return_value={"file_info": "native"})
    send = AsyncMock(side_effect=TimeoutError("delivery unknown"))
    monkeypatch.setattr(senders, "_qqbot_upload_url", upload)
    monkeypatch.setattr(senders, "_qqbot_send_media_message", send)
    result = asyncio.run(senders._qqbot_deliver_one_media(None, {}, "123", "https://example.test/a.png", False))
    assert "uncertain" in result["error"]
    assert upload.await_count == 1
    assert send.await_count == 1


def test_definite_rejection_may_try_group_without_reporting_fake_success(monkeypatch):
    upload = AsyncMock(side_effect=[QQAPIError(404, "/files"), {"file_info": "native"}])
    send = AsyncMock(return_value={"id": "delivered"})
    monkeypatch.setattr(senders, "_qqbot_upload_url", upload)
    monkeypatch.setattr(senders, "_qqbot_send_media_message", send)
    result = asyncio.run(senders._qqbot_deliver_one_media(None, {}, "123", "https://example.test/a.png", False))
    assert result["success"] is True
    assert result["chat_type"] == "group"
    assert upload.await_count == 2
    assert send.await_count == 1


def test_text_business_refusal_is_not_success_or_retry():
    client = SimpleNamespace(post=AsyncMock(return_value=SimpleNamespace(
        status_code=200, json=lambda: {"code": 40001, "message": "denied"})))
    result = asyncio.run(senders._qqbot_send_text_message(client, {}, "123", "hi"))
    assert "error" in result
    assert client.post.await_count == 1


def test_text_server_uncertainty_does_not_retry():
    client = SimpleNamespace(post=AsyncMock(return_value=SimpleNamespace(status_code=502)))
    result = asyncio.run(senders._qqbot_send_text_message(client, {}, "123", "hi"))
    assert "uncertain" in result["error"]
    assert client.post.await_count == 1


def test_missing_media_reports_partial_failure_after_text_send(monkeypatch, tmp_path):
    import httpx

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return SimpleNamespace(status_code=200, json=lambda: {"access_token": "private"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: Client())
    text = AsyncMock(return_value={"success": True, "message_id": "text"})
    monkeypatch.setattr(senders, "_qqbot_send_text_message", text)
    config = SimpleNamespace(token="secret", extra={"app_id": "app"})
    result = asyncio.run(senders._send_qqbot(config, "123", "hi", media_files=[(str(tmp_path / "missing.png"), False)]))
    assert result["success"] is False
    assert result["partial_success"] is True
    assert "incomplete" in result["error"]
    text.assert_awaited_once()


@pytest.mark.parametrize("failure", ["timeout", "server_error", "empty", "malformed", "list", "no_content"])
def test_caption_media_unknown_aborts_entire_send_without_plaintext_fallback(monkeypatch, failure):
    import httpx
    from hermes_qqbot.constants import TOKEN_URL
    requests = []

    def respond(request):
        import json
        body = json.loads(request.content)
        requests.append((str(request.url), body))
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "private"})
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json={"file_info": "uploaded"})
        assert request.url.path.endswith("/messages")
        if failure == "timeout":
            raise httpx.ReadTimeout("media acknowledgement lost", request=request)
        if failure == "server_error":
            return httpx.Response(503, json={"message": "unknown delivery"})
        if failure == "malformed":
            return httpx.Response(200, content=b"not-json")
        if failure == "no_content":
            return httpx.Response(204)
        return httpx.Response(200, json=[] if failure == "list" else {})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(
        transport=httpx.MockTransport(respond), **kwargs))
    config = SimpleNamespace(token="secret", extra={"app_id": "app"})
    result = asyncio.run(senders._send_qqbot(config, "123", "", caption="caption may have landed",
        media_files=[("https://example.test/a.png", False), ("https://example.test/b.png", False)]))
    assert result["delivery_unknown"] is True
    assert result["success"] is False
    assert result["partial_success"] is False
    messages = [(url, body) for url, body in requests if url.endswith("/messages")]
    assert len(messages) == 1
    assert messages[0][1]["media"] == {"file_info": "uploaded"}
    assert messages[0][1]["content"] == "caption may have landed"
    assert len([url for url, body in requests if url.endswith("/files")]) == 1


@pytest.mark.parametrize("ack", ["empty", "malformed", "list", "no_content"])
def test_text_invalid_ack_is_unknown_and_never_retried(monkeypatch, ack):
    import httpx
    from hermes_qqbot.constants import TOKEN_URL
    requests = []

    def respond(request):
        requests.append(str(request.url))
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "private"})
        if ack == "malformed":
            return httpx.Response(200, content=b"not-json")
        if ack == "no_content":
            return httpx.Response(204)
        return httpx.Response(200, json=[] if ack == "list" else {})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(
        transport=httpx.MockTransport(respond), **kwargs))
    config = SimpleNamespace(token="secret", extra={"app_id": "app"})
    result = asyncio.run(senders._send_qqbot(config, "123", "hello"))
    assert result["delivery_unknown"] is True
    assert result["success"] is False
    assert "message_id" not in result
    assert len([url for url in requests if url.endswith("/messages")]) == 1
