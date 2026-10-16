"""Plugin-owned QQ REST sender, migrated from Hermes 6a1bad9bee."""
import logging
import os
import time
from tools.send_message_senders import (
    _IMAGE_EXTS, _VIDEO_EXTS, _AUDIO_EXTS, _VOICE_EXTS, _error, _success, _sanitize_error_text,
)
logger = logging.getLogger(__name__)

def _qqbot_media_file_type(media_path: str, is_voice: bool) -> int:
    """Map a local path / voice flag to QQ Bot ``file_type`` constants."""
    from .constants import (
        MEDIA_TYPE_FILE, MEDIA_TYPE_IMAGE, MEDIA_TYPE_VIDEO, MEDIA_TYPE_VOICE)

    ext = os.path.splitext(media_path)[1].lower()
    if is_voice or ext in _VOICE_EXTS:
        return MEDIA_TYPE_VOICE
    if ext in _IMAGE_EXTS:
        return MEDIA_TYPE_IMAGE
    if ext in _VIDEO_EXTS:
        return MEDIA_TYPE_VIDEO
    if ext in _AUDIO_EXTS:
        return MEDIA_TYPE_VOICE
    return MEDIA_TYPE_FILE


async def _qqbot_api_json(client, headers: dict, method: str, path: str,
                          body: dict | None = None, *, timeout: float = 30.0) -> dict:
    """Request QQ JSON; HTTP success does not imply business-level success."""
    from .constants import API_BASE
    from .api_errors import QQAPIError

    resp = await client.request(method, f"{API_BASE}{path}", json=body, headers=headers, timeout=timeout)
    if resp.status_code == 204:
        return {}
    try:
        data = resp.json() if resp.content else {}
    except Exception:
        data = {}
    data = data if isinstance(data, dict) else {}
    code = data.get("code")
    if resp.status_code >= 400 or code not in (None, 0, "0"):
        raise QQAPIError(resp.status_code, path, code, str(data.get("message", "request rejected")))
    return data


async def _qqbot_upload_local_file(client, headers, chat_type, chat_id, media_path, file_type) -> dict:
    """Chunked-upload a local file; returns the ``/files`` complete response."""
    from pathlib import Path as _Path
    from .chunked_upload import ChunkedUploader
    from .constants import FILE_UPLOAD_TIMEOUT

    local_path = _Path(media_path).expanduser()
    if not local_path.is_absolute():
        local_path = (_Path.cwd() / local_path).resolve()
    if not local_path.exists() or not local_path.is_file():
        raise FileNotFoundError(f"Media file not found: {local_path}")

    async def _api_request(method, path, body=None, timeout=FILE_UPLOAD_TIMEOUT):
        return await _qqbot_api_json(client, headers, method, path, body, timeout=timeout)

    uploader = ChunkedUploader(api_request=_api_request, http_put=client.put, log_tag="send_message.qqbot")
    return await uploader.upload(
        chat_type=chat_type, target_id=chat_id, file_path=str(local_path),
        file_type=file_type, file_name=local_path.name)


async def _qqbot_upload_url(client, headers, chat_type, chat_id, url, file_type,
                            file_name=None) -> dict:
    """Ask QQ to fetch ``url`` into the media store; returns upload JSON."""
    from .constants import FILE_UPLOAD_TIMEOUT, MEDIA_TYPE_FILE

    path = f"/v2/users/{chat_id}/files" if chat_type == "c2c" else f"/v2/groups/{chat_id}/files"
    body = {"file_type": file_type, "srv_send_msg": False, "url": url}
    if file_type == MEDIA_TYPE_FILE and file_name:
        body["file_name"] = file_name
    return await _qqbot_api_json(client, headers, "POST", path, body, timeout=FILE_UPLOAD_TIMEOUT)


def _qqbot_extract_file_info(upload: dict):
    if not isinstance(upload, dict):
        return None
    file_info = upload.get("file_info") or (upload.get("data") or {}).get("file_info")
    return file_info if file_info else None


async def _qqbot_send_media_message(client, headers, chat_type, chat_id, file_info,
                                    caption=None, force_document=False) -> dict:
    """POST a RichMedia message referencing an uploaded ``file_info``."""
    from .constants import MAX_MESSAGE_LENGTH, MSG_TYPE_MEDIA

    path = f"/v2/users/{chat_id}/messages" if chat_type == "c2c" else f"/v2/groups/{chat_id}/messages"
    body = {"msg_type": MSG_TYPE_MEDIA, "media": {"file_info": file_info},
            "msg_seq": int(time.time() * 1000) % 1_000_000_000}
    if caption and caption.strip():
        body["content"] = caption.strip()[:MAX_MESSAGE_LENGTH]
    data = await _qqbot_api_json(client, headers, "POST", path, body)
    if not isinstance(data.get("id"), str) or not data["id"].strip():
        raise RuntimeError("QQBot media acknowledgement missing a valid message ID")
    return data


