// static/app.js  v38
// New in v38: Clank-world-style lore selection screen on connect;
// per-lore isolated memory; expression system bypass (direct coreModel writes).

window.onerror = function(msg, url, lineNo, columnNo, error) {
    appendMessage("System", "JS Error: " + msg);
    return false;
};
const originalConsoleError = console.error;
console.error = function(...args) {
    appendMessage("System", "Console Error: " + args.join(' '));
    originalConsoleError.apply(console, args);
};

// ── DOM refs ─────────────────────────────────────────────────────────────────
const canvas        = document.getElementById('canvas');
const chatContainer = document.getElementById('chat-container');
const chatBox       = document.getElementById('chat-history');
const chatInput     = document.getElementById('chat-input');
const sendBtn       = document.getElementById('send-btn');
const waifuSelect   = document.getElementById('waifu-select');
const toggleChatBtn = document.getElementById('toggle-chat-btn');
const ttsToggle     = document.getElementById('tts-toggle');
const llmSelect     = document.getElementById('llm-select');
const moodSelect    = document.getElementById('mood-select');
// lore-toggle removed from HTML (lore always active after selection)
const loreToggle    = null;

// Bond level display refs
const bondFill  = document.getElementById('bond-fill');
const bondValue = document.getElementById('bond-value');


// ── Lore selection screen state ──────────────────────────────────────────────
// Tracks which packs the server knows about (for selection screen).
let lorePacks = [];        // [{name, display_name, tagline, description, ...}]
let loreSelected = false;  // becomes true after user picks a pack

// Lore pack state legacy (kept for config broadcast compat)
const lorePackStates = {};

// ── Message history mirror ───────────────────────────────────────────────────
// Local display state only. The `id` field (not array position) is what
// gets sent to the backend for delete/regenerate — see deleteFromIndex()
// and submitRegen() below. Array position is still used for local DOM
// bookkeeping (which elements to fade out), but is never sent over the wire.
let chatMessageHistory = [];  // [{role, content, element, regenDialog?, id}]

// ── Live2D / PIXI ────────────────────────────────────────────────────────────
let model;
let isChatHidden = false;
const CHAT_WIDTH = 400;

const app = new PIXI.Application({
    view: canvas,
    autoStart: true,
    backgroundAlpha: 0,
    width: window.innerWidth - CHAT_WIDTH,
    height: window.innerHeight
});

toggleChatBtn.addEventListener('click', () => {
    isChatHidden = !isChatHidden;
    chatContainer.classList.toggle('collapsed', isChatHidden);
    toggleChatBtn.classList.toggle('flipped', isChatHidden);
    const w = isChatHidden ? window.innerWidth : window.innerWidth - CHAT_WIDTH;
    app.renderer.resize(w, window.innerHeight);
    if (model?.internalModel) model.x = (app.screen.width - model.width) / 2;
});

window.addEventListener('resize', () => {
    const w = isChatHidden ? window.innerWidth : window.innerWidth - CHAT_WIDTH;
    app.renderer.resize(w, window.innerHeight);
    if (model?.internalModel) {
        const scale = (window.innerHeight * 2.3) / (model.internalModel.originalHeight || window.innerHeight);
        model.scale.set(scale);
        model.x = (app.screen.width - model.width) / 2;
        model.y = _modelYOffset();
    }
});

function _modelYOffset() {
    let y = window.innerHeight * 0.05;
    if (waifuSelect.value.includes("%E9%AD%94%E5%A5%B3")) y -= window.innerHeight * 0.20;
    return y;
}

let isModelLoading = false;

async function loadModel(url) {
    if (isModelLoading) return;
    isModelLoading = true;
    try {
        if (model) {
            model.internalModel?.motionManager?.stopAllMotions();
            app.stage.removeChild(model);
            model.destroy({ children: true, texture: true, baseTexture: true });
            model = null;
        }
        model = await PIXI.live2d.Live2DModel.from(url);
        app.stage.addChild(model);
        const scale = (window.innerHeight * 2.3) / (model.internalModel.originalHeight || window.innerHeight);
        model.scale.set(scale);
        model.x = (app.screen.width - model.width) / 2;
        model.y = _modelYOffset();
        model.autoInteract = false;
        if (model.internalModel?.focusController) {
            model.internalModel.focusController.focus = () => {};
        }
        // Design_genius_White's only motion group is "" (empty string) —
        // the model3.json Motions key is {"" : [{"File":"idle.motion3.json"}]}.
        // The previous call used "idle" which silently failed (wrong group name).
        // Priority 1 = IDLE (won't interrupt a higher-priority motion).
        try { model.internalModel?.motionManager?.startRandomMotion("", 1); } catch(e) {}
    } catch(e) {
        console.error("Live2D Load Error:", e);
    } finally {
        isModelLoading = false;
    }
}

// ── WebSocket ─────────────────────────────────────────────────────────────────
const initialTts = ttsToggle.checked ? "elevenlabs" : "edge-tts";
const ws = new WebSocket(`ws://${window.location.host}/ws?llm_provider=groq&tts_provider=${initialTts}`);

ws.onopen = () => {
    appendMessage("System", "Connected.");
    // Don't start chat yet — show lore selection screen first.
    // sendConfigUpdate() is deferred until lore is selected.
    showLoreSelectionScreen();
};

ws.onmessage = async (event) => {
    const data = JSON.parse(event.data);
    if (data.type === "metadata") {
        appendMessage("SARA", data.text, data.emotion, data.id);
        triggerEmotion(data.emotion, data.affection_delta ?? 0);
    } else if (data.type === "lore_selected") {
        // Backend confirmed lore selection — hide screen, start chat
        hideLoreSelectionScreen(data);
    } else if (data.type === "bond_update") {
        updateBondDisplay(data.level);
        // Don't auto-fetch lore packs for toggles — selection screen handles that
    } else if (data.type === "audio") {
        playAudio(data.content, data.word_boundaries || []);
    } else if (data.type === "fallback_alert") {
        appendMessage("System", data.text);
        const sel = document.getElementById("llm-select");
        if (sel) { sel.value = data.new_val; sendConfigUpdate(); }
    } else if (data.type === "cancelled") {
        audioQueue = [];
        isPlayingQueue = false;
        if (globalAudio && !globalAudio.paused) { globalAudio.pause(); globalAudio.currentTime = 0; }
    } else if (data.type === "delete_ok") {
        // Backend confirmed — frontend already optimistically removed locally.
    }
};

