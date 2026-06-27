import asyncio
import base64
import json
import os
import re
import time
import urllib.parse
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Dict, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, File, UploadFile, HTTPException, Query, Header
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import httpx
from dotenv import load_dotenv
import edge_tts

from core.pipeline import InteractionPipeline
from core.brain import CoreBrain
from core.personality import DEFAULT_MOOD
from memory.session import SessionMemory
from memory.long_term import LongTermMemory, extract_memories_sync
from memory.lore import LoreBook, LorePackManager

load_dotenv()


# ════════════════════════════════════════════════════════════════════════
# Sentence splitting for streaming TTS
# ════════════════════════════════════════════════════════════════════════
_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?।])\s+')


def split_into_sentences(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    parts = _SENTENCE_SPLIT_RE.split(text)
    merged: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if merged and len(p) < 3:
            merged[-1] = merged[-1] + " " + p
        else:
            merged.append(p)
    return merged if merged else [text]


def _map_model_to_ui_value(provider: str, model: str) -> str:
    model = model or ""
    if provider == "groq":
        return "groq-fast" if "8b" in model else "groq"
    if provider == "gemini":
        return "gemini"
    if provider == "openrouter":
        if "dolphin" in model:
            return "dolphin"
        if "hermes" in model:
            return "hermes"
    return "groq-fast"


# ════════════════════════════════════════════════════════════════════════
# App state
# ════════════════════════════════════════════════════════════════════════

class SessionStore:
    def __init__(self, max_sessions=100, ttl_seconds=3600):
        self._store: OrderedDict[str, tuple[InteractionPipeline, float]] = OrderedDict()
        self.max_sessions = max_sessions
        self.ttl = ttl_seconds

    def get(self, session_id: str, brain: CoreBrain) -> InteractionPipeline:
        now = time.time()
        dead = [k for k, (_, ts) in self._store.items() if now - ts > self.ttl]
        for k in dead:
            del self._store[k]

        if session_id in self._store:
            pipeline, _ = self._store[session_id]
            self._store[session_id] = (pipeline, now)
            self._store.move_to_end(session_id)
            return pipeline

        if len(self._store) >= self.max_sessions:
            self._store.popitem(last=False)

        pipeline = InteractionPipeline(brain=brain, memory=SessionMemory())
        self._store[session_id] = (pipeline, now)
        return pipeline


class AppState:
    def __init__(self):
        self.long_term_memory = LongTermMemory()
        # LorePackManager auto-migrates data/lore_book.json → data/lore/sara_character.json
        # on first run, then manages all .json files under data/lore/ as named packs.
        self.lore_manager = LorePackManager()
        self.brain = CoreBrain(
            companion_name="Sara",
            long_term_memory=self.long_term_memory,
            lore_book=self.lore_manager,
        )
        self.sessions = SessionStore()
        self.ws_groups: Dict[str, Set[WebSocket]] = {}
        self.locks: Dict[str, asyncio.Lock] = {}
        self.last_interaction: Dict[str, float] = {}
        self.proactive_pending: Dict[str, bool] = {}
        self.tts_providers: Dict[str, str] = {}
        self.mood_presets: Dict[str, str] = {}
        self.lore_enabled: Dict[str, bool] = {}   # Per-session master lore toggle
        # Per-session pack states: {session_id: {pack_name: bool}}
        # Absent key means the pack is enabled by default (None = all).
        self.lore_pack_states: Dict[str, Dict[str, bool]] = {}
        # Per-session selected lore pack name
        self.selected_lore: Dict[str, str] = {}
        # Per-lore-name LTM instances (shared across sessions using the same lore,
        # isolated from sessions using a different lore — different ChromaDB collection).
        self.lore_ltms: Dict[str, LongTermMemory] = {}

    def get_pipeline(self, session_id: str) -> InteractionPipeline:
        return self.sessions.get(session_id, self.brain)

    def get_lore_ltm(self, lore_name: str) -> LongTermMemory:
        """Get or lazily create a per-lore ChromaDB collection.
        All sessions that pick the same lore share this LTM instance —
        they build up memories together under that character's collection
        while remaining fully isolated from other lore packs.
        """
        if lore_name not in self.lore_ltms:
            safe_name = lore_name.replace("-", "_").replace(" ", "_")[:48]
            self.lore_ltms[lore_name] = LongTermMemory(
                collection_name=f"sara_facts_{safe_name}"
            )
        return self.lore_ltms[lore_name]


state = AppState()


# ════════════════════════════════════════════════════════════════════════
# TTS
# ════════════════════════════════════════════════════════════════════════

# Prosody adjustments for Edge-TTS, keyed by the emotion strings that
# the LLM already outputs (see personality.py's JSON schema).
#
# Edge-TTS Communicate() accepts three prosody parameters:
#   rate   — speech speed, as a signed percentage string: "+15%" / "-10%"
#   pitch  — voice pitch offset in Hz: "+6Hz" / "-5Hz"
#   volume — output volume, as a signed percentage string: "+5%" / "-5%"
#
# These are passed directly into the <prosody> SSML tag that edge-tts
# wraps the text in. Microsoft's free Edge-TTS endpoint does NOT support
# the <mstts:express-as> style tag (that's Azure Cognitive Services only),
# so prosody rate/pitch/volume adjustments are the ceiling of what's
# achievable here without paying for Azure.
#
# Values tuned by ear against the default en-US-AnaNeural voice — if a
# different voice is configured, re-check that the pitch offsets don't
# push it into an unnatural range (some voices have a narrower usable
# pitch window than AnaNeural).
EMOTION_PROSODY: dict[str, dict[str, str]] = {
    "neutral":  {"rate": "+0%",   "pitch": "+0Hz",  "volume": "+0%"},
    "happy":    {"rate": "+15%",  "pitch": "+8Hz",  "volume": "+5%"},
    "sad":      {"rate": "-15%",  "pitch": "-7Hz",  "volume": "-5%"},
    "worried":  {"rate": "+8%",   "pitch": "+3Hz",  "volume": "+0%"},
    "angry":    {"rate": "+12%",  "pitch": "+5Hz",  "volume": "+10%"},
    "confused": {"rate": "-5%",   "pitch": "+2Hz",  "volume": "+0%"},
    "blushing": {"rate": "-8%",   "pitch": "+3Hz",  "volume": "-3%"},
    "jealous":  {"rate": "+5%",   "pitch": "+1Hz",  "volume": "+3%"},
}

# Fallback used whenever the LLM emits an emotion string we don't
# recognise — keeps the voice at neutral defaults rather than crashing.
_PROSODY_NEUTRAL = EMOTION_PROSODY["neutral"]


async def generate_tts(
    text: str,
    provider: str,
    edge_voice: str = "en-US-AnaNeural",
    emotion: str = "neutral",
) -> tuple[bytes, list[dict]]:
    """
    Returns (audio_bytes, word_boundaries).

    word_boundaries is a list of {word, offset_ms, duration_ms} dicts,
    populated only when provider is "edge-tts" — edge-tts emits precise
    per-word timing events (type "WordBoundary") alongside audio chunks
    during streaming, which the frontend uses to drive mouth-open timing
    on the Live2D model instead of relying purely on raw audio amplitude.

    All non-edge-tts providers return an empty list; the frontend falls
    back to amplitude-based lip sync in that case.
    """
    if not text or not text.strip():
        return b"", []

    if provider == "mock":
        return b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x44" + b"\x00" * 2000, []

    if provider == "elevenlabs":
        api_key = os.getenv("ELEVENLABS_API_KEY", "")
        if not api_key:
            print("Missing ELEVENLABS_API_KEY, falling back to edge-tts")
            provider = "edge-tts"
        else:
            voice_id = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
            try:
                url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
                headers = {"xi-api-key": api_key, "Content-Type": "application/json"}
                payload = {"text": text, "model_id": "eleven_turbo_v2_5"}
                async with httpx.AsyncClient() as client:
                    resp = await client.post(url, headers=headers, json=payload, timeout=30.0)
                    if resp.status_code == 200:
                        # ElevenLabs has no word-level timing API on the free tier,
                        # so return empty boundaries — the frontend uses amplitude
                        # fallback for ElevenLabs audio.
                        return resp.content, []
                    print(f"ElevenLabs failed ({resp.status_code}), falling back")
                    provider = "edge-tts"
            except Exception as e:
                print(f"ElevenLabs error: {e}, falling back")
                provider = "edge-tts"

    if provider == "edge-tts":
        try:
            # Look up prosody values for this emotion; fall back to neutral
            # defaults if the emotion string is unrecognised or missing.
            prosody = EMOTION_PROSODY.get(emotion or "neutral", _PROSODY_NEUTRAL)

            communicate = edge_tts.Communicate(
                text,
                edge_voice,
                rate=prosody["rate"],
                pitch=prosody["pitch"],
                volume=prosody["volume"],
            )
            audio_data = b""
            word_boundaries: list[dict] = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_data += chunk["data"]
                elif chunk["type"] == "WordBoundary":
                    # Edge-TTS reports timing offsets in 100-nanosecond ticks
                    # (same unit as Windows FILETIME). Dividing by 10,000
                    # converts to milliseconds, which matches what the frontend
                    # reads from audio.currentTime (converted: seconds * 1000).
                    word_boundaries.append({
                        "word":        chunk["text"],
                        "offset_ms":   chunk["offset"]   // 10_000,
                        "duration_ms": chunk["duration"] // 10_000,
                    })
            return audio_data, word_boundaries
        except Exception as e:
            print(f"Edge-TTS Error: {e}")

    return b"", []


async def classify_intent_groq(text: str) -> str:
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        return "NONE"

    system_prompt = """You are an intent classifier for a conversational AI.
Analyze the user's message and determine if the AI needs a 'thinking pause' before answering.
- If it's a simple, personal, or conversational question (e.g. 'Can you cook?', 'Who am I?', 'Hello'), output ONLY: NONE
- If it's a question requiring some thought or search (e.g. 'What is a black hole?', 'Write a poem'), output ONLY: HMM
- If it's a highly complex, deep, or multi-step question (e.g. 'How do I build a nuclear reactor?', 'Explain quantum physics in detail'), output ONLY: LET_ME_THINK
Output NO OTHER TEXT. ONLY the exact word."""

    payload = {
        "model": "llama-3.1-8b-instant",   # Updated: llama3-8b-8192 is deprecated
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text}
        ],
        "temperature": 0.0,
        "max_tokens": 10
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=2.0
            )
            if resp.status_code == 200:
                result = resp.json()["choices"][0]["message"]["content"].strip().upper()
                if "LET_ME_THINK" in result: return "LET_ME_THINK"
                if "HMM" in result: return "HMM"
    except Exception as e:
        print(f"Intent Router Error: {e}")
    return "NONE"


