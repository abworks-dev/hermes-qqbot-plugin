"""QQ clarify cards over the shared gateway waiter, never a second answer queue."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import httpx

from gateway.platforms.base import BasePlatformAdapter, SendResult
from .api_errors import QQAPIError
from .keyboards import (
    InlineKeyboard, KeyboardContent, KeyboardRow, KeyboardButton,
    KeyboardButtonAction, KeyboardButtonPermission, KeyboardButtonRenderData,
)

logger = logging.getLogger(__name__)
_PREFIX = "clarify:"


@dataclass
class _Card:
    entry: object
    operator: str
    chat_type: str
    chat_id: str
    reply_to: str | None = None
    selected: set[int] = field(default_factory=set)


_RECOMMENDED = re.compile(r"\s*[(（]\s*(?:recommended|推荐)\s*[)）]\s*$", re.IGNORECASE)


def _choice_display(choice: str, *, button: bool = False) -> str:
    """Presentation only: original waiter choices remain untouched."""
    text = choice.strip()
    if _RECOMMENDED.search(text):
        text = _RECOMMENDED.sub("" if button else "（推荐）", text)
    return text


def _keyboard(clarify_id: str, choices: list[str], operator: str, multi: bool) -> InlineKeyboard:
    """Two options per row; recommendation notes belong in the full body only."""
    specs = [(str(i), f"{i}. {_choice_display(choice, button=True)}")
             for i, choice in enumerate(choices, 1)]
    option_count = len(specs)
    if multi:
        specs.append(("submit", "确认选择"))
    specs.extend([("other", "手动输入"), ("skip", "跳过")])
    buttons = [KeyboardButton(
        id=f"{clarify_id}_{action}",
        render_data=KeyboardButtonRenderData(
            label=label if len(label) <= 10 else label[:9] + "…",
            visited_label=("✓ " + label[:8]) if action.isdigit() else label),
        action=KeyboardButtonAction(
            type=1, data=f"{_PREFIX}{clarify_id}:{action}",
            permission=KeyboardButtonPermission(type=0, specify_user_ids=[operator]),
            unsupport_tips="请回复消息中的选项编号，或直接输入答案。"),
    ) for action, label in specs]
    rows = [KeyboardRow(buttons=buttons[i:min(i + 2, option_count)])
            for i in range(0, option_count, 2)]
    if multi:
        rows.append(KeyboardRow(buttons=[buttons[option_count]]))
    rows.append(KeyboardRow(buttons=buttons[-2:]))
    return InlineKeyboard(content=KeyboardContent(rows=rows))


class QQClarifyMixin:
    """Render native choice buttons; identity and answers stay with clarify_gateway."""

    def _clarify_cards(self):
        cards = getattr(self, "_qq_clarify_cards", None)
        if cards is None:
            cards = self._qq_clarify_cards = {}
        # Waiters are retired by timeout/reset/text too. Do not retain stale cards.
        from tools import clarify_gateway as cg
        with cg._lock:
            for cid, card in list(cards.items()):
                if cg._entries.get(cid) is not card.entry or card.entry.event.is_set():
                    cards.pop(cid, None)
        return cards

    def _clarify_waiting_for_chat(self, chat_id, metadata=None):
        """Only this adapter's live prompt identities authorize suppression."""
        from tools import clarify_gateway as cg
        session_key = (metadata or {}).get("session_key")
        waits = getattr(self, "_qq_clarify_waits", {})
        with cg._lock:
            for cid, (entry, chat) in list(waits.items()):
                if cg._entries.get(cid) is not entry or entry.event.is_set():
                    waits.pop(cid, None)
            return any(chat == chat_id and (not session_key or entry.session_key == session_key)
                       for entry, chat in waits.values())

    def _suppress_clarify_heartbeat(self, chat_id, content, metadata=None):
        md = metadata or {}
        if md.get("_interim_send") is not True or md.get("is_approval_prompt"):
            return False
        if not self._clarify_waiting_for_chat(chat_id, md):
            return False
        from agent.i18n import t
        # Match only the host's localized, timed working-heartbeat shape. Generic
        # status phrases have no reliable provenance and must remain visible.
        minute, detail = "QQMINUTESMARKER", "QQDETAILMARKER"
        template = t("gateway.progress.working_heartbeat", minutes=minute, detail=detail)
        if template.count(minute) != 1 or template.count(detail) != 1:
            return False
        pattern = re.escape(template).replace(minute, r"[0-9]+").replace(detail, r"(?: — [^\r\n]+)?")
        return re.fullmatch(pattern, content) is not None

    async def _clarify_text(self, chat_id, question, choices, clarify_id, session_key, metadata):
        return await BasePlatformAdapter.send_clarify(
            self, chat_id, question, choices, clarify_id, session_key, metadata=metadata)

    async def send_clarify(self, chat_id, question, choices, clarify_id, session_key, metadata=None) -> SendResult:
        from tools import clarify_gateway as cg
        # A decision prompt is not the turn-final and must not reconcile/seal a native stream.
        metadata = {**(metadata or {}), "_interim_send": True}
        metadata.setdefault("reply_to_message_id", self._last_msg_id.get(chat_id))
        cards = self._clarify_cards()
        with cg._lock:
            entry = cg._entries.get(clarify_id)
            valid = entry is not None and entry.session_key == session_key and not entry.event.is_set()
        if not valid:
            return SendResult(success=False, retryable=False, error="Clarify request is no longer pending")
        if not hasattr(self, "_qq_clarify_waits"):
            self._qq_clarify_waits = {}
        self._qq_clarify_waits[clarify_id] = (entry, chat_id)
        if not await self._native_prompt_boundary(chat_id, metadata=metadata):
            return SendResult(success=False, retryable=False,
                              error="QQ prompt stream boundary is ambiguous",
                              raw_response={"ambiguous": True})
        parsed = self._parse_gateway_session_key(session_key) or {}
        kind = self._guess_chat_type(chat_id)
        operator = parsed.get("user_id") if kind == "group" else parsed.get("chat_id")
        multi = entry.multi_select
        text = self._clarify_body(question, choices, multi, kind)
        supported = self._clarify_supported(choices, entry, kind, parsed, chat_id, operator, multi, text)
        if not choices:
            return await self._clarify_text(chat_id, question, choices, clarify_id, session_key, metadata)
        if not supported:
            # The runner owns the ONE numbered-text retry (also for late native rejections).
            return SendResult(success=False, retryable=False, error="QQ clarify keyboard unavailable for this prompt")
        if not await self._ensure_connected():
            return self._NOT_CONNECTED
        cards[clarify_id] = _Card(entry, operator, kind, chat_id,
                                  reply_to=metadata.get("reply_to_message_id"))
        # QQ custom keyboards accompany a Markdown message, regardless of ordinary text settings.
        body = {"msg_type": 2, "markdown": {"content": text},
                "keyboard": _keyboard(clarify_id, choices, operator, multi).to_dict(),
                "msg_seq": self._next_msg_seq(metadata.get("reply_to_message_id") or "default")}
        reply_to = metadata.get("reply_to_message_id")
        if reply_to:
            body["msg_id"] = reply_to
        try:
            return await self._post_message(self._messages_path(kind, chat_id), body)
        except Exception as exc:
            rejected = ((isinstance(exc, QQAPIError) and exc.definitely_rejected)
                        or isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)))
            logger.warning("QQ clarify keyboard send failed (%s): %s",
                           "rejected" if rejected else "possibly delivered", exc)
            if rejected:
                cards.pop(clarify_id, None)
                return SendResult(success=False, retryable=False, error=str(exc))
            # Lost ACK / 5xx can have landed. Runner keeps the waiter but MUST NOT send another copy.
            return SendResult(success=False, retryable=False, error=str(exc), raw_response={"ambiguous": True})

    @staticmethod
    def _clarify_body(question, choices, multi, kind):
        question_text = "\n".join(line.strip() for line in question.splitlines() if line.strip())
        text = "\n".join([f"❓ {question_text}", *[
            f"{i}. {_choice_display(choice)}" for i, choice in enumerate(choices or [], 1)],
            ("多选：依次点选，再点“确认选择”；也可回复编号（如 1,3）。" if multi else
             "点击按钮或回复选项编号；也可点击“手动输入”。"),
             ("手动输入时，请 @机器人 后输入答案。" if kind == "group" else
              "手动输入时，请直接发送答案。")])
        return text

    def _clarify_supported(self, choices, entry, kind, parsed, chat_id, operator, multi, text):
        return (choices and list(choices) == entry.choices and kind in {"c2c", "group"}
                     and parsed.get("platform") == "qqbot" and parsed.get("chat_id") == chat_id
                     and parsed.get("chat_type") in ({"dm", "c2c"} if kind == "c2c" else {"group"})
                     and operator and (len(choices) + 1) // 2 + (2 if multi else 1) <= 5
                     and len(text) <= self.MAX_MESSAGE_LENGTH)

    async def _dispatch_clarify_interaction(self, event) -> bool:
        """Consume only our payload namespace, including stale/foreign clicks (fail closed)."""
        if not event.button_data.startswith(_PREFIX):
            return False
        from tools import clarify_gateway as cg
        parts = event.button_data.split(":")
        if len(parts) != 3:
            return True
        _, cid, action = parts
        cards = self._clarify_cards()
        card = cards.get(cid)
        if card is None:
            return True
        chat = event.group_openid if card.chat_type == "group" else event.user_openid
        if not self._clarify_operator_matches(event, card, chat):
            logger.warning("Rejected QQ clarify click from a different conversation/operator")
            return True
        # Hold the same lock as text resolution/reset; callbacks cannot resurrect a retired waiter.
        manual_input = False
        with cg._lock:
            entry = cg._entries.get(cid)
            if entry is not card.entry or entry.event.is_set():
                cards.pop(cid, None)
                return True
            if action == "other":
                cg.mark_awaiting_text(cid)
                cards.pop(cid, None)
                manual_input = True
            elif action == "skip":
                cg.resolve_gateway_clarify(cid, cg.SKIPPED)
                cards.pop(cid, None)
            elif action == "submit" and entry.multi_select and card.selected:
                cg.resolve_gateway_clarify(cid, json.dumps(
                    [entry.choices[i] for i in sorted(card.selected)], ensure_ascii=False))
                cards.pop(cid, None)
            elif action.isascii() and action.isdigit() and 1 <= int(action) <= len(entry.choices):
                index = int(action) - 1
                if entry.multi_select:
                    # Idempotent: QQ may redeliver clicks. Typed numbering can replace the selection.
                    card.selected.add(index)
                else:
                    cg.resolve_gateway_clarify(cid, entry.choices[index])
                    cards.pop(cid, None)
        if manual_input:
            await self._send_manual_input_hint(card)
        return True

    @staticmethod
    def _clarify_operator_matches(event, card, chat):
        return (event.scene == card.chat_type and chat == card.chat_id
                and event.operator_openid == card.operator)

    async def _send_manual_input_hint(self, card: _Card) -> None:
        """One non-final hint, outside the waiter lock; never resolve or replace it."""
        text = ("已切换为手动输入，请 @机器人 后输入答案，例如：@机器人 福建。"
                if card.chat_type == "group" else
                "已切换为手动输入，请直接发送答案，例如：福建。")
        try:
            result = await self.send(card.chat_id, text, reply_to=card.reply_to,
                                     metadata={"_interim_send": True,
                                               "reply_to_message_id": card.reply_to})
            if not result.success:
                logger.warning("QQ manual-input hint was not confirmed; waiter remains pending")
        except Exception:
            logger.warning("QQ manual-input hint failed; waiter remains pending", exc_info=False)

    async def retire_clarify_card(self, clarify_id, notice):
        # QQ cannot edit a posted card. Invalidating its waiter makes every old button inert.
        self._clarify_cards().pop(clarify_id, None)

