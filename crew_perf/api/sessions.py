"""Conversation registry for the web UI.

In-process and deliberately so. A conversation is worth exactly as long as the
tab is open: it holds no decision, nothing is scored from it, and every number
in it can be recomputed from the warehouse. Persisting it would mean a store to
run, a retention question to answer about crew performance data, and stale
scores surviving a weight refit — all to save re-asking a question.

Bounded on both axes, because neither bound alone is enough: without a count
cap a busy afternoon of one-question visitors grows the dict forever, and
without an age cap a quiet one keeps yesterday's threads addressable.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from crew_perf.agents.conversation import Conversation

MAX_SESSIONS = 200
MAX_AGE_SECONDS = 4 * 60 * 60


@dataclass
class _Entry:
    conversation: Conversation
    touched: float = field(default_factory=time.time)


class SessionStore:
    def __init__(self, max_sessions: int = MAX_SESSIONS, max_age: float = MAX_AGE_SECONDS):
        self._entries: dict[str, _Entry] = {}
        self.max_sessions = max_sessions
        self.max_age = max_age

    def get(self, session_id: str | None) -> Conversation:
        """The conversation for this id, creating one when the id is new or gone.

        An unknown id yields a fresh conversation under *that same id* rather
        than a new one. The client has already put the id in its URL and its next
        request will reuse it; minting a different one would strand the thread the
        user is looking at after a single eviction.
        """
        self._expire()
        entry = self._entries.get(session_id or "")
        if entry is not None:
            entry.touched = time.time()
            return entry.conversation

        sid = session_id or uuid.uuid4().hex[:16]
        conversation = Conversation(id=sid)
        self._entries[sid] = _Entry(conversation)
        self._evict()
        return conversation

    def drop(self, session_id: str) -> bool:
        return self._entries.pop(session_id, None) is not None

    def _expire(self) -> None:
        cutoff = time.time() - self.max_age
        for sid in [s for s, e in self._entries.items() if e.touched < cutoff]:
            del self._entries[sid]

    def _evict(self) -> None:
        while len(self._entries) > self.max_sessions:
            oldest = min(self._entries, key=lambda s: self._entries[s].touched)
            del self._entries[oldest]

    def __len__(self) -> int:
        return len(self._entries)