# ════════════════════════════════════════════════════════════════════════
# Broadcast + core processing
# ════════════════════════════════════════════════════════════════════════

async def broadcast_ws(sid: str, payload: dict):
    dead_sockets = set()
    for ws in state.ws_groups.get(sid, []):
        try:
            await ws.send_json(payload)
        except Exception:
            dead_sockets.add(ws)
    for ws in dead_sockets:
        state.ws_groups[sid].discard(ws)


async def _extract_and_store_memory(user_message: str, assistant_text: str, ltm: LongTermMemory = None):
    """
    Fire-and-forget (see call site in process_and_stream — wrapped in
    asyncio.create_task, never awaited there). Runs AFTER the live
    response is already on its way to the user, so a slow or failed
    extraction call can never add latency or break the live conversation.
    Section 1.A of the memory spec.

    ltm: per-session/per-lore LongTermMemory to write into. Falls back to
    the global state.long_term_memory if not provided.
    """
    target_ltm = ltm or state.long_term_memory
    try:
        facts = await asyncio.to_thread(extract_memories_sync, user_message, assistant_text)
        for item in facts:
            target_ltm.store_fact(
                item["fact"], item.get("category", "identity"), item.get("importance", 3)
            )
        if facts:
            print(f"[MEMORY EXTRACT] stored {len(facts)} fact(s) from this exchange")
    except Exception as e:
        print(f"Memory extraction task failed: {e}")


