"""
Lore Book — exact, keyword-triggered long-term memory.

Complements LongTermMemory (ChromaDB, fuzzy semantic search) with a second,
deterministic layer: discrete facts, each tagged with explicit trigger
keywords. When a keyword appears in the user's message, the matching
entry's content is injected into context directly — no embedding math, no
"close enough" surprises. This is the same mechanism most AI companion
platforms call a "Lorebook" (SillyTavern, HammerAI, Clank World, etc.) —
it's the actual reason their recall feels precise instead of fuzzy: a
keyword either matched or it didn't, there's no probabilistic middle ground
where something half-related sneaks in.

This is deliberately the "exact, certain" layer sitting next to ChromaDB's
"fuzzy, associative" layer in brain.py — both get queried on every turn and
shown to the model as separate sections, since they serve different jobs.

Two failure modes are well-documented across these platforms, worth baking
in as constraints from day one rather than discovering them later:
  1. Writing entries like instructions ("Always respond by...") instead of
     like memories ("Rounak's dad is a CS teacher...") causes drift — the
     model treats commands as disposable guidance, not lived-in fact.
  2. Over-triggering: too many broad/overlapping keywords on one entry
     causes constant re-injection and token bloat. Keep keyword lists
     short and specific (enforced below via MAX_KEYWORDS_PER_ENTRY).

KNOWN LIMITATION — not thread-safe: write methods (add_entry,
retrieve_triggered) aren't lock-protected. brain.respond() runs inside
asyncio.to_thread, so concurrent sessions on different threads could in
theory race on the JSON file. Same deprioritized status as the shared
CoreBrain state — fine for solo use, needs a lock (or a real DB) before
any multi-user deployment.
"""

import json
import os
import time
import uuid
from dataclasses import dataclass, field

# Hard ceiling so unsupervised auto-extraction can't quietly grow this file
# forever. Once full, new entries are rejected (not silently evicting old
# ones) — every entry here was explicitly judged "important enough to
# remember forever" by the model at the time, so silently dropping one to
# make room for a new one is worse than just refusing and logging it.
MAX_ENTRIES = 150

# An entry's keywords must each be at least this long — blocks the model
# from accidentally creating a near-universal trigger (e.g. a keyword like
# "I" or "the") that would re-inject on nearly every message.
MIN_KEYWORD_LENGTH = 3
MAX_KEYWORDS_PER_ENTRY = 6


@dataclass
class LoreEntry:
    id: str
    content: str
    keywords: list[str]
    category: str = "general"
    created_at: float = field(default_factory=time.time)
    last_triggered_at: float | None = None
    trigger_count: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "keywords": self.keywords,
            "category": self.category,
            "created_at": self.created_at,
            "last_triggered_at": self.last_triggered_at,
            "trigger_count": self.trigger_count,
        }

    @staticmethod
    def from_dict(d: dict) -> "LoreEntry":
        return LoreEntry(
            id=d["id"],
            content=d["content"],
            keywords=d.get("keywords", []),
            category=d.get("category", "general"),
            created_at=d.get("created_at", time.time()),
            last_triggered_at=d.get("last_triggered_at"),
            trigger_count=d.get("trigger_count", 0),
        )