// ── Emotion / Live2D expression ───────────────────────────────────────────────
//
// Design_genius_White expression parameter map — built from reading each
// .exp3.json file in the model directory. All use "Blend": "Add" with a
// base parameter value of 0, which means the expression system sets:
//   paramValue = 0 + expressionValue * expressionWeight
// After the model's own update applies that at full intensity, our ticker
// runs and OVERRIDES the parameter with (expressionValue * intensity),
// achieving true fractional blending. intensity = 1.0 is a no-op.
//
// Parameter IDs:
//   Param32 = happy expression   Param33 = angry
//   Param34 = worried            Param35 = confused
//   Param36 = blush              Param37 = jealous
//   Param38 = sad (Param39/40 stay 0, no override needed)

const DESIGN_GENIUS_EXPR_PARAMS = {
    happy:    [{ id: 'Param32', expressionValue: 1 }],
    angry:    [{ id: 'Param33', expressionValue: 1 }],
    worried:  [{ id: 'Param34', expressionValue: 1 }],
    confused: [{ id: 'Param35', expressionValue: 1 }],
    blush:    [{ id: 'Param36', expressionValue: 1 }],
    jealous:  [{ id: 'Param37', expressionValue: 1 }],
    sad:      [{ id: 'Param38', expressionValue: 1 }],
};

// Currently active expression override. Null when no expression is showing
// (neutral) or when intensity === 1.0 (ticker can skip the override work).
let activeExpressionOverride = null; // { params: [{id, expressionValue}], intensity }

// Mapping from LLM emotion string → DESIGN_GENIUS_EXPR_PARAMS key.
const EMOTION_TO_EXPR_KEY = {
    happy:    'happy',
    sad:      'sad',
    angry:    'angry',
    worried:  'worried',
    confused: 'confused',
    blushing: 'blush',
    blush:    'blush',
    jealous:  'jealous',
};

function triggerEmotion(emotion, affectionDelta = 0) {
    if (!model) return;

    const exprKey = EMOTION_TO_EXPR_KEY[emotion?.toLowerCase()] ?? null;

    // Compute intensity from affection_delta magnitude:
    //   |delta| = 0 → 0.25  (subtle — emotion is present but mild)
    //   |delta| = 1 → 0.50
    //   |delta| = 2 → 0.75
    //   |delta| = 3 → 1.00  (full — strong emotional moment)
    const magnitude = Math.abs(affectionDelta || 0);
    const intensity  = exprKey ? Math.min(1.0, 0.25 + (magnitude / 3) * 0.75) : 0;

    try {
        const core = model.internalModel?.coreModel;
        if (!core) return;

        // Clear ALL expression params directly (bypasses expressionManager entirely —
        // pixi-live2d-display v0.4.x's expressionManager is often null or races
        // with its own async blend; direct coreModel writes are always safe because
        // lip sync already uses the exact same path and works perfectly).
        for (const params of Object.values(DESIGN_GENIUS_EXPR_PARAMS)) {
            for (const { id } of params) {
                core.setParameterValueById(id, 0);
            }
        }

        // Register new expression for the ticker to sustain every frame.
        activeExpressionOverride = (exprKey && intensity > 0)
            ? { params: DESIGN_GENIUS_EXPR_PARAMS[exprKey] || [], intensity }
            : null;

    } catch(e) { console.warn('[expr] Expression change failed:', e); }
}

// ── Audio & Lip Sync ──────────────────────────────────────────────────────────
let audioCtx, globalAudio, globalAnalyzer, globalDataArray;
let audioQueue = [], isPlayingQueue = false;

// Word boundaries for the currently-playing audio chunk.
//
// Populated from the "word_boundaries" field of each incoming "audio"
// WebSocket message when using Edge-TTS. Edge-TTS emits a WordBoundary
// event for every word it speaks, with an exact offset and duration in
// 100-nanosecond ticks (converted to ms on the backend before sending).
//
// The ticker below uses audio.currentTime (converted to ms) to check
// whether playback is currently inside a word window, and opens the
// mouth accordingly. This is far more precise than amplitude analysis:
// amplitude can lag by a full FFT frame (10–40ms) and is sensitive to
// room acoustics; currentTime is locked to the decoded audio clock.
//
// When empty (ElevenLabs, or any non-Edge-TTS provider), the ticker
// falls back to the original amplitude-based approach automatically.
let currentWordBoundaries = [];