async def process_and_stream(
    sid: str, msg: str, tts_prov: str, edge_vc: str, _llm_provider: str, _llm_model: str,
    active_tasks: set,
    is_continuation: bool = False,
    trailed_off: bool = False,
    incoming_id: str = None,
    regen_direction: str = None,
):
    pipeline = state.get_pipeline(sid)
    lock = state.locks.setdefault(sid, asyncio.Lock())
    current_mood = state.mood_presets.get(sid, DEFAULT_MOOD)
    lore_en = state.lore_enabled.get(sid, True)   # Master lore toggle

    # Per-lore LTM: use the session's selected lore collection if one has been chosen.
    selected_lore = state.selected_lore.get(sid)
    session_ltm = state.get_lore_ltm(selected_lore) if selected_lore else None
    # Also sync the pipeline's LTM so brain.respond() gets the right one.
    if pipeline.long_term_memory is not session_ltm:
        pipeline.long_term_memory = session_ltm

    # Compute which packs are active for this session.
    # If no pack states have been set yet, None signals LorePackManager
    # to query all packs (the out-of-the-box default).
    pack_states = state.lore_pack_states.get(sid, {})
    if pack_states:
        all_pack_names = {p["name"] for p in state.lore_manager.list_packs()}
        # A pack is enabled if explicitly True, or absent from pack_states (default True)
        enabled_packs = {name for name in all_pack_names if pack_states.get(name, True)}
    else:
        enabled_packs = None   # None = all packs

    intent_task = None
    if not is_continuation:
        intent_task = asyncio.create_task(classify_intent_groq(msg))

    async with lock:
        response_task = asyncio.create_task(asyncio.to_thread(
            pipeline.handle, msg, tts_prov, _llm_provider, _llm_model,
            current_mood, trailed_off, incoming_id, is_continuation, regen_direction,
            lore_en, enabled_packs
        ))

        if intent_task:
            intent = await intent_task
            if intent in ["HMM", "LET_ME_THINK"]:
                filename = "hmm.mp3" if intent == "HMM" else "let_me_think.mp3"
                filepath = os.path.join("static", "audio", filename)
                if os.path.exists(filepath):
                    with open(filepath, "rb") as f:
                        filler_audio = f.read()
                    await broadcast_ws(sid, {
                        "type": "audio",
                        "content": base64.b64encode(filler_audio).decode("utf-8"),
                        "is_filler": True
                    })

        response = await response_task

        if response.llm_provider != _llm_provider or response.llm_model != _llm_model:
            new_val = _map_model_to_ui_value(response.llm_provider, response.llm_model)
            await broadcast_ws(sid, {
                "type": "fallback_alert",
                "new_val": new_val,
                "text": f"Auto-switched to {new_val.upper()} due to upstream API error."
            })

    if is_continuation and not response.text.strip():
        return

    # Fire-and-forget memory extraction — kicked off here (not awaited)
    # so it runs concurrently with the TTS broadcast below instead of
    # adding any latency to it. Skipped for continuation/internal turns
    # (the synthetic "[SYSTEM: ...]" prompts) since those aren't real user
    # exchanges worth extracting facts from.
    if not is_continuation and response.text.strip():
        asyncio.create_task(_extract_and_store_memory(msg, response.text, ltm=session_ltm))

    await broadcast_ws(sid, {
        "type": "metadata",
        "id": response.message_id,
        "text": response.text,
        "emotion": response.emotion,
        # affection_delta magnitude drives expression intensity on the frontend.
        # delta=0 → subtle (0.25); delta=±3 → full (1.0).
        # The frontend uses Math.abs() so sign doesn't matter here.
        "affection_delta": response.affection_delta,
    })
    # Keep the bond-level bar in the UI current after every response.
    await broadcast_ws(sid, {
        "type": "bond_update",
        "level": state.brain.affection_level,
    })

    tts_text = re.sub(r'(?i),\s*Master,', ' Master,', response.text)

    # Pass the detected emotion into generate_tts so Edge-TTS can apply
    # the matching prosody preset (rate / pitch / volume) for every sentence
    # in this response. All sentences in a single turn share the same
    # emotion — the LLM outputs one emotion per full reply, not per sentence.
    response_emotion = response.emotion or "neutral"

    for sentence in split_into_sentences(tts_text):
        audio_bytes, word_boundaries = await generate_tts(
            sentence, tts_prov, edge_vc, emotion=response_emotion
        )
        if audio_bytes:
            await broadcast_ws(sid, {
                "type": "audio",
                "content": base64.b64encode(audio_bytes).decode("utf-8"),
                # Word boundaries let the frontend synchronise mouth-open
                # timing to actual spoken words instead of raw amplitude.
                # Empty list when using ElevenLabs — frontend falls back
                # to amplitude-based lip sync in that case automatically.
                "word_boundaries": word_boundaries,
            })

    continue_time = getattr(response, 'continue_in_seconds', 0)
    if isinstance(continue_time, (int, float)) and continue_time > 0:
        continue_time = max(1.5, min(continue_time, 6))

        async def schedule_continuation():
            await asyncio.sleep(continue_time)
            if state.proactive_pending.get(sid, False):
                prompt = (
                    "[SYSTEM: You previously scheduled a continuation for your current "
                    "task. Please output your next step now. If you are finished, output "
                    "EXACTLY AND ONLY an empty JSON object: {}]"
                )
                task = asyncio.create_task(process_and_stream(
                    sid, prompt, tts_prov, edge_vc, _llm_provider, _llm_model, active_tasks,
                    is_continuation=True
                ))
                active_tasks.add(task)
                task.add_done_callback(active_tasks.discard)

        task = asyncio.create_task(schedule_continuation())
        active_tasks.add(task)
        task.add_done_callback(active_tasks.discard)