async def _qqbot_send_text_message(client, headers, chat_id, message: str) -> dict:
    """Try channel → C2C → group text endpoints (pre-media standalone behavior)."""
    from .constants import API_BASE

    payload = {"content": (message or "")[:4000], "msg_type": 0}
    endpoints = (("channel", f"{API_BASE}/channels/{chat_id}/messages"),
                 ("c2c", f"{API_BASE}/v2/users/{chat_id}/messages"),
                 ("group", f"{API_BASE}/v2/groups/{chat_id}/messages"))
    statuses = []
    for kind, url in endpoints:
        resp = await client.post(url, json=payload, headers=headers)
        if 200 <= resp.status_code < 300:
            try:
                data = resp.json()
            except (ValueError, TypeError):
                data = None
            if isinstance(data, dict):
                code = data.get("code")
                if code not in (None, 0, "0"):
                    return _error(f"QQBot send failed: code={code}: {data.get('message', 'business refusal')}")
                message_id = data.get("id")
                if isinstance(message_id, str) and message_id.strip():
                    return _success("qqbot", chat_id, message_id=message_id)
            return {**_error("QQBot text acknowledgement missing a valid message ID; delivery outcome uncertain"),
                    "success": False, "delivery_unknown": True}
        if resp.status_code >= 500 or resp.status_code == 408:
            return _error(f"QQBot send failed: {kind}={resp.status_code}; delivery outcome uncertain")
        statuses.append(f"{kind}={resp.status_code}")
    return _error(f"QQBot send failed: {' '.join(statuses)}")


async def _qqbot_deliver_one_media(client, headers, chat_id, media_path, is_voice,
                                   caption=None, force_document=False) -> dict:
    """Upload one attachment and send it, trying C2C then group (no guild)."""
    from pathlib import Path as _Path
    from urllib.parse import urlparse

    from .constants import MEDIA_TYPE_FILE
    from .api_errors import QQAPIError
    file_type = MEDIA_TYPE_FILE if force_document else _qqbot_media_file_type(media_path, is_voice)
    is_url = urlparse(str(media_path)).scheme in {"http", "https"}
    errors: list[str] = []

    for chat_type in ("c2c", "group"):
        try:
            if is_url:
                upload = await _qqbot_upload_url(
                    client, headers, chat_type, chat_id, media_path, file_type,
                    file_name=_Path(urlparse(media_path).path).name or "media")
            else:
                upload = await _qqbot_upload_local_file(
                    client, headers, chat_type, chat_id, media_path, file_type)
            file_info = _qqbot_extract_file_info(upload)
            if not file_info:
                errors.append(f"{chat_type}: upload returned no file_info: {upload}")
                continue
            send_data = await _qqbot_send_media_message(
                client, headers, chat_type, chat_id, file_info, caption=caption)
            return _success("qqbot", chat_id, message_id=send_data.get("id"), chat_type=chat_type)
        except Exception as exc:
            if not isinstance(exc, QQAPIError) or not exc.definitely_rejected:
                return {**_error("QQBot media delivery outcome uncertain; no retry or text fallback was attempted"),
                        "success": False, "delivery_unknown": True}
            errors.append(f"{chat_type}: {exc}")
            continue

    return _error(
        "QQBot media send failed (guild/channel native media is unsupported; "
        f"tried c2c and group): {'; '.join(errors)}")