app.ticker.add(() => {
    if (!model) return;

    // ── Lip sync ────────────────────────────────────────────────────────────
    let mouthValue = 0;

    if (globalAudio && !globalAudio.paused && !globalAudio.ended) {
        if (currentWordBoundaries.length > 0) {
            // Word-boundary mode: check whether the current playback
            // position (ms) falls inside any word's time window.
            // audio.currentTime is in seconds — multiply by 1000 for ms.
            // This stays accurate across stalls and tab-backgrounding
            // because it's tied to the decoded audio clock, not wallclock.
            const currentMs = globalAudio.currentTime * 1000;
            const insideWord = currentWordBoundaries.some(
                wb => currentMs >= wb.offset_ms && currentMs < (wb.offset_ms + wb.duration_ms)
            );
            // Mouth fully open during a word, cleanly closed between words.
            // A fixed value of 0.85 (not 1.0) avoids the model's wide-open
            // "maximum" pose, which looks unnatural during normal speech.
            mouthValue = insideWord ? 0.85 : 0.0;
        } else if (globalAnalyzer && globalDataArray) {
            // Amplitude fallback for ElevenLabs (or any provider that
            // doesn't supply per-word timing). Same formula as before.
            globalAnalyzer.getByteFrequencyData(globalDataArray);
            let sum = 0;
            for (let i = 0; i < globalDataArray.length; i++) sum += globalDataArray[i];
            mouthValue = Math.max(0, Math.min(1, Math.pow((sum / globalDataArray.length - 5) / 60, 1.5)));
        }
    }

    // Design_genius_White uses "ParamMouthOpenY" (Cubism 4 naming);
    // setting both the Cubism 4 and legacy Cubism 2 names covers any
    // model that may be swapped in without needing a separate code path.
    model.internalModel.coreModel.setParameterValueById('ParamMouthOpenY',   mouthValue);
    model.internalModel.coreModel.setParameterValueById('PARAM_MOUTH_OPEN_Y', mouthValue);

    // ── Expression override (sustain per frame) ──────────────────────────────
    // triggerEmotion() cleared all params and stored the target in
    // activeExpressionOverride. Writing here every frame (after the model's
    // internal update) ensures our value is the last write before the GPU flush.

    if (activeExpressionOverride) {
        const { params, intensity } = activeExpressionOverride;
        try {
            const core = model.internalModel?.coreModel;
            if (core) {
                for (const { id, expressionValue } of params) {
                    core.setParameterValueById(id, expressionValue * intensity);
                }
            }
        } catch(e) { /* model transitioning — safe to skip */ }
    }

    // ── Idle motion continuity ────────────────────────────────────────────────
    //
    // Design_genius_White only has one motion in the "" (empty-string) group.
    // pixi-live2d-display does NOT auto-loop it because it's not in the
    // conventional "idle" group. We check isFinished() every frame (fast
    // boolean read) and restart the motion when it ends, keeping SARA alive
    // between turns instead of snapping to a frozen T-pose.
    const mm = model.internalModel?.motionManager;
    if (mm && mm.isFinished()) {
        try { mm.startRandomMotion('', 1); } catch(e) { /* model not ready */ }
    }
});

// Multiple "audio" broadcasts (one per sentence, from backend's sentence-
// streaming TTS) queue up here and play back to back automatically — this
// is what makes sentence-streaming work with zero other frontend changes.
//
// Each item in audioQueue is now { audio: base64String, wordBoundaries: [] }
// instead of a plain string. wordBoundaries drives precise mouth timing
// in the ticker above when using Edge-TTS; empty list for ElevenLabs.
async function playAudio(base64Data, wordBoundaries = []) {
    audioQueue.push({ audio: base64Data, wordBoundaries });
    processAudioQueue();
}

async function processAudioQueue() {
    if (isPlayingQueue || audioQueue.length === 0) return;
    isPlayingQueue = true;

    const { audio: base64Data, wordBoundaries } = audioQueue.shift();

    const bytes = Uint8Array.from(atob(base64Data), c => c.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: 'audio/mpeg' }));
    const audio = new Audio(url);
    globalAudio = audio;
    audio.crossOrigin = "anonymous";

    // Load the word boundaries for this sentence so the ticker can drive
    // the mouth. Cleared in onended/on-error so the mouth closes cleanly
    // between sentences and isn't left hanging open if playback fails.
    currentWordBoundaries = wordBoundaries || [];

    if (model) {
        if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        if (audioCtx.state === 'suspended') await audioCtx.resume();
        try {
            const src = audioCtx.createMediaElementSource(audio);
            if (!globalAnalyzer) {
                globalAnalyzer = audioCtx.createAnalyser();
                globalAnalyzer.fftSize = 256;
                globalDataArray = new Uint8Array(globalAnalyzer.frequencyBinCount);
            }
            src.connect(globalAnalyzer);
            globalAnalyzer.connect(audioCtx.destination);
        } catch(e) { console.warn("Analyzer error", e); }
    }

    audio.onended = () => {
        // Clear boundaries so the mouth closes and isn't stuck open between
        // sentences. The next sentence will set its own boundaries immediately.
        currentWordBoundaries = [];
        isPlayingQueue = false;
        processAudioQueue();
    };
    try {
        await audio.play();
    } catch(e) {
        // Playback failed (e.g. browser autoplay policy before first user gesture).
        currentWordBoundaries = [];
        isPlayingQueue = false;
        processAudioQueue();
    }
}

// ── appendMessage ─────────────────────────────────────────────────────────────
// `id` is the backend-stable id for this message (see session.py). User
// messages generate their own id client-side (crypto.randomUUID()) before
// the backend even sees them; SARA messages get their id from the backend's
// "metadata" broadcast. Either way, `id` — never array position — is what
// delete/regenerate send back to the backend.

function appendMessage(sender, text, emotion = null, id = null) {
    const role = sender === "User" ? "user" : sender === "SARA" ? "assistant" : null;

    const div = document.createElement("div");
    div.className = `message ${sender.toLowerCase()}`;

    if (role) {
        const msgIndex = chatMessageHistory.length;
        div.dataset.msgIndex = msgIndex;
        if (id) div.dataset.msgId = id;

        const actions = document.createElement("div");
        actions.className = "message-actions";

        if (role === "assistant") {
            const regenBtn = document.createElement("button");
            regenBtn.className = "action-btn regen-btn";
            regenBtn.title = "Try again";
            regenBtn.textContent = "↩";
            regenBtn.addEventListener("click", () => showRegenDialog(msgIndex, div));
            actions.appendChild(regenBtn);
        }

        const deleteBtn = document.createElement("button");
        deleteBtn.className = "action-btn delete-btn";
        deleteBtn.title = "Delete from here";
        deleteBtn.textContent = "✕";
        deleteBtn.addEventListener("click", () => deleteFromIndex(msgIndex));
        actions.appendChild(deleteBtn);

        div.appendChild(actions);

        chatMessageHistory.push({ role, content: text, element: div, regenDialog: null, id });
    }

    if (emotion) {
        const emoDiv = document.createElement("div");
        emoDiv.className = "emotion-tag";
        emoDiv.innerText = `[ ${emotion.toUpperCase()} ]`;
        div.appendChild(emoDiv);
    }

    const txtDiv = document.createElement("div");
    txtDiv.innerText = text;
    div.appendChild(txtDiv);

    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;
}

