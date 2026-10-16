"""QQ passive reply limits, keyed by destination and authority (not msg_seq).

Authority: https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/overview.html
Active sends are permitted by QQ subject to permissions/user preference and server
frequency limits. An expired passive reply must NEVER silently become active.
"""
from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass

PASSIVE_REPLY_LIMITS = {"c2c": 4, "group": 5}
PASSIVE_REPLY_TTL = {"c2c": 3600, "group": 300, "guild": 300, "dm": 300}


@dataclass
class _Authority:
    received_at: float
    used: int = 0


class QQMessageRules:
    """Bounded per-adapter ledger; QQ remains authoritative across restarts."""

    def __init__(self):
        self._authorities = OrderedDict()

    def remember(self, chat_type, chat_id, msg_id, received_at=None):
        key = (chat_type, chat_id, msg_id)
        self._authorities.setdefault(key, _Authority(time.time() if received_at is None else received_at))
        self._authorities.move_to_end(key)
        while len(self._authorities) > 4096:
            self._authorities.popitem(last=False)

    def remaining(self, chat_type, chat_id, msg_id):
        cap = PASSIVE_REPLY_LIMITS.get(chat_type)
        state = self._authorities.get((chat_type, chat_id, msg_id))
        if state and time.time() - state.received_at >= PASSIVE_REPLY_TTL[chat_type]:
            return 0
        return max(0, cap - (state.used if state else 0)) if cap is not None else None

    def reserve(self, chat_type, chat_id, msg_id):
        """Reserve before network await, including ambiguous deliveries. Return key."""
        remaining = self.remaining(chat_type, chat_id, msg_id)
        if remaining == 0:
            raise RuntimeError("QQ passive reply expired or reply quota exhausted; not converted to active send")
        self.remember(chat_type, chat_id, msg_id)
        key = (chat_type, chat_id, msg_id)
        self._authorities[key].used += 1
        return key

    def refund(self, key):
        if key in self._authorities:
            self._authorities[key].used = max(0, self._authorities[key].used - 1)

