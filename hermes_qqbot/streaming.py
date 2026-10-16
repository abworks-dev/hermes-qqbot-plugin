"""QQ C2C native streams: immutable snapshots and prefix-safe final fallback.

Protocol: https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/
v2_users_user_openid_stream_messages.post.html

The HTTP helper preserves rejection evidence; legacy helpers expose HTTP status
only. Unproven acceptance failures remain unknown, including missing receipts.
Unknown acceptance MUST NOT become a retry or a second copy via normal send().
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

import httpx

from collections import OrderedDict
from dataclasses import dataclass, field
from urllib.parse import quote

from gateway.platforms.base import SendResult

logger = logging.getLogger(__name__)


@dataclass
class _Stream:
    seq: int
    created_at: float = field(default_factory=lambda: time.time())
    head: str = ""
    # An explicit consumer boundary declares subsequent finals segment-scoped.
    # head remains immutable delivery evidence, not part of the next answer.
    segment_final: bool = False
    reservation: tuple | None = None
    text: str = ""
    message_id: str | None = None
    index: int = 0
    failed: bool = False
    unknown: bool = False
    finalized: bool = False
    close_attempted: bool = False
    abandoned: bool = False
    reconciled: str | None = None
    result: SendResult | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class QQNativeStreamingMixin:
    """Mixin before BasePlatformAdapter; normal send calls reconciliation first.

    State belongs to the adapter instance (therefore its credential/profile), not
    module globals. Receipts outlive passive authority; capacity refuses bursts
    rather than evicting live evidence. reset_native_streams() is shutdown-only.
    """

    SUPPORTS_NATIVE_STREAMING = True
    NATIVE_STREAM_CONTENT_FIRST = True
    NATIVE_STREAM_PREFIX_STABLE = True
    NATIVE_STREAM_FINAL_ACK_REQUIRED = True
    NATIVE_STREAM_BUFFER_FALLBACK = True
    SUPPORTS_NATIVE_TOOL_PROGRESS = False
    _NATIVE_STREAM_LIMIT = 4096
    _NATIVE_RECEIPT_TTL = 3600

    def _native_states(self):
        if not hasattr(self, "_qq_native_streams"):
            self._qq_native_streams = OrderedDict()
        # Passive authority expires before its receipt. Preserve the original
        # timestamp in the rules ledger so pruning cannot revive old msg_ids.
        now = time.time()
        for key, state in list(self._qq_native_streams.items()):
            if now - state.created_at > self._NATIVE_RECEIPT_TTL and not state.lock.locked():
                self._message_rules.remember("c2c", key[0], key[1], state.created_at)
                if self._message_rules.remaining("c2c", key[0], key[1]) == 0:
                    del self._qq_native_streams[key]
        return self._qq_native_streams

    def reset_native_streams(self) -> None:
        """Release receipts only when no turn can still issue fallback sends."""
        self._native_states().clear()

    @staticmethod
    def _native_progressive_host() -> bool:
        # Older consumers decorate snapshots with cursors/closing fences and
        # optimistically mark failed seals delivered. Never put those drafts on
        # wire. Semantic boundary support is the conservative modern-host gate.
        from gateway.stream_consumer import GatewayStreamConsumer
        return callable(getattr(GatewayStreamConsumer, "close_for_approval_prompt_sync", None))

    def supports_native_streaming(self, *, chat_type=None, metadata=None) -> bool:
        if not self._native_progressive_host():
            if not getattr(self, "_qq_legacy_stream_warning", False):
                logger.warning("QQ native streaming disabled on legacy host; using buffered final delivery")
                self._qq_legacy_stream_warning = True
            return False
        return self._native_route_enabled(chat_type=chat_type, metadata=metadata)

    def _native_route_enabled(self, *, chat_type=None, metadata=None) -> bool:
        extra = self.config.extra or {}
        enabled = extra.get("native_streaming", True)
        if enabled is False or str(enabled).lower() in {"false", "0", "off", "no"}:
            return False
        md = metadata or {}
        # Generic dm includes guild DMs. Only QQ's original route is authoritative.
        source = md.get("source")
        source_md = getattr(source, "metadata", None) or {}
        qq_type = md.get("qq_chat_type") or source_md.get("qq_chat_type")
        if isinstance(source, dict):
            qq_type = qq_type or source.get("qq_chat_type")
        if qq_type:
            return qq_type == "c2c"
        if chat_type == "c2c":
            return True
        chat_id = md.get("chat_id") or getattr(source, "chat_id", None)
        if chat_id:
            return self._guess_chat_type(str(chat_id)) == "c2c"
        # Older consumers probe before they possess source metadata. This is
        # eligibility only: send_stream_frame validates the actual route again.
        return chat_type in {None, "dm"}

    @staticmethod
    def _native_partial(state, error):
        return SendResult(
            success=False, message_id=state.message_id, retryable=False, error=error,
            raw_response={"partial_overflow": True, "qq_send_terminal": True,
                          "native_stream": True, "delivered_prefix": state.head + state.text,
                          "delivery_unknown": state.unknown, "stream_closed": state.finalized})

    async def send_stream_frame(self, text: str, *, finalize: bool = False,
                                chat_id: str, reply_to: str | None = None,
                                turn_id: str | None = None) -> str | bool:
        """Accept cumulative text, returning a real acknowledged stream id or False.

        Consumer checks truthiness only; it does not adopt this id as editable.
        Empty seeds initialize local state only, without a remote receipt.
        """
        if not turn_id or not reply_to:
            return False
        if not self._native_route_enabled(
                metadata={"chat_id": chat_id, "qq_chat_type": self._guess_chat_type(chat_id)}):
            return False
        states = self._native_states()
        key = (chat_id, reply_to, turn_id)
        state = states.get(key)
        if state is None:
            if len(states) >= self._NATIVE_STREAM_LIMIT:
                return False
            state = _Stream(seq=0)
            states[key] = state
        async with state.lock:
            prepared = self._native_prepare_segment(state, text, finalize)
            if prepared is not None:
                return prepared
            if self._message_rules.remaining("c2c", chat_id, reply_to) == 0 and state.reservation is None:
                state.failed = True
                return False
            if not text.startswith(state.text):
                logger.warning("QQ native frame prefix mismatch: turn=%s index=%d final=%s "
                               "visible_chars=%d frame_chars=%d first_diff=%d",
                               turn_id, state.index, finalize, len(state.text), len(text),
                               self._native_first_diff(state.text, text))
                if finalize:
                    await self._close_known_native(state, chat_id, reply_to)
                state.failed = True
                return False
            if state.index and text == state.text and not finalize:
                return state.message_id
            return await self._native_post_frame(state, text, finalize, chat_id, reply_to, turn_id)

    def _native_prepare_segment(self, state, text, finalize):
        if state.failed or state.abandoned or state.unknown:
            return False
        # Legacy consumers eagerly seed with an empty frame. Initialize only:
        # no HTTP request, passive reservation, seq, or fabricated receipt.
        if not text and not finalize and not state.finalized:
            return True
        if finalize and not state.message_id and not text:
            return False
        if not finalize and not self._native_progressive_host():
            # Local acceptance is not a remote receipt. Final text, cleaned by
            # the consumer, is the only frame a legacy host may put on wire.
            return True
        if state.finalized:
            if text or finalize:
                return state.message_id if text == state.text else False
            # An explicit empty seed after a confirmed seal starts the next
            # boundary segment, retaining every prior immutable prefix.
            state.head += state.text
            state.text = ""
            state.message_id = None
            state.index = 0
            state.finalized = False
            state.close_attempted = False
            state.reservation = None
            return True
        return None

    async def _native_post_frame(self, state, text, finalize, chat_id, reply_to, turn_id):
        if not await self._ensure_connected():
            state.failed = True
            return False
        if state.reservation is None:
            try:
                state.reservation = self._message_rules.reserve("c2c", chat_id, reply_to)
            except RuntimeError:
                state.failed = True
                return False
            state.seq = self._next_msg_seq(reply_to)
        body = {"input_mode": "replace", "input_state": 10 if finalize else 1,
                "index": state.index,
                "content_type": "markdown" if self._markdown_support else "text",
                "content_raw": text, "msg_id": reply_to, "msg_seq": state.seq}
        if state.message_id:
            body["stream_msg_id"] = state.message_id
        # Mark BEFORE await: cancellation or a lost response may have landed.
        state.unknown = True
        started = time.monotonic()
        try:
            data = await self._api_request(
                "POST", f"/v2/users/{quote(chat_id, safe='')}/stream_messages", body)
        except asyncio.CancelledError:
            state.failed = True
            raise
        except Exception as exc:
            state.failed = True
            # No replay even for a definite rejection; final reconciliation
            # may send only the suffix beyond previously acknowledged text.
            rejected = self._native_definitely_rejected(exc)
            state.unknown = not rejected
            logger.warning(
                "QQ native frame refused: turn=%s index=%d final=%s chars=%d "
                "error_type=%s status=%s code=%s acceptance_unknown=%s",
                turn_id, state.index, finalize, len(text), type(exc).__name__,
                getattr(exc, "status", None), getattr(exc, "code", None), state.unknown)
            if not state.unknown and state.index == 0 and state.reservation is not None:
                self._message_rules.refund(state.reservation)
                state.reservation = None
            return False
        if not isinstance(data, dict) or not data.get("id"):
            logger.warning("QQ native receipt missing id: turn=%s index=%d final=%s chars=%d",
                           turn_id, state.index, finalize, len(text))
            state.failed = True
            return False
        if state.message_id is None:
            state.message_id = str(data["id"])
        state.text = text
        state.index += 1
        state.unknown = False
        state.finalized = finalize
        log = logger.info if finalize else logger.debug
        log("QQ native frame acknowledged: turn=%s index=%d final=%s chars=%d elapsed_ms=%d",
            turn_id, state.index - 1, finalize, len(text),
            int((time.monotonic() - started) * 1000))
        return state.message_id

    @staticmethod
    def _native_definitely_rejected(exc):
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
            return True
        rejected = getattr(exc, "definitely_rejected", None)
        if rejected is not None:
            return rejected
        status = re.search(r"QQ Bot API error \[(\d+)\]", str(exc))
        return bool(status and 400 <= int(status[1]) < 500 and int(status[1]) != 408)

    @staticmethod
    def _native_first_diff(left: str, right: str) -> int:
        return next((i for i, (a, b) in enumerate(zip(left, right)) if a != b),
                    min(len(left), len(right)))

    async def _close_known_native(self, state, chat_id, reply_to) -> bool:
        """Seal only the last ACKed snapshot. Closing is not final-content delivery.

        Caller holds state.lock. One attempt per segment; an unknown receipt must
        never be retried or used to authorize a second copy of the answer.
        """
        if state.finalized:
            return True
        if state.unknown or state.close_attempted or not state.message_id:
            return False
        state.close_attempted = True
        body = {"input_mode": "replace", "input_state": 10, "index": state.index,
                "content_type": "markdown" if self._markdown_support else "text",
                "content_raw": state.text, "msg_id": reply_to, "msg_seq": state.seq,
                "stream_msg_id": state.message_id}
        started = time.monotonic()
        state.unknown = True
        try:
            data = await self._api_request(
                "POST", f"/v2/users/{quote(chat_id, safe='')}/stream_messages", body)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            rejected = self._native_definitely_rejected(exc)
            state.unknown = not rejected
            logger.warning("QQ native known-snapshot close refused: index=%d error_type=%s "
                           "status=%s code=%s acceptance_unknown=%s elapsed_ms=%d",
                           state.index, type(exc).__name__, getattr(exc, "status", None),
                           getattr(exc, "code", None), state.unknown,
                           int((time.monotonic() - started) * 1000))
            return False
        if not isinstance(data, dict) or not data.get("id"):
            logger.warning("QQ native known-snapshot close missing receipt: index=%d", state.index)
            return False
        state.index += 1
        state.unknown = False
        state.finalized = True
        logger.info("QQ native known-snapshot closed: index=%d chars=%d elapsed_ms=%d "
                    "final_content_confirmed=False", state.index - 1, len(state.text),
                    int((time.monotonic() - started) * 1000))
        return True

    async def _reconcile_native_send(self, chat_id: str, content: str,
                                     reply_to: str | None = None, metadata=None):
        """None means normal send; otherwise this hook owns the entire send.

        _interim_send/expect_edits are never intercepted. Explicit turn_id is
        preferred; without it only a unique chat+reply receipt is safe to use.
        A consumer-declared boundary makes ordinary finals segment-scoped, just
        like its internal sends. Explicit _native_stream_segment=False selects a
        whole-turn ledger; never infer scope by matching text against prefixes.
        Never return a partial suffix to a caller that could replay the full text.
        """
        md = metadata or {}
        if md.get("_interim_send") or md.get("expect_edits") or md.get("is_approval_prompt"):
            return None
        reply_to = reply_to or md.get("reply_to_message_id")
        if not reply_to:
            return None
        turn_id = md.get("turn_id")
        matches = [s for (chat, reply, turn), s in self._native_states().items()
                   if chat == chat_id and reply == reply_to and (not turn_id or turn == turn_id)]
        if not matches:
            return None
        if len(matches) != 1:
            return self._native_partial(matches[-1], "Ambiguous QQ native turn; refusing duplicate send")
        state = matches[0]
        async with state.lock:
            if state.abandoned:
                return self._native_partial(state, "QQ native turn was abandoned")
            segment = md.get("_native_stream_segment", state.segment_final) is True
            canonical = state.head + content if segment else content
            if state.result is not None:
                if canonical == state.reconciled:
                    return state.result
                return self._native_partial(state, "QQ final changed after reconciliation")
            result = await self._native_reconcile_locked(
                state, chat_id, content, reply_to, segment=segment)
            state.reconciled, state.result = canonical, result
            return result

    async def _native_reconcile_locked(self, state, chat_id, content, reply_to, *, segment=False):
        if not state.finalized and state.message_id and not state.unknown:
            await self._close_known_native(state, chat_id, reply_to)
        if state.unknown:
            return self._native_partial(state, "QQ native acceptance unknown; refusing replay")
        prefix = state.text if segment else state.head + state.text
        if not content.startswith(prefix):
            logger.warning("QQ native final prefix mismatch: segment=%s head_chars=%d "
                           "visible_chars=%d final_chars=%d",
                           segment, len(state.head), len(state.text), len(content))
            return self._native_partial(state, "QQ native visible prefix cannot be changed")
        suffix = content[len(prefix):]
        if not suffix:
            if state.finalized:
                return SendResult(success=True, message_id=state.message_id,
                                  raw_response={"native_stream": True, "stream_closed": True})
            return self._native_partial(state, "QQ native final was not acknowledged")
        # No formatter: changing an already emitted markdown prefix is not safe.
        # Chunk only the residual, with the same passive-reply authority.
        # Raw slices, not truncate_message(): that helper adds display chunk
        # indicators which are not source text and corrupt prefix accounting.
        chunks = [suffix[i:i + self.MAX_MESSAGE_LENGTH]
                  for i in range(0, len(suffix), self.MAX_MESSAGE_LENGTH)]
        remaining = self._message_rules.remaining("c2c", chat_id, reply_to)
        if remaining is not None and len(chunks) > remaining:
            return self._native_partial(state, "QQ native residual exceeds passive reply quota")
        last = None
        for chunk in chunks:
            state.unknown = True
            try:
                last = await self._send_chunk(chat_id, chunk, reply_to)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return self._native_partial(state, f"QQ residual acceptance unknown: {exc}")
            if not last.success:
                return self._native_partial(state, last.error or "QQ residual send failed")
            state.text += chunk
            state.unknown = False
        logger.info("QQ native residual delivered: stream_closed=%s final_content_confirmed=True "
                    "residual_chars=%d", state.finalized, len(suffix))
        return SendResult(success=True, message_id=last.message_id,
                          raw_response={"native_stream": True, "native_residual": True,
                                        "stream_closed": state.finalized})

    def native_stream_boundary(self, chat_id: str, *, reply_to: str | None, turn_id: str) -> None:
        """Consumer confirms a delivered boundary before resetting its segment.

        Approval's buffered next segment has no seed to announce the transition;
        an explicit boundary signal avoids mistaking it for a rewritten final.
        """
        state = self._native_states().get((chat_id, reply_to, turn_id))
        if state is None or state.lock.locked() or state.unknown or state.abandoned:
            return
        if not state.finalized and not (state.result and state.result.success):
            return
        state.head += state.text
        state.segment_final = True
        state.text = ""
        state.message_id = None
        state.index = 0
        state.reservation = None
        # Boundary already archived the sealed segment. The next actual content
        # can start directly; do not require an unsupported empty opening frame.
        state.finalized = False
        state.close_attempted = False
        state.failed = False
        state.reconciled = None
        state.result = None

    async def _native_prompt_boundary(self, chat_id, reply_to=None, metadata=None):
        """Old-host prompt seam; new-host archived segments are already empty.

        No selection by recency: concurrent turns sharing passive authority are
        ambiguous and cannot safely be sealed on another prompt's behalf.
        """
        md = metadata or {}
        reply_to = reply_to or md.get("reply_to_message_id")
        turn_id = md.get("turn_id")
        matches = [(key, state) for key, state in self._native_states().items()
                   if key[0] == chat_id and key[1] == reply_to
                   and (not turn_id or key[2] == turn_id)]
        if not matches:
            return True
        if len(matches) != 1:
            return False
        key, state = matches[0]
        async with state.lock:
            if state.unknown or state.abandoned:
                return False
            if not state.text and not state.message_id:
                return True
            if not await self._close_known_native(state, chat_id, reply_to):
                return False
        self.native_stream_boundary(chat_id, reply_to=reply_to, turn_id=key[2])
        return True

    async def abandon_native_stream(self, chat_id: str, content: str = "", *, metadata=None):
        """Best-effort close ONLY known streams; never retry ambiguous frames."""
        md = metadata or {}
        turn = md.get("turn_id")
        reply = md.get("reply_to_message_id")
        if not turn:
            return
        state = self._native_states().get((chat_id, reply, turn))
        if state is None:
            return
        if not state.failed and not state.finalized and not state.unknown:
            await self.send_stream_frame(state.text, finalize=True, chat_id=chat_id,
                                         reply_to=reply, turn_id=turn)
        async with state.lock:
            state.abandoned = True