// ── Send a user message (typed, mic, or VAD) ─────────────────────────────────
// Single shared path so every user message gets a real id consistently.
function sendUserMessage(content, displayText = null, extra = {}) {
    const id = crypto.randomUUID();
    appendMessage("User", displayText ?? content, null, id);
    ws.send(JSON.stringify({ type: "text", content, id, ...extra }));
    return id;
}

// ── Delete from point ─────────────────────────────────────────────────────────
function deleteFromIndex(msgIndex) {
    closeAllRegenDialogs();
    const entry = chatMessageHistory[msgIndex];
    if (!entry || !entry.id) return;

    const toRemove = chatMessageHistory.slice(msgIndex);
    toRemove.forEach(e => {
        if (e.regenDialog) {
            e.regenDialog.classList.add("deleting");
            setTimeout(() => e.regenDialog.remove(), 230);
        }
        e.element.classList.add("deleting");
        setTimeout(() => e.element.remove(), 230);
    });

    chatMessageHistory = chatMessageHistory.slice(0, msgIndex);

    if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "delete_from", id: entry.id }));
    }
}

// ── Regenerate dialog ─────────────────────────────────────────────────────────
let activeRegenDialog = null;

function closeAllRegenDialogs() {
    if (activeRegenDialog) {
        activeRegenDialog.remove();
        activeRegenDialog = null;
        chatMessageHistory.forEach(e => { e.regenDialog = null; });
    }
}

function showRegenDialog(msgIndex, afterElement) {
    if (activeRegenDialog && activeRegenDialog.dataset.forIndex === String(msgIndex)) {
        closeAllRegenDialogs();
        return;
    }
    closeAllRegenDialogs();

    const dialog = document.createElement("div");
    dialog.className = "regen-dialog";
    dialog.dataset.forIndex = msgIndex;

    const label = document.createElement("p");
    label.className = "regen-dialog-label";
    label.innerHTML = 'Give SARA a direction <span>(optional)</span>';

    const row = document.createElement("div");
    row.className = "regen-input-row";

    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = 'e.g. "shorter" · "funnier" · "more detail"';

    const submitBtn = document.createElement("button");
    submitBtn.className = "regen-submit-btn";
    submitBtn.textContent = "↩ Try again";

    const cancelBtn = document.createElement("button");
    cancelBtn.className = "regen-cancel-btn";
    cancelBtn.textContent = "Cancel";

    row.appendChild(input);
    row.appendChild(submitBtn);
    row.appendChild(cancelBtn);
    dialog.appendChild(label);
    dialog.appendChild(row);

    afterElement.insertAdjacentElement("afterend", dialog);
    chatBox.scrollTop = chatBox.scrollHeight;
    input.focus();
    activeRegenDialog = dialog;

    if (chatMessageHistory[msgIndex]) chatMessageHistory[msgIndex].regenDialog = dialog;

    const submit = () => submitRegen(msgIndex, input.value.trim());
    submitBtn.addEventListener("click", submit);
    input.addEventListener("keydown", e => { if (e.key === "Enter") submit(); });
    cancelBtn.addEventListener("click", closeAllRegenDialogs);
}

function submitRegen(msgIndex, direction) {
    const entry = chatMessageHistory[msgIndex];
    if (!entry || !entry.id) return;

    const toRemove = chatMessageHistory.slice(msgIndex);
    toRemove.forEach(e => {
        if (e.regenDialog) e.regenDialog.remove();
        e.element.remove();
    });
    chatMessageHistory = chatMessageHistory.slice(0, msgIndex);
    activeRegenDialog = null;

    if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
            type: "regenerate",
            id: entry.id,
            direction: direction
        }));
    }
}

// ── Bond display ──────────────────────────────────────────────────────────────

function updateBondDisplay(level) {
    if (!bondFill || !bondValue) return;
    const clamped = Math.max(0, Math.min(100, level ?? 0));
    bondFill.style.width  = clamped + '%';
    bondValue.textContent = clamped;
}

// ── Lore Selection Screen ───────────────────────────────────────────────────────
//
// Replaces the old pack-toggle panel. On WS connect, we show a full-screen
// overlay so the user must choose a character before any chat begins.
// Each character card is built from the /api/lore/packs metadata.

async function showLoreSelectionScreen() {
    const screen  = document.getElementById('lore-select-screen');
    const grid    = document.getElementById('lore-cards-grid');
    const loading = document.getElementById('lore-loading-indicator');
    if (!screen) return;

    screen.classList.remove('hidden');
    loading.classList.remove('hidden');
    grid.innerHTML = '';

    try {
        const res  = await fetch('/api/lore/packs');
        if (!res.ok) throw new Error('API error');
        const data = await res.json();
        lorePacks = data.packs || [];
        loading.classList.add('hidden');
        lorePacks.forEach(pack => grid.appendChild(buildLoreCard(pack)));
    } catch (e) {
        console.warn('[Lore] failed to fetch packs:', e);
        loading.innerHTML = '<span>Could not load characters. Please refresh.</span>';
    }
}

function buildLoreCard(pack) {
    const card = document.createElement('div');
    card.className = 'lore-card';
    const accent = pack.accent_color || '#6366f1';
    card.style.setProperty('--card-accent', accent);

    const emoji    = pack.cover_emoji || '📖';
    const name     = pack.display_name || pack.name.replace(/_/g, ' ');
    const tagline  = pack.tagline || '';
    const desc     = pack.description || '';
    const tags     = (pack.tags || []).slice(0, 3);

    const tagsHTML = tags.map(t =>
        `<span class="lore-tag">${t}</span>`
    ).join('');

    card.innerHTML = `
        <div class="lore-card-emoji">${emoji}</div>
        <div class="lore-card-name">${name}</div>
        ${tagline ? `<div class="lore-card-tagline">${tagline}</div>` : ''}
        ${desc    ? `<div class="lore-card-desc">${desc}</div>` : ''}
        ${tagsHTML ? `<div class="lore-card-tags">${tagsHTML}</div>` : ''}
        <div class="lore-card-begin">▶ Begin</div>
    `;

    card.addEventListener('click', () => selectLore(pack));
    return card;
}

