"""Core brain layer — Long-Term Memory & Personality Engine."""

import os
import json
import httpx
from dataclasses import dataclass
from typing import Any

from core.types import BrainResponse
from core.personality import get_system_prompt, DEFAULT_MOOD


@dataclass(slots=False)
class CoreBrain:
    companion_name: str
    current_llm_provider: str = "groq"
    current_llm_model: str = None
    long_term_memory: Any = None
    lore_book: Any = None   # NEW: exact, keyword-triggered memory — see sara/memory/lore.py
    affection_level: int = 50

    def respond(
        self,
        message: str,
        turn_count: int,
        chat_history: list[dict] = None,
        tts_prov: str = "elevenlabs",
        llm_provider: str = None,
        llm_model: str = None,
        mood_preset: str = DEFAULT_MOOD,
        trailed_off: bool = False,
        regen_direction: str = None,
        lore_enabled: bool = True,   # Can be toggled off per-session from the UI
        enabled_packs: set | None = None,  # Which lore packs are active; None = all
        ltm_override=None,  # Per-session LTM for per-lore memory isolation (overrides self.long_term_memory)
    ) -> BrainResponse:

        active_provider = llm_provider or self.current_llm_provider or "groq"
        active_model = llm_model or self.current_llm_model
        normalized = message.strip()
        lowered = normalized.lower()

        if lowered in {"exit", "quit"}:
            return BrainResponse(
                text=f"Okay, I'm here whenever you want to talk again. Take care of yourself.",
                should_exit=True,
                emotion="sad",
            )

        # Internal turns are the synthetic "[SYSTEM: ...]" prompts that
        # trigger continuations/proactive nudges (see app.py) — not real
        # user content. Skip lore retrieval AND lore extraction for these,
        # so the system's own instruction text can't accidentally trigger
        # keyword matches or get mined for fake "memory_worthy" facts.
        is_internal_trigger = normalized.startswith("[SYSTEM:")

        memory_context = ""
        active_ltm = ltm_override if ltm_override is not None else self.long_term_memory
        if active_ltm:
            past_memories = active_ltm.retrieve_context(normalized)
            if past_memories:
                memory_context = f"\nRelevant past memories with the user:\n{past_memories}"

        lore_character_context = ""
        lore_user_facts = ""
        if self.lore_book and lore_enabled and not is_internal_trigger:
            # Layer 1: Always inject ALL lore entries — SARA's identity/
            # character description must be present every turn so she
            # always knows who she is, not only when the user says a
            # matching keyword (e.g. she should know she's a street beggar
            # even when the user just says "I missed you").
            lore_character_context = self.lore_book.get_all_context(
                enabled_packs=enabled_packs
            )
            # Layer 2: Keyword-triggered — for user-specific facts SARA has
            # learned during past conversations (memory_worthy entries added
            # at runtime). These only surface when relevant keywords appear.
            lore_user_facts = self.lore_book.retrieve_triggered(
                normalized, enabled_packs=enabled_packs
            )

        system_prompt = get_system_prompt(
            self.companion_name,
            self.affection_level,
            memory_context,
            tts_prov,
            mood_preset,
            trailed_off,
            regen_direction,
            lore_character_context,
            lore_user_facts,
        )

        response_text = None
        emotion = "worried"
        continue_in = 0
        raw_delta = 0    # Will hold this turn's affection_delta for BrainResponse.

        MODEL_QUEUE = [
            {"provider": "groq",       "model": "llama-3.1-8b-instant",        "env_key": "GROQ_API_KEY"},
            {"provider": "gemini",     "model": "gemini-3.1-flash-lite",       "env_key": "GEMINI_API_KEY"},
            {"provider": "groq",       "model": "llama-3.3-70b-versatile",     "env_key": "GROQ_API_KEY"},
            {"provider": "openrouter", "model": "cognitivecomputations/dolphin-mistral-24b-venice-edition:free", "env_key": "OPENROUTER_KEY_1"},
            {"provider": "openrouter", "model": "nousresearch/hermes-3-llama-3.1-405b:free",                    "env_key": "OPENROUTER_KEY_2"},
        ]

        def _call_openrouter(target_model: str, env_key_name: str) -> str | None:
            api_key = os.getenv(env_key_name, "")
            base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
            if not api_key:
                return None
            try:
                with httpx.Client() as client:
                    headers = {
                        "Authorization": f"Bearer {api_key}",
                        "HTTP-Referer": "http://localhost:8000",
                        "X-Title": "SARA",
                        "Content-Type": "application/json",
                    }
                    payload_messages = [{"role": "system", "content": system_prompt}]
                    if chat_history:
                        payload_messages.extend(chat_history)
                    else:
                        payload_messages.append({"role": "user", "content": normalized})
                    payload = {"model": target_model, "messages": payload_messages, "max_tokens": 1024}
                    resp = client.post(f"{base_url}/chat/completions", headers=headers, json=payload, timeout=30.0)
                    if resp.status_code == 200:
                        return resp.json()["choices"][0]["message"]["content"]
                    print(f"OpenRouter ({target_model}) -> {resp.status_code}: {resp.text[:300]}")
            except Exception as e:
                print(f"OpenRouter ({target_model}) Error: {e}")
            return None

        def _call_groq(target_model: str) -> str | None:
            api_key = os.getenv("GROQ_API_KEY", "")
            if not api_key:
                return None
            try:
                with httpx.Client() as client:
                    headers = {
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    }
                    payload_messages = [{"role": "system", "content": system_prompt}]
                    if chat_history:
                        payload_messages.extend(chat_history)
                    else:
                        payload_messages.append({"role": "user", "content": normalized})
                    payload = {
                        "model": target_model,
                        "response_format": {"type": "json_object"},
                        "messages": payload_messages,
                        "max_tokens": 1024,
                    }
                    resp = client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers=headers, json=payload, timeout=30.0,
                    )
                    if resp.status_code == 200:
                        return resp.json()["choices"][0]["message"]["content"]
                    with open("groq_error.log", "a", encoding="utf-8") as f:
                        f.write(f"Groq -> {resp.status_code}: {resp.text}\n")
                    print(f"Groq ({target_model}) -> {resp.status_code}: {resp.text[:300]}")
            except Exception as e:
                with open("groq_error.log", "a", encoding="utf-8") as f:
                    f.write(f"Groq Error: {e}\n")
                print(f"Groq Error: {e}")
            return None

        def _call_gemini(target_model: str) -> str | None:
            api_key = os.getenv("GEMINI_API_KEY", "")
            if not api_key:
                return None
            contents = []
            if chat_history:
                for m in chat_history:
                    role = "user" if m["role"] == "user" else "model"
                    contents.append({"role": role, "parts": [{"text": m["content"]}]})
            else:
                contents.append({"role": "user", "parts": [{"text": normalized}]})
            try:
                with httpx.Client() as client:
                    resp = client.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent",
                        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                        json={
                            "contents": contents,
                            "systemInstruction": {"parts": [{"text": system_prompt}]},
                            "generationConfig": {
                                "responseMimeType": "application/json",
                                "maxOutputTokens": 1024,
                            },
                        },
                        timeout=20.0,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            if parts:
                                return parts[0].get("text")
                        return None
                    print(f"Gemini ({target_model}) -> {resp.status_code}: {resp.text[:300]}")
            except Exception as e:
                print(f"Gemini Error: {e}")
            return None

        current_selection = {"provider": active_provider, "model": active_model}
        if not current_selection["model"]:
            defaults = {
                "groq": "llama-3.1-8b-instant",
                "gemini": "gemini-3.1-flash-lite",
                "openrouter": "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
            }
            current_selection["model"] = defaults.get(current_selection["provider"], "llama-3.1-8b-instant")
        for m in MODEL_QUEUE:
            if m["model"] == current_selection["model"]:
                current_selection["env_key"] = m["env_key"]
                break
        current_selection.setdefault("env_key", "GROQ_API_KEY")

        execution_queue = [current_selection] + [
            m for m in MODEL_QUEUE if m["model"] != current_selection["model"]
        ]

        raw_response = None
        for step in execution_queue:
            print(f"Trying: {step['provider']} / {step['model']} ...")
            if step["provider"] == "groq":
                raw_response = _call_groq(step["model"])
            elif step["provider"] == "gemini":
                raw_response = _call_gemini(step["model"])
            else:
                raw_response = _call_openrouter(step["model"], step.get("env_key", "OPENROUTER_KEY_1"))

            if raw_response:
                active_provider = step["provider"]
                active_model = step["model"]
                print("OK - LLM call successful")
                break
            print("FAILED - trying next fallback...")

        if raw_response:
            try:
                clean_json = raw_response.replace("```json", "").replace("```", "").strip()
                parsed = json.loads(clean_json)

                if "affection_delta" in parsed:
                    try:
                        raw_delta = int(parsed["affection_delta"])
                        self.affection_level = max(0, min(100, self.affection_level + raw_delta))
                    except (ValueError, TypeError):
                        raw_delta = 0

                emotion = parsed.get("emotion", "neutral")
                response_text = parsed.get("response_text", "")
                try:
                    continue_in = int(parsed.get("continue_in_seconds", 0))
                except (ValueError, TypeError):
                    continue_in = 0

                print(f"[THOUGHT]  {parsed.get('internal_thought', '')}")
                print(f"[STATE]    emotion={emotion} | bond={self.affection_level} | mood={mood_preset}")

                mw = parsed.get("memory_worthy")
                if mw and isinstance(mw, dict) and self.lore_book and not is_internal_trigger:
                    try:
                        mw_content = mw.get("content", "")
                        mw_keywords = mw.get("keywords", [])
                        if mw_content and isinstance(mw_keywords, list) and mw_keywords:
                            # No category passed — LoreBook is itself the
                            # "lore" layer (see sara/memory/lore.py), kept
                            # fully separate from the identity/preference/
                            # ongoing_situation categories used by the
                            # ChromaDB fact-store in long_term.py. Don't
                            # conflate the two taxonomies.
                            self.lore_book.add_entry(mw_content, mw_keywords)
                    except (AttributeError, TypeError) as e:
                        print(f"LoreBook: malformed memory_worthy block, skipped: {e}")

            except json.JSONDecodeError:
                print(f"JSON parse error. Raw response:\n{raw_response}")
                response_text = raw_response
                continue_in = 0
                emotion = "neutral"

        if response_text is None:
            response_text = "I'm having a little trouble connecting right now. Give me a moment?"

        # NOTE: raw-transcript storage used to happen here
        # (self.long_term_memory.store_memory(...)). Removed — Section 1's
        # extraction-based rewrite moved storage to a fire-and-forget call
        # in app.py's process_and_stream() (_extract_and_store_memory),
        # which runs AFTER the live response is already sent, so it can
        # never add latency to a voice turn. brain.respond() only ever
        # READS from long_term_memory now (the retrieve_context call
        # above), it never writes to it directly anymore.

        return BrainResponse(
            text=response_text,
            emotion=emotion,
            affection_delta=raw_delta,
            continue_in_seconds=continue_in,
            should_exit=False,
            llm_provider=active_provider,
            llm_model=active_model,
        )