<div align="center">

# S A R A
### *Sentient Adaptive Responsive Ally*

**A local AI companion with a Live2D avatar, long-term memory, and a real character.**

[![Python](https://img.shields.io/badge/Python-3.10+-blue?style=flat-square&logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/license-MIT-purple?style=flat-square)](LICENSE)

</div>

---

## What is SARA?

SARA is a locally-hosted AI companion that runs entirely on your machine. She's not a chatbot — she has a **character**, **long-term memory** that persists across sessions, and a **Live2D avatar** that reacts emotionally to conversations in real time.

She starts as a quiet, guarded street beggar. She earns trust slowly. Her expressions change. She remembers your name, what you talked about last week, what you said that made her smile.

---

## Features

- 🧠 **Long-term memory** — Extracts and stores facts across sessions using ChromaDB (vector search + recency blending)
- 📖 **Lore system** — Keyword-triggered character facts injected into context (like SillyTavern's Lorebook)
- 🎭 **Live2D avatar** — Animated 2D model with real-time expression changes driven by emotion detection
- 🎙️ **Voice input** — Silero VAD neural voice activity detection; push-to-talk or continuous listening mode
- 🔊 **Multi-provider TTS** — edge-tts (free, no key needed) or ElevenLabs (premium voice)
- ⚡ **Multi-LLM backend** — Groq (Llama 70B / 8B), Google Gemini, OpenRouter — auto-fallback on API errors
- 🌧️ **Character selection screen** — Clank.world-style lore selection on startup; each character keeps isolated memories
- 💬 **Streaming responses** — Sentence-by-sentence TTS so responses feel instant
- 🔄 **Message regeneration** — Edit or regenerate any reply in the chat history

---

## Quick Start

### 1. Clone the repo

```bash
git clone https://github.com/rounakrkr/SARA.git
cd SARA
```

### 2. Set up Python environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Linux / macOS
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Configure API keys

```bash
cp .env.example .env
# Open .env and fill in your keys (see the file for links to get each key)
```

**Minimum required:** `GROQ_API_KEY` (free at [console.groq.com](https://console.groq.com/keys))  
Everything else is optional — SARA works with just Groq + edge-tts.

### 4. Download VAD assets

Neural voice activity detection (Silero VAD) needs ONNX weights:

```bash
python download_vad_assets.py
```

### 5. Add Live2D models

SARA needs at least one Live2D Cubism 4 model in the `Models/` folder.  
Get free models from:
- [nizima LIVE model gallery](https://nizimalive.com/en/models/)
- [booth.pm](https://booth.pm/en/browse/Live2D) — search "free Live2D model"

Place the extracted model folder inside `Models/` and add it to the dropdown in `static/index.html`.

### 6. Run

```bash
# Windows
run.bat

# Or directly
python main.py
```

Open your browser at **http://localhost:8000**

---

## Project Structure

```
SARA/
├── app.py              # FastAPI app — WebSocket handler, TTS, session management
├── main.py             # Entry point (uvicorn)
├── bootstrap.py        # ASGI setup
├── core/
│   ├── brain.py        # LLM call, memory retrieval, lore injection
│   ├── pipeline.py     # Per-session interaction pipeline
│   ├── personality.py  # System prompt builder
│   └── types.py        # Shared dataclasses
├── memory/
│   ├── long_term.py    # ChromaDB-backed fact store with semantic search
│   ├── lore.py         # Keyword-triggered lore book (LorePackManager)
│   └── session.py      # In-session conversation history
├── static/
│   ├── app.js          # Frontend logic (Live2D, WebSocket, VAD, audio)
│   ├── style.css       # UI styles
│   └── index.html      # Main page
├── data/
│   └── lore/           # Character lore packs (.json + .meta.json)
├── .env.example        # API key template — copy to .env
└── requirements.txt
```

---

## How Memory Works

SARA has two memory layers:

| Layer | System | How it works |
|---|---|---|
| **Long-term facts** | ChromaDB | After each exchange, an LLM call extracts durable facts ("user's name is Rounak", "has an exam next week") and stores them as embeddings. Retrieved by semantic similarity + recency on future turns. |
| **Character lore** | Keyword matching | A lorebook of Sara's character facts is injected into every prompt. Additional user-specific facts can be added at runtime. |

Each character (lore pack) has its **own isolated memory collection** — switching characters never mixes memories.

---

## Adding New Characters

1. Create `data/lore/your_character.json` — an array of lore entries:
```json
[
  {
    "id": "uuid-here",
    "content": "Your character is a librarian who has never left the city",
    "keywords": ["librarian", "city", "books"],
    "category": "general",
    "created_at": 1700000000.0,
    "last_triggered_at": null,
    "trigger_count": 0
  }
]
```

2. Create `data/lore/your_character.meta.json`:
```json
{
  "display_name": "Character Name",
  "tagline": "Short tagline",
  "description": "One-sentence description for the selection screen.",
  "cover_emoji": "📚",
  "accent_color": "#6366f1",
  "tags": ["tag1", "tag2"],
  "first_message": "Opening line the character says when selected."
}
```

3. Restart the server — the character appears automatically on the selection screen.

---

## Tech Stack

| Component | Technology |
|---|---|
| Backend | Python, FastAPI, WebSockets |
| LLM | Groq (Llama), Google Gemini, OpenRouter |
| TTS | edge-tts, ElevenLabs |
| STT / VAD | Silero VAD (ONNX), browser MediaRecorder API |
| Memory | ChromaDB, MiniLM embeddings |
| Avatar | Live2D Cubism 4, pixi-live2d-display, PixiJS |
| Frontend | Vanilla JS, CSS |

---

## License

MIT — do whatever you want with the code. Live2D models have their own separate licenses.

---

<div align="center">
<sub>Built with too much caffeine and a soft spot for street cats.</sub>
</div>