async def _send_qqbot(pconfig, chat_id, message, media_files=None, caption=None, force_document=False):
    """Send via the QQ Bot Open Platform REST API (no WebSocket needed).

    Guild channels, C2C (private) chats and groups are tried in order for text.
    When ``media_files`` is provided, each attachment is uploaded (C2C then
    group — guild has no native media upload path) and sent as a
    ``msg_type=MEDIA`` RichMedia message (#37315). Optional ``caption`` rides on
    the first media bubble; non-empty ``message`` is sent as a separate text
    message before the media.
    """
    try:
        import httpx
    except ImportError:
        return _error("QQBot direct send requires httpx. Run: hermes pm repair")

    # Profile-scoped lookup so a multiplex profile never borrows another's QQ credentials.
    from gateway.config import _getenv
    from .constants import FILE_UPLOAD_TIMEOUT, TOKEN_URL
    extra = pconfig.extra or {}
    appid = extra.get("app_id") or _getenv("QQ_APP_ID", "")
    secret = pconfig.token or extra.get("client_secret") or _getenv("QQ_CLIENT_SECRET", "")
    if not appid or not secret:
        return _error("QQBot: QQ_APP_ID / QQ_CLIENT_SECRET not configured.")

    media_files = media_files or []
    # Longer timeout when uploading; text-only stays snappy.
    client_timeout = FILE_UPLOAD_TIMEOUT if media_files else 15.0

    try:
        async with httpx.AsyncClient(timeout=client_timeout) as client:
            token_resp = await client.post(TOKEN_URL,
                                           json={"appId": str(appid), "clientSecret": str(secret)})
            if token_resp.status_code != 200:
                return _error(f"QQBot token request failed: {token_resp.status_code}")
            access_token = token_resp.json().get("access_token")
            if not access_token:
                return _error("QQBot: no access_token in response")

            headers = {"Authorization": f"QQBot {access_token}", "Content-Type": "application/json"}

            # --- Media path (#37315) ---
            if media_files:
                last_result = None
                text = (message or "").strip()
                warnings: list[str] = []
                if text and not (caption and caption.strip()):
                    text_result = await _qqbot_send_text_message(client, headers, chat_id, text)
                    if isinstance(text_result, dict) and text_result.get("error"):
                        return text_result
                    last_result = text_result

                for index, (media_path, is_voice) in enumerate(media_files):
                    if not media_path:
                        warnings.append("QQBot: empty media attachment")
                        continue
                    # Caption applies to the first bubble only (single-file
                    # caption split already enforced by the caller).
                    media_caption = caption if index == 0 else None
                    if not os.path.exists(media_path):
                        from urllib.parse import urlparse as _urlparse
                        if _urlparse(str(media_path)).scheme not in {"http", "https"}:
                            warnings.append(f"QQBot media file not found, skipping: {media_path}")
                            logger.warning(warnings[-1])
                            continue
                    result = await _qqbot_deliver_one_media(
                        client, headers, chat_id, media_path, bool(is_voice), caption=media_caption,
                        force_document=force_document)
                    if isinstance(result, dict) and result.get("delivery_unknown"):
                        # The caption may already have landed with this media POST. Stop
                        # the entire batch, including any plaintext-caption fallback.
                        return {**result, "success": False,
                                "partial_success": bool(last_result and last_result.get("success")),
                                **({"warnings": [_sanitize_error_text(w) for w in warnings]} if warnings else {})}
                    if isinstance(result, dict) and result.get("error"):
                        # Media failure must not discard the send (main degraded to text +
                        # omission warning): collect it and keep delivering.
                        warnings.append(result["error"])
                        logger.warning("QQBot media delivery failed, omitting attachment: %s",
                                       result["error"])
                        continue
                    last_result = result

                if last_result is None:
                    # Nothing was deliverable (caption rides the media, which all failed):
                    # degrade to the text alone, as the pre-media generic path did.
                    fallback_text = text or (caption or "").strip()
                    if not fallback_text:
                        return {"error": "QQBot: no deliverable media attachments",
                                **({"warnings": warnings} if warnings else {})}
                    text_result = await _qqbot_send_text_message(client, headers, chat_id, fallback_text)
                    if isinstance(text_result, dict) and text_result.get("error"):
                        return {**text_result, **({"warnings": warnings} if warnings else {})}
                    last_result = text_result
                if warnings and isinstance(last_result, dict):
                    last_result["warnings"] = [_sanitize_error_text(w) for w in [*last_result.get("warnings", []), *warnings]]
                    last_result["partial_success"] = bool(last_result.get("success"))
                    last_result["success"] = False
                    last_result["error"] = "QQBot: requested media delivery incomplete"
                return last_result

            # --- Text-only path: first 2xx wins (pre-media behavior) ---
            return await _qqbot_send_text_message(client, headers, chat_id, message or "")
    except Exception as e:
        return _error(f"QQBot send failed: {e}")


async def standalone_sender(pconfig, chat_id, message, *, thread_id=None, media_files=None,
                            force_document=False, caption=None):
    return await _send_qqbot(pconfig, chat_id, message, media_files=media_files, caption=caption,
                             force_document=force_document)
