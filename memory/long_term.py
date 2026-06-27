"""
Long-term memory -- extraction-based fact store (ChromaDB-backed).

Stores DISTILLED FACTS, not raw transcripts. A dedicated extraction LLM
call (extract_memories_sync(), fired fire-and-forget from app.py after
each exchange -- see process_and_stream()) pulls out only what's actually
worth remembering long-term: names, relationships, ongoing situations,
preferences -- written as short, clean, standalone factual sentences.
Retrieval blends semantic similarity with recency (same scoring approach
as before this rewrite), applied on top of this curated fact store instead
of raw exchange blobs.

Categories: identity, preference, ongoing_situation, milestone.
Kept deliberately separate from the LoreBook's "lore" layer (see
sara/memory/lore.py), which uses exact keyword triggers instead of
semantic search and is a fully independent system -- never conflate the
two during retrieval.

Collection renamed from the old "sara_conversations" (raw transcripts) to
"sara_facts" (distilled facts) -- deliberate clean break, not a migration.
Old raw-transcript memories won't carry over automatically. Given this
project is still being actively refined, a clean slate beats quietly
half-migrating differently-shaped data.
"""

import json
import os
import time
import uuid

import chromadb
import httpx

# Chroma's default index space is "l2" (squared Euclidean) over the
# default ONNX MiniLM embeddings, which are unit-normalized -- so
# l2_distance = 2 - 2*cosine_similarity, range roughly 0 (identical) to 4
# (opposite). 1.1 corresponds to ~cos_sim 0.45, a medium relevance bar.
# UNVERIFIED IN THIS SANDBOX -- couldn't reach Chroma's model-download
# host to test against real data here. Watch the "[MEMORY DEBUG]" line
# during real use and tune this if real memories get filtered out, or
# junk gets through.
MAX_RELEVANT_DISTANCE = 1.1

RECENCY_HALF_LIFE_DAYS = 14
SIMILARITY_WEIGHT = 0.7
RECENCY_WEIGHT = 0.3
IMPORTANCE_WEIGHT = 0.15  # small nudge -- a 5/5 fact edges out an otherwise-tied 2/5 one

VALID_CATEGORIES = {"identity", "preference", "ongoing_situation", "milestone"}

# ongoing_situation facts shouldn't surface forever once "resolved" -- a
# soft decay window on top of normal recency weighting. Other categories
# (identity, preference, milestone) don't decay this way; a name or a
# strong preference doesn't go stale just because it's old.
ONGOING_SITUATION_TTL_DAYS = 30