class LoreBook:
    def __init__(self, persist_path: str = "data/lore_book.json"):
        self.persist_path = persist_path
        self.entries: list[LoreEntry] = []
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.persist_path):
            try:
                with open(self.persist_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self.entries = [LoreEntry.from_dict(e) for e in raw]
            except (json.JSONDecodeError, KeyError, OSError) as e:
                print(f"LoreBook: failed to load {self.persist_path}, starting empty: {e}")
                self.entries = []
        else:
            os.makedirs(os.path.dirname(self.persist_path) or ".", exist_ok=True)

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.persist_path) or ".", exist_ok=True)
            with open(self.persist_path, "w", encoding="utf-8") as f:
                json.dump([e.to_dict() for e in self.entries], f, indent=2, ensure_ascii=False)
        except OSError as e:
            print(f"LoreBook: failed to save {self.persist_path}: {e}")

    def add_entry(
        self, content: str, keywords: list[str], category: str = "general", _skip_save: bool = False
    ) -> "LoreEntry | None":
        content = (content or "").strip()
        if not content:
            return None

        clean_keywords: list[str] = []
        for kw in keywords or []:
            kw = (kw or "").strip().lower()
            if len(kw) >= MIN_KEYWORD_LENGTH and kw not in clean_keywords:
                clean_keywords.append(kw)
        clean_keywords = clean_keywords[:MAX_KEYWORDS_PER_ENTRY]

        if not clean_keywords:
            print(f"LoreBook: rejected entry with no usable keywords: {content[:60]!r}")
            return None

        # Near-duplicate guard: if an existing entry already covers this
        # exact fact, refresh its keywords instead of growing the book with
        # copies (the model may re-surface the same fact across turns).
        for existing in self.entries:
            if existing.content.strip().lower() == content.lower():
                merged = list(dict.fromkeys(existing.keywords + clean_keywords))[:MAX_KEYWORDS_PER_ENTRY]
                existing.keywords = merged
                if not _skip_save:
                    self._save()
                return existing

        if len(self.entries) >= MAX_ENTRIES:
            print(f"LoreBook: at MAX_ENTRIES ({MAX_ENTRIES}), rejected new entry: {content[:60]!r}")
            return None

        entry = LoreEntry(id=str(uuid.uuid4()), content=content, keywords=clean_keywords, category=category)
        self.entries.append(entry)
        if not _skip_save:
            self._save()
        return entry

    def add_entries_bulk(self, entries: list[dict]) -> int:
        """
        Bulk-load pre-written entries without going through the live
        per-message conversation flow — used by the one-time lore-document
        ingestion script (see sara/scripts/seed_lore.py), not by anything
        in the live request path.

        Each entry: {"content": str, "keywords": list[str], "category": str (optional)}.
        Writes to disk exactly once at the end, instead of once per entry
        (add_entry's normal behavior) — matters here since a real ingestion
        run can produce dozens of entries in one call.

        Returns the number actually added (some may be rejected — no
        usable keywords, or MAX_ENTRIES reached partway through).
        """
        added = 0
        for e in entries:
            result = self.add_entry(
                e.get("content", ""), e.get("keywords", []), e.get("category", "general"), _skip_save=True
            )
            if result is not None:
                added += 1
        self._save()
        return added

    def retrieve_triggered(self, message: str, enabled_packs=None, limit: int = 5) -> str:
        """
        Exact keyword scan against the incoming message — deterministic,
        no embeddings. Returns a formatted block of matched entries'
        content (most-triggered-historically first, capped at `limit`),
        or "" if nothing matched.

        `enabled_packs` is accepted for interface compatibility with
        LorePackManager but is unused here — a single LoreBook has no
        pack concept. Pass-through is safe.
        """
        if not message or not self.entries:
            return ""

        lowered = message.lower()
        matched = [e for e in self.entries if any(kw in lowered for kw in e.keywords)]
        if not matched:
            return ""

        # Update in-memory stats only — NOT persisted on every retrieval.
        # This runs on every chat turn that has any match at all; writing
        # to disk that often is unnecessary I/O for stats that are only
        # used for ranking when more than `limit` entries match at once.
        now = time.time()
        for entry in matched:
            entry.last_triggered_at = now
            entry.trigger_count += 1

        matched.sort(key=lambda e: e.trigger_count, reverse=True)
        top = matched[:limit]
        return "\n".join(f"- {e.content}" for e in top)

    def get_all_context(self, enabled_packs=None) -> str:
        """
        Return ALL entries as a formatted block — no keyword filter.
        Used for always-present identity/character lore that should
        be in context every turn regardless of what the user says.
        `enabled_packs` accepted for interface compatibility.
        """
        if not self.entries:
            return ""
        return "\n".join(f"- {e.content}" for e in self.entries)