function selectLore(pack) {
    if (ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: 'select_lore', lore_name: pack.name }));
    // Card click visual feedback
    document.querySelectorAll('.lore-card').forEach(c => {
        c.style.opacity = (c.querySelector('.lore-card-name')?.textContent === (pack.display_name || pack.name.replace(/_/g, ' '))) ? '1' : '0.4';
        c.style.pointerEvents = 'none';
    });
}

function hideLoreSelectionScreen(data) {
    const screen = document.getElementById('lore-select-screen');
    if (screen) screen.classList.add('hidden');

    loreSelected = true;

    // Show active character in sidebar
    const section = document.getElementById('active-character-section');
    const pack    = lorePacks.find(p => p.name === data.lore_name) || {};
    if (section) {
        document.getElementById('active-char-emoji').textContent   = pack.cover_emoji || '📖';
        document.getElementById('active-char-name').textContent    = data.display_name || data.lore_name;
        document.getElementById('active-char-tagline').textContent = data.tagline || pack.tagline || '';
        section.style.display = 'block';
    }

    // Now fire the deferred config update
    sendConfigUpdate();
}

// Change character button — re-opens the selection screen
const changeCharBtn = document.getElementById('change-char-btn');
if (changeCharBtn) {
    changeCharBtn.addEventListener('click', () => {
        loreSelected = false;
        showLoreSelectionScreen();
    });
}

// ── Config broadcast ──────────────────────────────────────────────────────────
const sendConfigUpdate = () => {
    const ttsProvider  = ttsToggle.checked ? "elevenlabs" : "edge-tts";
    const llmSelection = llmSelect.value;
    const moodPreset   = moodSelect.value;
    const loreEnabled  = loreToggle ? loreToggle.checked : true;

    let llmProvider = "groq", llmModel = null;
    if      (llmSelection === "groq")      { llmProvider = "groq";       llmModel = "llama-3.3-70b-versatile"; }
    else if (llmSelection === "groq-fast") { llmProvider = "groq";       llmModel = "llama-3.1-8b-instant"; }
    else if (llmSelection === "gemini")    { llmProvider = "gemini";     llmModel = "gemini-3.1-flash-lite"; }
    else if (llmSelection === "dolphin")   { llmProvider = "openrouter"; llmModel = "cognitivecomputations/dolphin-mistral-24b-venice-edition:free"; }
    else if (llmSelection === "hermes")    { llmProvider = "openrouter"; llmModel = "nousresearch/hermes-3-llama-3.1-405b:free"; }

    if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
            type:         "config",
            tts_provider: ttsProvider,
            llm_provider: llmProvider,
            llm_model:    llmModel,
            mood_preset:  moodPreset,
            lore_enabled: loreEnabled,   // Per-session lore toggle
        }));
    }
};

ttsToggle.addEventListener('change', sendConfigUpdate);
llmSelect.addEventListener('change', sendConfigUpdate);
moodSelect.addEventListener('change', sendConfigUpdate);
if (loreToggle) loreToggle.addEventListener('change', sendConfigUpdate);

// ── Send message (typed) ──────────────────────────────────────────────────────
sendBtn.onclick = () => {
    const text = chatInput.value.trim();
    if (!text) return;
    closeAllRegenDialogs();
    sendUserMessage(text);
    chatInput.value = "";
};

chatInput.onkeypress = e => { if (e.key === "Enter") sendBtn.click(); };

// ── Push-to-talk microphone ───────────────────────────────────────────────────
const micBtn = document.getElementById('mic-btn');
let mediaRecorder, audioChunks = [];

micBtn.addEventListener('mousedown', async () => {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaRecorder = new MediaRecorder(stream);
        audioChunks = [];
        mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };
        mediaRecorder.onstop = async () => {
            micBtn.classList.remove('recording');
            const blob = new Blob(audioChunks, { type: 'audio/webm' });
            stream.getTracks().forEach(t => t.stop());
            const fd = new FormData();
            fd.append("file", blob, "recording.webm");
            appendMessage("System", "Transcribing...");
            try {
                const res  = await fetch('/api/stt', { method: 'POST', body: fd });
                const data = await res.json();
                if (data.text) {
                    sendUserMessage(data.text, "🎤 " + data.text);
                } else {
                    appendMessage("System", "Could not transcribe audio.");
                }
            } catch(err) { console.error("STT Error", err); appendMessage("System", "STT failed."); }
        };
        mediaRecorder.start();
        micBtn.classList.add('recording');
    } catch(err) { console.error("Mic denied", err); alert("Please allow microphone access."); }
});
micBtn.addEventListener('mouseup',    () => { if (mediaRecorder?.state === 'recording') mediaRecorder.stop(); });
micBtn.addEventListener('mouseleave', () => { if (mediaRecorder?.state === 'recording') mediaRecorder.stop(); });

// ── VAD (Continuous Listening) — Silero Neural VAD ───────────────────────────
//
// Architecture:
//   Previously used energy/amplitude thresholding over an adaptive noise
//   floor. Replaced with @ricky0123/vad-web (Silero VAD via ONNX/WASM,
//   runs fully client-side, zero server cost, MIT licensed).
//
//   Silero outputs a per-frame speech probability (0–1) instead of raw
//   loudness — it distinguishes human speech from coughs, door slams, fans,
//   and SARA's own TTS playback leaking into the mic.
//
// Spec compliance:
//   A. Neural VAD: Silero via @ricky0123/vad-web@0.0.30
//   B. Confirm window: vad-web minSpeechMs (200ms) — onSpeechStart only
//      fires after sustained speech, not a single loud frame.
//   C. Self-echo guard: checked in onSpeechStart — higher probability
//      threshold required while SARA's TTS is playing.
//   D. Trail-off detection: probability history slope in onSpeechEnd.
//      Gradual decline → extra wait before sending; sudden drop → send now.
//      Two-tier timeout concept preserved (short for clean stop, longer for
//      trail-off), now driven by probability shape not raw energy decline.
//   E. Pre-roll: vad-web's preSpeechPadMs (600ms built-in, replaces DelayNode).
//   F. Fail-safe: mic-ended detection (visible error), tab-visibility
//      recovery (auto-pause/resume on background/foreground).
//      Never fails silently — always shows a visible indicator.
//   G. STT in-flight guard: sttInFlight flag prevents overlapping /api/stt
//      calls if the user speaks again before the previous transcript returns.