class LongTermMemory:
    def __init__(self, persist_directory: str = "data/chroma", collection_name: str = "sara_facts"):
        os.makedirs(persist_directory, exist_ok=True)
        self.client = chromadb.PersistentClient(path=persist_directory)
        self.collection = self.client.get_or_create_collection(name=collection_name)

    def store_fact(self, content: str, category: str = "identity", importance: int = 3) -> str | None:
        """
        Store one distilled fact. `content` should already be a short,
        standalone factual sentence (e.g. "Has a younger sister named
        Priya") -- extraction/distillation happens BEFORE this call, in
        extract_memories_sync(). This method does zero LLM work itself.
        """
        content = (content or "").strip()
        if not content:
            return None
        if category not in VALID_CATEGORIES:
            category = "identity"
        try:
            importance = max(1, min(5, int(importance)))
        except (ValueError, TypeError):
            importance = 3

        fact_id = str(uuid.uuid4())
        try:
            self.collection.add(
                documents=[content],
                metadatas=[{
                    "category": category,
                    "importance": importance,
                    "created_at": time.time(),
                    # 0.0 = never referenced yet. Updated each time this fact
                    # actually surfaces to the LLM during retrieve_context().
                    # Keeping it separate from created_at means we can tell the
                    # difference between "this fact is old" and "this fact is old
                    # but still keeps coming up as relevant" — the latter should
                    # not decay out of retrieval just because its birthday is old.
                    "last_referenced_at": 0.0,
                    "superseded_by": "",
                }],
                ids=[fact_id],
            )
            return fact_id
        except Exception as e:
            # A storage failure must NEVER break the live conversation.
            # Real-world repro of exactly this: my test sandbox's network
            # allowlist blocked Chroma's embedding-model download and this
            # call threw uncaught, which (before this fix) would have
            # crashed the entire response AFTER the LLM had already
            # generated a perfectly good reply -- the user would've gotten
            # nothing. Fail loud in logs, fail silent to the caller.
            print(f"LongTermMemory: failed to store fact ({e}): {content[:60]!r}")
            return None

    def supersede(self, old_fact_id: str, new_fact_id: str) -> None:
        """Mark an old fact as replaced by a newer one (e.g. 'has an interview' -> 'got the job')."""
        try:
            self.collection.update(ids=[old_fact_id], metadatas=[{"superseded_by": new_fact_id}])
        except Exception as e:
            print(f"LongTermMemory: failed to mark fact superseded: {e}")

    def retrieve_context(self, query: str, limit: int = 3) -> str:
        try:
            if self.collection.count() == 0:
                return ""

            fetch_n = min(max(limit * 3, 8), self.collection.count())
            results = self.collection.query(
                query_texts=[query],
                n_results=fetch_n,
                # "ids" must NOT be in include — ChromaDB 0.4+ always returns
                # ids automatically and raises a validation error if you request
                # them explicitly. They are still accessible via results["ids"][0].
                include=["documents", "metadatas", "distances"],
            )

            if not results["documents"] or len(results["documents"][0]) == 0:
                return ""

            documents = results["documents"][0]
            metadatas = results["metadatas"][0]
            distances = results["distances"][0]
            ids = results["ids"][0]

            print(f"[MEMORY DEBUG] query={query!r} raw_distances={[round(d, 3) for d in distances]}")

            now = time.time()
            scored = []
            for doc, meta, dist, fact_id in zip(documents, metadatas, distances, ids):
                if dist > MAX_RELEVANT_DISTANCE:
                    continue

                # Superseded facts are excluded outright -- the newer fact
                # already covers it. Surfacing both reads as a
                # contradiction ("has an interview" AND "got the job"
                # together), not an update.
                if meta.get("superseded_by"):
                    continue

                # Recency is based on whichever timestamp is more recent:
                # - created_at: when the fact was first stored
                # - last_referenced_at: when it was last surfaced to the LLM
                # Using the max of the two means a repeatedly-relevant old fact
                # doesn't decay away just because its creation date is old.
                # last_referenced_at of 0.0 means it has never been surfaced
                # yet, so fall back purely to created_at in that case.
                ts_created = meta.get("created_at") or 0.0
                ts_referenced = meta.get("last_referenced_at") or 0.0
                effective_ts = max(ts_created, ts_referenced) if ts_created else 0.0
                age_days = (now - effective_ts) / 86400 if effective_ts else RECENCY_HALF_LIFE_DAYS

                category = meta.get("category", "identity")
                if category == "ongoing_situation" and age_days > ONGOING_SITUATION_TTL_DAYS:
                    continue  # assume resolved/no-longer-current rather than keep surfacing forever

                recency_factor = 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)
                similarity = max(0.0, 1.0 - (dist / 2.0))
                importance_factor = meta.get("importance", 3) / 5.0

                blended_score = (
                    (SIMILARITY_WEIGHT * similarity)
                    + (RECENCY_WEIGHT * recency_factor)
                    + (IMPORTANCE_WEIGHT * importance_factor)
                )
                # Store the full meta alongside the score and id so we can
                # write back the updated last_referenced_at at the end without
                # needing a second round-trip to ChromaDB to re-fetch it.
                scored.append((blended_score, doc, fact_id, meta))

            if not scored:
                return ""

            scored.sort(key=lambda x: x[0], reverse=True)
            top_scored = scored[:limit]
            top_docs = [doc for _, doc, _, _ in top_scored]

            # Update last_referenced_at for every fact that made it into the
            # final surfaced set. We already have their full metadata in memory
            # from the query results, so construct the updated dict inline
            # (avoids a separate get() round-trip). ChromaDB's update() replaces
            # the entire metadata object, so we must pass all fields — not just
            # the one we're changing.
            referenced_now = time.time()
            for _, _, fact_id, existing_meta in top_scored:
                try:
                    updated_meta = {
                        "category":             existing_meta.get("category", "identity"),
                        "importance":           existing_meta.get("importance", 3),
                        "created_at":           existing_meta.get("created_at", referenced_now),
                        "last_referenced_at":   referenced_now,
                        "superseded_by":        existing_meta.get("superseded_by", ""),
                    }
                    self.collection.update(ids=[fact_id], metadatas=[updated_meta])
                except Exception as update_err:
                    # Non-critical — the fact surfaced correctly this turn;
                    # only the timestamp tracking is affected. Log and move on.
                    print(f"LongTermMemory: could not update last_referenced_at for {fact_id}: {update_err}")

            # Clean distilled bullets, NOT raw "User said X | Sara replied
            # Y" transcript pairs -- the actual fix from Section 1.C.
            return "\n".join(f"- {fact}" for fact in top_docs)
        except Exception as e:
            print(f"ChromaDB retrieval error: {e}")
            return ""