async def process_proactive(sid: str, prompt: str):
    proactive_tasks: set = set()
    tts_prov = state.tts_providers.get(sid, "edge-tts")
    try:
        await process_and_stream(
            sid, prompt, tts_prov, "en-US-AnaNeural", "groq", "llama-3.1-8b-instant",
            proactive_tasks, is_continuation=True
        )
    except Exception as e:
        print(f"Proactive task error: {e}")


async def proactive_loop():
    while True:
        await asyncio.sleep(10)
        now = time.time()
        for sid, last_time in list(state.last_interaction.items()):
            if state.proactive_pending.get(sid, False):
                if now - last_time > 180:
                    state.proactive_pending[sid] = False
                    if sid in state.ws_groups and state.ws_groups[sid]:
                        prompt = (
                            "[SYSTEM: The user has been silent for 3 minutes after your "
                            "last response. Look at the last message. Was the conversation "
                            "left hanging? If yes, proactively ask if they are still there "
                            "or need help (e.g. 'Hey, are you still there?'). If the "
                            "conversation naturally concluded, output EXACTLY AND ONLY an "
                            "empty JSON object: {}]"
                        )
                        asyncio.create_task(process_proactive(sid, prompt))


@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(proactive_loop())
    yield


app = FastAPI(title="SARA API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/models", StaticFiles(directory="Models"), name="models")


