"""
One-time lore-document ingestion script.

Takes a block of pre-written lore (a markdown file, a plain text file,
whatever format — this is intentionally format-agnostic since it works at
the LLM-extraction level, not by parsing structure) and converts it into
proper keyword-tagged LoreEntry objects in the LoreBook.

This is NOT part of the live app — run it once (or again whenever you
update your lore document) from the command line, from the project root:

    python -m sara.scripts.seed_lore path/to/your_lore.md

Add --dry-run first to see what would be extracted without saving anything:

    python -m sara.scripts.seed_lore path/to/your_lore.md --dry-run

Uses Groq's 70B model rather than the live conversation path's 8B model —
this only runs once (or occasionally) per document, so extraction quality
matters more than latency here, unlike extract_memories_sync() in
long_term.py which runs live on every conversation turn.
"""

import argparse
import json
import os
import sys

import httpx

# Allow running as `python -m sara.scripts.seed_lore` from the project root
# without needing the package pre-installed.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from memory.lore import LoreBook

# Format-agnostic chunking: split on blank-line paragraph breaks, then
# regroup into chunks under this size so each LLM call stays well within
# context limits regardless of how the source document is structured
# (markdown headers, plain prose, bullet lists — doesn't matter, this
# never tries to parse the structure itself).
MAX_CHUNK_CHARS = 3000

_INGEST_PROMPT = """You are converting a piece of someone's personal background writing into discrete, standalone memory entries for an AI companion that will recall them later in conversation.

For EACH distinct fact, relationship, preference, event, or detail worth remembering — extract it as its own entry. Write the content as a short, standalone factual sentence in third person (e.g. "His childhood dog was named Bruno" — NOT "I had a dog named Bruno", and NOT an instruction like "Always mention that..."). Then give 2-4 specific keywords that should bring this fact back up in a later conversation — specific nouns, names, topics, not generic words like "the" or "is".

Skip anything that's pure scene-setting, mood, or prose flourish with no actual factual content. One paragraph of source material can produce zero, one, or several entries — extract what's actually there, don't force a fixed count.

Respond ONLY with a JSON object: {"entries": [{"content": "...", "keywords": ["...", "..."]}, ...]}. If this chunk has nothing extractable, respond with {"entries": []}."""


def chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""
    for p in paragraphs:
        if current and len(current) + len(p) + 2 > max_chars:
            chunks.append(current)
            current = p
        else:
            current = f"{current}\n\n{p}" if current else p
    if current:
        chunks.append(current)
    return chunks


def extract_entries_from_chunk(chunk: str, api_key: str) -> list[dict]:
    try:
        with httpx.Client() as client:
            resp = client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": _INGEST_PROMPT},
                        {"role": "user", "content": chunk},
                    ],
                    "max_tokens": 1500,
                    "temperature": 0.1,
                },
                timeout=60.0,
            )
            if resp.status_code != 200:
                print(f"  Groq error {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
                return []
            raw = resp.json()["choices"][0]["message"]["content"]
            clean = raw.replace("```json", "").replace("```", "").strip()
            parsed = json.loads(clean)
            entries = parsed.get("entries", []) if isinstance(parsed, dict) else []
            return [e for e in entries if isinstance(e, dict) and e.get("content") and e.get("keywords")]
    except (json.JSONDecodeError, KeyError, IndexError, httpx.HTTPError) as e:
        print(f"  Extraction error on chunk: {e}", file=sys.stderr)
        return []


def main():
    parser = argparse.ArgumentParser(description="Seed the LoreBook from a pre-written lore document.")
    parser.add_argument("file", help="Path to the lore document (any plain-text format — .md, .txt, etc.)")
    parser.add_argument(
        "--persist-path", default="data/lore_book.json",
        help="LoreBook storage path (default: data/lore_book.json — same file the live app reads from)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Print extracted entries without saving them")
    args = parser.parse_args()

    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        print("ERROR: GROQ_API_KEY not set in environment.", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.file):
        print(f"ERROR: file not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    with open(args.file, "r", encoding="utf-8") as f:
        text = f.read()

    chunks = chunk_text(text)
    print(f"Document split into {len(chunks)} chunk(s). Extracting...")

    all_entries: list[dict] = []
    for i, chunk in enumerate(chunks, 1):
        print(f"  [{i}/{len(chunks)}] processing ({len(chunk)} chars)...")
        entries = extract_entries_from_chunk(chunk, api_key)
        print(f"    -> {len(entries)} entries extracted")
        all_entries.extend(entries)

    print(f"\nTotal extracted: {len(all_entries)} entries")
    for e in all_entries:
        print(f"  - {e['content']}  [keywords: {', '.join(e['keywords'])}]")

    if args.dry_run:
        print("\n--dry-run set, nothing saved.")
        return

    lore_book = LoreBook(persist_path=args.persist_path)
    added = lore_book.add_entries_bulk(all_entries)
    print(
        f"\nSaved {added}/{len(all_entries)} entries to {args.persist_path} "
        f"(some may have been merged into existing duplicates, or rejected for missing keywords)."
    )


if __name__ == "__main__":
    main()