# ════════════════════════════════════════════════════════════════════════
# Extraction -- write-time distillation (Section 1.A)
# ════════════════════════════════════════════════════════════════════════
# Deliberately a free function, not a LongTermMemory method: it needs its
# own LLM call (Groq, fast + free), entirely separate from CoreBrain's main
# conversation call, and must run fire-and-forget so it can NEVER add
# latency to a live voice turn. app.py calls this via asyncio.to_thread()
# inside a asyncio.create_task() AFTER the user's response is already on
# its way -- see process_and_stream()'s _extract_and_store_memory().

_EXTRACTION_PROMPT = """You extract durable facts worth remembering long-term from a single conversation exchange.

Ignore small talk, filler, anything already obvious, and anything that isn't genuinely new information about the person.

Categories:
- identity: name, age, relationships, job, where they live
- preference: likes/dislikes, communication style, things they enjoy or avoid
- ongoing_situation: current life events -- exams, job search, health, a project, anything time-bound and evolving
- milestone: significant one-time events (got a job, finished a project, a birthday)

Respond ONLY with a JSON object: {"facts": [...]}. Each item in the array: {"fact": "short standalone sentence", "category": "one of the four above", "importance": 1-5}.
If nothing new or notable, respond with {"facts": []}."""


def extract_memories_sync(user_message: str, assistant_message: str) -> list[dict]:
    """
    Synchronous extraction call -- meant to be run inside
    asyncio.to_thread() by the fire-and-forget caller in app.py, same
    pattern already used for the main brain call. Uses Groq's 8B model:
    fast and free, this doesn't need to be smart, just needs to spot
    obvious durable facts.
    """
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        return []

    exchange = f"User said: {user_message}\nSara replied: {assistant_message}"
    try:
        with httpx.Client() as client:
            resp = client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": _EXTRACTION_PROMPT},
                        {"role": "user", "content": exchange},
                    ],
                    "max_tokens": 400,
                    "temperature": 0.0,
                },
                timeout=15.0,
            )
            if resp.status_code != 200:
                print(f"Memory extraction: Groq -> {resp.status_code}: {resp.text[:200]}")
                return []
            raw = resp.json()["choices"][0]["message"]["content"]
            clean = raw.replace("```json", "").replace("```", "").strip()
            parsed = json.loads(clean)
            facts = parsed.get("facts", []) if isinstance(parsed, dict) else []
            if not isinstance(facts, list):
                return []
            return [
                item for item in facts
                if isinstance(item, dict) and item.get("fact") and item.get("category") in VALID_CATEGORIES
            ]
    except (json.JSONDecodeError, KeyError, IndexError, httpx.HTTPError) as e:
        print(f"Memory extraction error: {e}")
        return []