const vadToggle = document.getElementById('vad-toggle');
const vadStatus  = document.getElementById('vad-status');

// ── Configuration constants ───────────────────────────────────────────────────

// Silero model output thresholds. The creators recommend setting
// negativeSpeechThreshold ~0.15 below positiveSpeechThreshold.
const VAD_POSITIVE_THRESHOLD = 0.50;   // above this → frame is speech
const VAD_NEGATIVE_THRESHOLD = 0.35;   // below this → frame is silence

// While SARA's TTS audio is playing, require higher sustained confidence
// before treating detected audio as genuine user speech (echo guard).
// Genuine barge-in at normal speaking volume will still clear this.
// Speaker leakage without headphones typically peaks at 0.55–0.65.
const VAD_ECHO_GUARD_THRESHOLD = 0.72;

// Rolling window of per-frame probabilities maintained during speech.
// At ~60ms per Silero frame, 20 frames ≈ 1200ms of context for slope analysis.
const VAD_PROB_HISTORY_LEN = 20;

// Extra silence added in onSpeechEnd for a detected trail-off, on top of
// vad-web's internal redemptionMs. Gives the user time to continue their
// thought. If they do speak, trailOffPending is cancelled immediately.
const VAD_TRAILOFF_EXTRA_MS = 1400;

// ── State ─────────────────────────────────────────────────────────────────────

let micVad        = null;   // vad.MicVAD instance; null until first startVAD()
let vadActive     = false;  // true while VAD is running
let probHistory   = [];     // per-frame speech probabilities (rolling window)
let sttInFlight   = false;  // true while /api/stt is in progress (guard flag)
let trailOffPending = false; // true during the extra trail-off wait delay;
                             // cleared immediately if user starts speaking again.

// ── Visible fail-safe ─────────────────────────────────────────────────────────
//
// The target audience may include less technical users who would otherwise
// be left talking to nothing without realising the mic stopped working.
// This function ensures any VAD failure is always visibly communicated.

function showVADError(msg) {
    vadStatus.style.backgroundColor = '#ef4444';   // vivid red = problem
    vadStatus.title = msg;
    appendMessage('System', '⚠️ ' + msg);
    console.error('[VAD]', msg);
}

// ── Trail-off detection ───────────────────────────────────────────────────────
//
// Analyses the slope of probHistory to distinguish:
//   Trail-off:  probability fades gradually (15–70% decline over the window)
//               → user is hesitating mid-thought, give more time.
//   Hard stop:  probability drops suddenly (>70% decline, or no real slope)
//               → clean sentence end, respond quickly.
//
// This replaces the old raw-energy decline ratio which was fragile under
// varying mic gain and room acoustics. Silero's probability output is
// normalised and mic-agnostic, making the slope reliable.

function detectTrailOff() {
    if (probHistory.length < 12) return false;

    // Recent tail of the speech window vs the mid-window preceding it.
    // "Recent" covers the last ~240ms; "mid" covers the 480ms before that.
    const recentSlice = probHistory.slice(-4);
    const midSlice    = probHistory.slice(-12, -4);

    const recentAvg = recentSlice.reduce((sum, p) => sum + p, 0) / recentSlice.length;
    const midAvg    = midSlice.reduce((sum, p) => sum + p, 0) / midSlice.length;

    if (midAvg <= 0.01) return false;   // no real signal in the mid window

    const declineRatio = (midAvg - recentAvg) / midAvg;

    // 15–70% gradual decline = trail-off.
    // >70% = hard stop (sudden silence, not a trailing fade).
    // <15% = still speaking strongly (no meaningful decline yet).
    return declineRatio > 0.15 && declineRatio < 0.70;
}

// ── WAV encoding ─────────────────────────────────────────────────────────────
//
// vad-web's onSpeechEnd delivers Float32Array PCM at 16kHz mono.
// The /api/stt endpoint (Groq Whisper) accepts WAV, so we encode a minimal
// RIFF/WAV here. This avoids needing a parallel MediaRecorder stream — the
// pre-roll is handled natively by vad-web's preSpeechPadMs instead.
//
// WAV structure:
//   Offset  Size  Value
//   0       4     "RIFF"
//   4       4     fileSize - 8
//   8       4     "WAVE"
//   12      4     "fmt "
//   16      4     16 (PCM subchunk size, always 16 for uncompressed PCM)
//   20      2     1  (AudioFormat = PCM)
//   22      2     numChannels (1 = mono)
//   24      4     sampleRate (16000)
//   28      4     byteRate = sampleRate × blockAlign
//   32      2     blockAlign = numChannels × bitsPerSample/8
//   34      2     bitsPerSample (16)
//   36      4     "data"
//   40      4     dataByteLen
//   44      …     int16 PCM samples (little-endian)