@app.get("/")
async def serve_frontend():
    return FileResponse("static/index.html")


@app.get("/api/lore/packs")
async def get_lore_packs():
    """
    List all available lore packs (scans data/lore/ directory).
    The UI calls this on every connect to populate the pack toggles.
    Returns: {packs: [{name, entry_count}, ...]}
    """
    return {"packs": state.lore_manager.list_packs()}


# ════════════════════════════════════════════════════════════════════════
# REST endpoints
# ════════════════════════════════════════════════════════════════════════

@app.post("/api/chat")
async def handle_chat(request: Request,
                      llm_provider: str = Query("groq"),
                      tts_provider: str = Query("edge-tts"),
                      mood_preset: str = Query(DEFAULT_MOOD)):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    message = data.get("message", "")
    session_id = data.get("session_id", "default")

    if not message.strip():
        raise HTTPException(status_code=400, detail="Empty message")

    pipeline = state.get_pipeline(session_id)
    lock = state.locks.setdefault(session_id, asyncio.Lock())

    async with lock:
        response = await asyncio.to_thread(
            pipeline.handle, message, tts_provider, llm_provider, None, mood_preset
        )

    tts_url = f"/api/tts?text={urllib.parse.quote(response.text)}&tts_provider={urllib.parse.quote(tts_provider)}"

    return {
        "id": response.message_id,
        "text": response.text,
        "emotion": response.emotion,
        "audio_url": tts_url
    }


@app.get("/api/tts")
async def handle_tts(text: str = Query(...), tts_provider: str = Query("mock")):
    if not text.strip():
        raise HTTPException(status_code=400, detail="Missing text parameter")

    # Word boundaries are not needed for the REST endpoint — the audio is
    # streamed as raw bytes for direct playback, not WebSocket-delivered
    # alongside timing data. Discard them.
    audio_bytes, _ = await generate_tts(text, tts_provider)

    async def chunk_generator():
        chunk_size = 512
        for i in range(0, len(audio_bytes), chunk_size):
            yield audio_bytes[i:i + chunk_size]
            await asyncio.sleep(0.001)

    return StreamingResponse(chunk_generator(), media_type="audio/mpeg")


