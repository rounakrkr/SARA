"""
Session memory for SARA.

Each visible turn carries a stable UUID (`id`) so the frontend can
reference exact messages regardless of how many internal/system turns the
backend injects in between (continuation triggers, proactive nudges).
Position-based indexing was the old approach and broke the moment any
internal turn got recorded — frontend and backend arrays silently drifted
out of sync, and delete/regenerate could silently act on the wrong message.

`internal=True` marks turns that are backend-only bookkeeping (the system
prompts that trigger continuations/proactive check-ins) — these never get
an id and the frontend never learns they exist. The assistant *replies* to
internal triggers ARE real, visible messages and DO get ids, since they're
broadcast to and shown in the chat.
"""

import uuid
from dataclasses import dataclass, field


@dataclass(slots=True)
class SessionMemory:
    history: list[dict] = field(default_factory=list)
    # each entry: {"id": str|None, "role": "user"|"assistant", "content": str, "internal": bool}

    # ── Write ────────────────────────────────────────────────────────────

    def record_user_message(
        self, message: str, internal: bool = False, explicit_id: str | None = None
    ) -> str | None:
        """
        explicit_id: the frontend generates the id for user messages (it
        creates the chat bubble immediately, before the backend is even
        involved) and sends it along with the message — we just trust and
        store it, rather than generating our own and having to round-trip
        it back. internal=True entries never get an id at all.
        """
        msg_id = None if internal else (explicit_id or str(uuid.uuid4()))
        self.history.append({"id": msg_id, "role": "user", "content": message, "internal": internal})
        return msg_id

    def record_assistant_message(self, message: str) -> str:
        """
        Assistant replies are always real/visible (frontend creates the
        bubble only once it receives this broadcast), so the backend
        generates the id here and sends it along in that same broadcast.
        """
        msg_id = str(uuid.uuid4())
        self.history.append({"id": msg_id, "role": "assistant", "content": message, "internal": False})
        return msg_id

    # ── Delete from point (by id, not position) ────────────────────────

    def truncate_at_id(self, target_id: str) -> bool:
        """Remove the entry with this id and everything after it. False if id not found (no-op, e.g. already deleted)."""
        for i, entry in enumerate(self.history):
            if entry.get("id") == target_id:
                self.history = self.history[:i]
                return True
        return False

    # ── Regenerate ──────────────────────────────────────────────────────

    def pop_last_visible_user_message(self) -> str | None:
        """
        Find and remove the most recent *visible* (non-internal) user turn,
        searching backward from the end. Used right after truncate_at_id()
        during regenerate, so the popped content can be re-run through the
        brain without permanently rewriting what the user actually said.

        Edge case: if the message being regenerated was itself a reply to
        an internal trigger (a continuation/proactive turn, not a real user
        message), this walks further back to the real user message that
        originally started that chain, leaving the internal entry dangling
        in place. Rare in practice — fine to leave as-is.
        """
        for i in range(len(self.history) - 1, -1, -1):
            if self.history[i]["role"] == "user" and not self.history[i]["internal"]:
                return self.history.pop(i)["content"]
        return None

    # ── LLM context ─────────────────────────────────────────────────────

    def get_llm_history(self, n: int = 10) -> list[dict]:
        """Clean role/content pairs for the API call — strips id/internal metadata the LLM doesn't need."""
        return [{"role": e["role"], "content": e["content"]} for e in self.history[-n:]]

    # ── Utility ─────────────────────────────────────────────────────────

    @property
    def turn_count(self) -> int:
        return sum(1 for e in self.history if e["role"] == "user" and not e["internal"])

    def clear(self) -> None:
        self.history.clear()