function encodeWAV(float32Array, sampleRate = 16000) {
    const numChannels   = 1;
    const bitsPerSample = 16;
    const blockAlign    = (numChannels * bitsPerSample) / 8;  // = 2 bytes/sample
    const byteRate      = sampleRate * blockAlign;
    const dataByteLen   = float32Array.length * 2;            // int16 = 2 bytes each
    const bufferLen     = 44 + dataByteLen;                   // header + data

    const buffer = new ArrayBuffer(bufferLen);
    const view   = new DataView(buffer);

    const writeStr = (offset, str) => {
        for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
    };

    // RIFF container
    writeStr(0, 'RIFF');
    view.setUint32(4, bufferLen - 8, true);
    writeStr(8, 'WAVE');

    // fmt subchunk
    writeStr(12, 'fmt ');
    view.setUint32(16, 16, true);           // PCM subchunk size is always 16
    view.setUint16(20, 1, true);            // AudioFormat = 1 (PCM, uncompressed)
    view.setUint16(22, numChannels, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, byteRate, true);
    view.setUint16(32, blockAlign, true);
    view.setUint16(34, bitsPerSample, true);

    // data subchunk
    writeStr(36, 'data');
    view.setUint32(40, dataByteLen, true);

    // Convert float32 [-1.0, 1.0] → signed int16 [-32768, 32767]
    let byteOffset = 44;
    for (let i = 0; i < float32Array.length; i++) {
        // Clamp to [-1, 1] first — occasional float precision overshoot
        // outside this range would produce silent/clipped audio.
        const clamped = Math.max(-1.0, Math.min(1.0, float32Array[i]));
        view.setInt16(byteOffset, clamped * 32767, true);
        byteOffset += 2;
    }

    return new Blob([buffer], { type: 'audio/wav' });
}

// ── STT dispatch ─────────────────────────────────────────────────────────────
//
// Sends a completed speech segment to the backend, with the STT in-flight
// guard preventing overlapping requests from a fast-speaking user.

async function sendSpeechToSTT(audioFloat32, trailedOff) {
    if (sttInFlight) {
        // The previous transcript hasn't come back yet. Skip this segment
        // rather than queue it — out-of-order STT responses would corrupt
        // conversation state. The user can simply speak again.
        console.warn('[VAD] STT already in-flight; skipping overlapping segment.');
        return;
    }

    sttInFlight = true;
    vadStatus.style.backgroundColor = '#3b82f6';  // blue = transcribing

    try {
        const wavBlob = encodeWAV(audioFloat32);
        const fd = new FormData();
        fd.append('file', wavBlob, 'speech.wav');

        appendMessage('System', 'Transcribing...');
        const res  = await fetch('/api/stt', { method: 'POST', body: fd });
        const data = await res.json();

        if (data.text && data.text.trim()) {
            sendUserMessage(data.text, '🗣️ ' + data.text, { trailed_off: trailedOff });
        } else {
            // Whisper returning empty/junk is normal for very short or very
            // quiet segments. Swallow silently rather than surfacing an error.
            console.debug('[VAD] empty transcript from STT, discarding.');
        }
    } catch (err) {
        console.error('[VAD] STT error:', err);
        appendMessage('System', 'Transcription failed. Please try again.');
    } finally {
        sttInFlight = false;
        // Return status indicator to listening state if VAD is still active.
        if (vadActive) vadStatus.style.backgroundColor = '#eab308';
    }
}

// ── VAD start ─────────────────────────────────────────────────────────────────