@app.post("/api/stt")
async def handle_stt(file: UploadFile = File(...)):
    if not file:
        raise HTTPException(status_code=400, detail="Missing file payload")

    audio_data = await file.read()
    if len(audio_data) == 0:
        raise HTTPException(status_code=400, detail="Zero-byte file")

    api_key = os.getenv("GROQ_API_KEY", "")
    if api_key:
        url = "https://api.groq.com/openai/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {api_key}"}
        files = {"file": (file.filename, audio_data, file.content_type)}
        data = {"model": "whisper-large-v3"}
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, headers=headers, files=files, data=data, timeout=30.0)
                if resp.status_code == 200:
                    text = resp.json().get("text", "").strip()
                    lower_text = text.lower()
                    if (lower_text in ["", "subtitles by amara.org", "amara.org", "subscribe to my channel",
                                        "thank you.", "thanks for watching!"]
                            or "yn ystod" in lower_text
                            or "user is speaking" in lower_text):
                        return {"text": ""}
                    return {"text": text}
        except Exception as e:
            print(f"Groq STT Error: {e}")

    return {"text": "STT placeholder transcript"}


# ════════════════════════════════════════════════════════════════════════
# WebSocket
# ════════════════════════════════════════════════════════════════════════

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, session_id: str = "default",
                             llm_provider: str = "groq", tts_provider: str = "edge-tts",
                             edge_voice: str = "en-US-AnaNeural"):
    llm_model = None
    await websocket.accept()

    if session_id not in state.ws_groups:
        state.ws_groups[session_id] = set()
    state.ws_groups[session_id].add(websocket)

    active_tasks: set[asyncio.Task] = set()

    # Send initial bond level immediately on connect so the bar isn't blank.
    await websocket.send_json({
        "type": "bond_update",
        "level": state.brain.affection_level,
    })

    def _cancel_active_tasks():
        for t in list(active_tasks):
            t.cancel()
        active_tasks.clear()

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg_data = json.loads(data)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            msg_type = msg_data.get("type")

            if msg_type == "text":
                content = msg_data.get("content", "")
                incoming_id = msg_data.get("id")
                trailed_off = bool(msg_data.get("trailed_off", False))
                state.last_interaction[session_id] = time.time()
                state.proactive_pending[session_id] = True

                content_lower = content.lower().strip()
                is_interrupt = False
                interrupt_words = ["stop", "cancel", "interrupt", "shut up", "pause", "be quiet"]
                word_count = len(content_lower.split())
                if word_count <= 4 and any(w in content_lower for w in interrupt_words):
                    if ("don't stop" not in content_lower
                            and "do not stop" not in content_lower
                            and "never stop" not in content_lower):
                        is_interrupt = True

                if is_interrupt:
                    _cancel_active_tasks()
                    await broadcast_ws(session_id, {"type": "cancelled", "message": "Stream cancelled"})
                    state.proactive_pending[session_id] = False
                else:
                    task = asyncio.create_task(process_and_stream(
                        session_id, content, tts_provider, edge_voice, llm_provider, llm_model,
                        active_tasks, trailed_off=trailed_off, incoming_id=incoming_id
                    ))
                    active_tasks.add(task)
                    task.add_done_callback(active_tasks.discard)

            elif msg_type == "delete_from":
                target_id = msg_data.get("id")
                if target_id:
                    _cancel_active_tasks()
                    state.proactive_pending[session_id] = False
                    pipeline = state.get_pipeline(session_id)
                    lock = state.locks.setdefault(session_id, asyncio.Lock())
                    async with lock:
                        found = pipeline.memory.truncate_at_id(target_id)
                    await websocket.send_json({"type": "delete_ok", "id": target_id, "found": found})

            elif msg_type == "regenerate":
                target_id = msg_data.get("id")
                direction = (msg_data.get("direction") or "").strip() or None
                if target_id:
                    _cancel_active_tasks()
                    state.proactive_pending[session_id] = False

                    pipeline = state.get_pipeline(session_id)
                    lock = state.locks.setdefault(session_id, asyncio.Lock())
                    async with lock:
                        found = pipeline.memory.truncate_at_id(target_id)
                        last_user_msg = pipeline.memory.pop_last_visible_user_message() if found else None

                    await websocket.send_json({"type": "delete_ok", "id": target_id, "found": found})

                    if last_user_msg is not None:
                        state.last_interaction[session_id] = time.time()
                        state.proactive_pending[session_id] = True
                        task = asyncio.create_task(process_and_stream(
                            session_id, last_user_msg, tts_provider, edge_voice, llm_provider, llm_model,
                            active_tasks, regen_direction=direction
                        ))
                        active_tasks.add(task)
                        task.add_done_callback(active_tasks.discard)

            elif msg_type == "config":
                llm_provider = msg_data.get("llm_provider", llm_provider)
                if "llm_model" in msg_data:
                    llm_model = msg_data["llm_model"]
                tts_provider = msg_data.get("tts_provider", tts_provider)
                state.tts_providers[session_id] = tts_provider
                state.mood_presets[session_id] = msg_data.get(
                    "mood_preset", state.mood_presets.get(session_id, DEFAULT_MOOD)
                )
                edge_voice = msg_data.get("edge_voice", edge_voice)
                # Per-session master lore toggle
                if "lore_enabled" in msg_data:
                    state.lore_enabled[session_id] = bool(msg_data["lore_enabled"])
                # Per-pack toggles: {pack_name: bool}
                if "lore_packs" in msg_data and isinstance(msg_data["lore_packs"], dict):
                    current = state.lore_pack_states.setdefault(session_id, {})
                    current.update({k: bool(v) for k, v in msg_data["lore_packs"].items()})
                new_session = msg_data.get("session_id", session_id)
                if new_session != session_id:
                    state.ws_groups[session_id].discard(websocket)
                    session_id = new_session
                    if session_id not in state.ws_groups:
                        state.ws_groups[session_id] = set()
                    state.ws_groups[session_id].add(websocket)
                await websocket.send_json({
                    "type": "config_ok",
                    "message": "Configuration updated",
                    "bond_level": state.brain.affection_level,
                    "lore_enabled": state.lore_enabled.get(session_id, True),
                    # Return full pack state so UI can sync after reconnect
                    "lore_packs": state.lore_pack_states.get(session_id, {}),
                })

            elif msg_type == "select_lore":
                lore_name = (msg_data.get("lore_name") or "").strip()
                if not lore_name:
                    await websocket.send_json({"type": "error", "message": "Missing lore_name"})
                    continue

                # Store the selection and configure lore state for this session
                state.selected_lore[session_id] = lore_name
                state.lore_enabled[session_id] = True
                # Enable ONLY the selected pack; disable all others
                all_packs = {p["name"] for p in state.lore_manager.list_packs()}
                state.lore_pack_states[session_id] = {
                    name: (name == lore_name) for name in all_packs
                }

                # Clear session memory — fresh conversation with this character
                pipeline = state.get_pipeline(session_id)
                pipeline.memory.clear()
                # Attach per-lore LTM to the pipeline
                pipeline.long_term_memory = state.get_lore_ltm(lore_name)

                # Get first_message from lore metadata
                import uuid as _uuid
                meta = state.lore_manager.get_pack_metadata(lore_name)
                first_msg = (meta.get("first_message") or "").strip()

                # Confirm selection to the frontend
                await websocket.send_json({
                    "type": "lore_selected",
                    "lore_name": lore_name,
                    "display_name": meta.get("display_name", lore_name),
                    "tagline": meta.get("tagline", ""),
                })

                # Deliver first_message as a spoken opening line
                if first_msg:
                    msg_id = str(_uuid.uuid4())
                    await websocket.send_json({
                        "type": "metadata",
                        "id": msg_id,
                        "text": first_msg,
                        "emotion": "neutral",
                        "affection_delta": 0,
                    })
                    tts_prov_now = state.tts_providers.get(session_id, tts_provider)
                    for sentence in split_into_sentences(first_msg):
                        audio_bytes, word_boundaries = await generate_tts(
                            sentence, tts_prov_now, edge_voice, emotion="neutral"
                        )
                        if audio_bytes:
                            await websocket.send_json({
                                "type": "audio",
                                "content": base64.b64encode(audio_bytes).decode("utf-8"),
                                "word_boundaries": word_boundaries,
                            })

    except WebSocketDisconnect:
        pass
    finally:
        _cancel_active_tasks()
        state.ws_groups[session_id].discard(websocket)