class LorePackManager:
    """
    Manages a directory of named LoreBook files — one .json file per
    "lore pack" — so different contexts (character backstory, user
    background, world-building, etc.) can be independently toggled
    per session from the UI.

    Backward compatibility: if the legacy data/lore_book.json exists and
    data/lore/sara_character.json does not, the file is automatically
    copied over on first startup so existing installs adopt the new
    multi-pack layout without any manual migration step.

    Exposes the same retrieve_triggered() interface as LoreBook so
    CoreBrain.respond() doesn't care which class it is talking to.
    """

    _LEGACY_PATH = "data/lore_book.json"

    def __init__(self, packs_dir: str = "data/lore"):
        self.packs_dir = packs_dir
        os.makedirs(packs_dir, exist_ok=True)
        self._packs: dict[str, LoreBook] = {}
        self._migrate_legacy()
        self._scan()

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _migrate_legacy(self) -> None:
        """
        One-time copy: data/lore_book.json → data/lore/sara_character.json.
        Preserves the original file so a rollback (if ever needed) is trivial.
        Silently skips if the destination already exists or the source is gone.
        """
        import shutil
        dest = os.path.join(self.packs_dir, "sara_character.json")
        if os.path.exists(self._LEGACY_PATH) and not os.path.exists(dest):
            try:
                shutil.copy2(self._LEGACY_PATH, dest)
                print(f"LorePackManager: migrated {self._LEGACY_PATH} -> {dest}")
            except OSError as exc:
                print(f"LorePackManager: migration failed: {exc}")

    def _scan(self) -> None:
        """
        Refresh the in-memory pack registry from disk.
        New .json files dropped into `packs_dir` are picked up on the next
        _scan() call (triggered by list_packs() on every /api/lore/packs
        request, so the UI always reflects the current directory contents).
        """
        try:
            fnames = [f for f in os.listdir(self.packs_dir)
                      if f.endswith(".json") and not f.endswith(".meta.json")]
        except OSError:
            return
        found: set[str] = set()
        for fname in fnames:
            name = fname[:-5]   # strip .json extension
            found.add(name)
            if name not in self._packs:
                self._packs[name] = LoreBook(
                    persist_path=os.path.join(self.packs_dir, fname)
                )
        # Drop references to deleted files
        for gone in set(self._packs) - found:
            del self._packs[gone]

    # ── Public API ────────────────────────────────────────────────────────────

    def list_packs(self) -> list[dict]:
        """
        Return [{name, entry_count, ...metadata}] for all known packs.
        Re-scans the directory each call so newly added files show up in the
        UI without a server restart. Metadata is read from .meta.json sibling
        files (e.g. sara_character.meta.json) if they exist.
        """
        self._scan()
        return [
            {"name": name, "entry_count": len(book.entries), **self.get_pack_metadata(name)}
            for name, book in sorted(self._packs.items())
        ]

    def get_pack_metadata(self, pack_name: str) -> dict:
        """
        Read the <pack_name>.meta.json file alongside the pack's .json file.
        Returns {} if the file doesn't exist or can't be parsed.
        Metadata fields: display_name, tagline, description, cover_emoji,
        accent_color, gradient_start, gradient_end, tags, first_message.
        """
        meta_path = os.path.join(self.packs_dir, f"{pack_name}.meta.json")
        if not os.path.exists(meta_path):
            return {}
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[LorePack] could not read {meta_path}: {e}")
            return {}


    def retrieve_triggered(
        self,
        message: str,
        enabled_packs: set | None = None,
        limit: int = 5,
    ) -> str:
        """
        Exact keyword scan across all enabled packs, merged and globally
        ranked by trigger_count.

        enabled_packs=None  → all packs active (first connect default).
        enabled_packs=set() → zero packs active (master lore toggle is off).
        enabled_packs={...} → only the named packs are queried.

        Total entries injected across all packs is capped at `limit`
        so a large multi-pack setup can't silently balloon the context.
        """
        if not message or not self._packs:
            return ""

        active_books = [
            book for name, book in self._packs.items()
            if enabled_packs is None or name in enabled_packs
        ]
        if not active_books:
            return ""

        lowered = message.lower()
        all_matched = [
            entry
            for book in active_books
            for entry in book.entries
            if any(kw in lowered for kw in entry.keywords)
        ]
        if not all_matched:
            return ""

        now = time.time()
        for entry in all_matched:
            entry.last_triggered_at = now
            entry.trigger_count += 1

        all_matched.sort(key=lambda e: e.trigger_count, reverse=True)
        top = all_matched[:limit]
        return "\n".join(f"- {e.content}" for e in top)

    def get_all_context(self, enabled_packs: set | None = None) -> str:
        """
        Return ALL entries from enabled packs as a formatted block —
        no keyword filter. Used to inject SARA's identity/character
        description on every turn so she always knows who she is,
        regardless of what keywords appear in the user's message.

        enabled_packs semantics same as retrieve_triggered.
        """
        self._scan()
        if not self._packs:
            return ""

        active_books = [
            book for name, book in self._packs.items()
            if enabled_packs is None or name in enabled_packs
        ]
        all_entries = [
            entry
            for book in active_books
            for entry in book.entries
        ]
        if not all_entries:
            return ""
        return "\n".join(f"- {e.content}" for e in all_entries)