async function startVAD() {
    // If the instance already exists (e.g. user toggled off then back on),
    // just resume instead of re-initialising the whole model.
    if (micVad) {
        vadActive = true;
        micVad.start();
        vadStatus.style.backgroundColor = '#eab308';
        return;
    }

    vadStatus.style.backgroundColor = '#eab308';
    appendMessage('System', 'Loading voice detection model…');

    try {
        micVad = await vad.MicVAD.new({

            // ── Asset paths ──────────────────────────────────────────────────
            // All VAD assets are pre-downloaded to static/vad/ by
            // download_vad_assets.py so the browser never reaches CDN.
            // vad-web@0.0.22 + onnxruntime-web@1.14.0:
            //   - No proxy worker → onnxWASMBasePath can be a relative path
            //     (ORT loads WASM via plain fetch(), no blob URL created)
            //   - numThreads=1 → uses ort-wasm-simd.wasm, no SharedArrayBuffer,
            //     no threading worker at all — clean single-thread operation
            baseAssetPath:    '/static/vad/',
            onnxWASMBasePath: '/static/vad/',

            // ── ORT: single-thread mode ───────────────────────────────────────
            ortConfig: (ort) => {
                ort.env.wasm.numThreads = 1;
                ort.env.wasm.wasmPaths  = '/static/vad/';
            },

            // ── Speech thresholds ─────────────────────────────────────────────
            positiveSpeechThreshold: VAD_POSITIVE_THRESHOLD,
            negativeSpeechThreshold: VAD_NEGATIVE_THRESHOLD,

            // ── Timing ────────────────────────────────────────────────────────
            //
            // preSpeechPadMs — built-in pre-roll. Frames captured before
            // onSpeechStart fires are prepended to the audio in onSpeechEnd.
            // 600ms ensures the first word/syllable is never clipped even if
            // the user begins speaking right as they trigger the confirm window.
            // Replaces the old 500ms DelayNode + MediaStreamDestination trick.
            preSpeechPadMs: 600,

            // minSpeechMs — minimum sustained speech duration before the
            // library considers speech valid and fires onSpeechStart.
            // This IS the "confirm window" from the spec (150–250ms).
            // Short sounds — coughs, chair creaks, brief mic bumps — that
            // don't sustain for this long are discarded via onVADMisfire.
            minSpeechMs: 200,

            // redemptionMs — how long Silero must report silence continuously
            // before speech is considered ended. This is the BASE silence
            // timeout; an additional VAD_TRAILOFF_EXTRA_MS is added in
            // onSpeechEnd when a trail-off is detected.
            // 900ms ≈ a normal breath/pause between sentences.
            redemptionMs: 900,

            // ── Custom mic stream with track-ended detection ──────────────────
            // Override getStream to intercept the mic track and attach a
            // 'ended' listener. If the device disconnects (Bluetooth headset
            // pulled out, USB mic unplugged), we surface a visible error
            // immediately instead of failing silently.
            getStream: async () => {
                const stream = await navigator.mediaDevices.getUserMedia({
                    audio: {
                        channelCount:     1,
                        echoCancellation: true,
                        autoGainControl:  true,
                        noiseSuppression: true,
                    },
                });

                stream.getTracks().forEach(track => {
                    track.addEventListener('ended', () => {
                        showVADError(
                            'Microphone disconnected. Toggle VAD off, reconnect your mic, then toggle back on.'
                        );
                        vadActive = false;
                        vadToggle.checked = false;
                    });
                });

                return stream;
            },

            // ── Per-frame callback ────────────────────────────────────────────
            // probabilities.isSpeech is the raw Silero model output (0–1).
            // We maintain a rolling window of these values for trail-off
            // detection and for the echo-guard's smoothed confidence check.
            onFrameProcessed: (probabilities) => {
                probHistory.push(probabilities.isSpeech);
                if (probHistory.length > VAD_PROB_HISTORY_LEN) probHistory.shift();
            },

            // ── Speech start ──────────────────────────────────────────────────
            // This fires AFTER minSpeechMs of sustained speech — i.e. the
            // confirm window has already passed. Single-frame blips that don't
            // sustain for 200ms will never reach this callback.
            onSpeechStart: () => {
                // Cancel any pending trail-off delayed send. The user started
                // speaking again, so the old segment should not be dispatched.
                trailOffPending = false;

                // ── Self-echo guard ───────────────────────────────────────────
                // Check whether this "speech" is genuine user input or just
                // SARA's TTS leaking back into the mic (common without
                // headphones even with echoCancellation:true).
                // We look at the smoothed average over the last ~360ms of
                // frames (6 frames × ~60ms each). If it's below the higher
                // echo-guard threshold, assume it's leakage and ignore it.
                // Genuine speech at normal conversation volume will typically
                // average 0.75–0.95 over this window.
                if (isPlayingQueue) {
                    const guardWindow = probHistory.slice(-6);
                    const avgProb = guardWindow.length
                        ? guardWindow.reduce((sum, p) => sum + p, 0) / guardWindow.length
                        : 0;

                    if (avgProb < VAD_ECHO_GUARD_THRESHOLD) {
                        // Below the higher threshold while SARA is speaking.
                        // Likely echo or room noise — do NOT interrupt.
                        console.debug('[VAD] echo guard blocked (avgProb=' + avgProb.toFixed(2) + ')');
                        return;
                    }

                    // Confident genuine barge-in. Stop SARA's current audio.
                    audioQueue          = [];
                    currentWordBoundaries = [];
                    if (globalAudio && !globalAudio.paused) {
                        globalAudio.pause();
                    }
                    ws.send(JSON.stringify({ type: 'text', content: 'interrupt' }));
                    appendMessage('System', 'SARA interrupted.');
                }

                // Reset probability history so onSpeechEnd's trail-off check
                // only sees slope data from THIS utterance, not the previous one.
                probHistory = [];

                vadStatus.style.backgroundColor = '#22c55e';  // green = user speaking
            },

            // ── Speech end ────────────────────────────────────────────────────
            // audio: Float32Array, mono 16kHz — the complete recorded segment
            // INCLUDING the preSpeechPadMs pre-roll prepended by vad-web.
            // This fires AFTER redemptionMs of continuous silence.
            onSpeechEnd: async (audio) => {
                vadStatus.style.backgroundColor = '#eab308';  // back to listening

                // Analyse the probability slope from the last utterance.
                // If it faded gradually → trail-off → wait before sending.
                // If it dropped suddenly → clean stop → send now.
                const isTrailOff = detectTrailOff();

                if (isTrailOff) {
                    // Mark pending and wait. If the user starts speaking again
                    // during this window, onSpeechStart will clear trailOffPending
                    // and we abort the send below, discarding this segment in
                    // favour of the new one that will follow.
                    trailOffPending = true;
                    await new Promise(resolve => setTimeout(resolve, VAD_TRAILOFF_EXTRA_MS));

                    if (!trailOffPending) {
                        // User resumed speaking — the new utterance will be sent
                        // instead. Drop this trail-off segment.
                        return;
                    }
                    trailOffPending = false;
                }

                await sendSpeechToSTT(audio, isTrailOff);
            },

            // ── VAD misfire ───────────────────────────────────────────────────
            // Fires instead of onSpeechEnd when a speech segment is detected
            // but shorter than minSpeechMs (cough, brief noise, etc.).
            // Just reset the status indicator — no STT call needed.
            onVADMisfire: () => {
                vadStatus.style.backgroundColor = '#eab308';
                console.debug('[VAD] misfire — segment too short, discarded.');
            },
        });

        vadActive = true;
        micVad.start();
        appendMessage('System', 'Voice detection active.');

    } catch (err) {
        showVADError('VAD failed to start: ' + (err.message || String(err)));
        vadToggle.checked = false;
        micVad    = null;
        vadActive = false;
    }
}

// ── VAD stop ──────────────────────────────────────────────────────────────────

function stopVAD() {
    if (micVad) micVad.pause();
    vadActive      = false;
    sttInFlight    = false;
    trailOffPending = false;
    probHistory    = [];
    vadStatus.style.backgroundColor = '#475569';  // grey = off
}

// ── Tab visibility recovery ───────────────────────────────────────────────────
//
// Browsers may suspend the AudioContext when the tab is backgrounded.
// Auto-pause on hide and auto-resume on show so the user doesn't need
// to toggle VAD off/on just because they switched tabs.

document.addEventListener('visibilitychange', () => {
    if (!micVad || !vadActive) return;
    if (document.hidden) {
        micVad.pause();
    } else {
        try {
            micVad.start();
        } catch (err) {
            showVADError('VAD could not resume after tab switch. Toggle off and on again.');
        }
    }
});

// ── Toggle handler ────────────────────────────────────────────────────────────

vadToggle.addEventListener('change', () => {
    if (vadToggle.checked) {
        startVAD();
    } else {
        stopVAD();
    }
});

// ── Model select ──────────────────────────────────────────────────────────────
waifuSelect.addEventListener('change', () => loadModel(waifuSelect.value));

// ── Init ──────────────────────────────────────────────────────────────────────
window.onload = () => { loadModel(waifuSelect.value); };