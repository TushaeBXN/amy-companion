# Amy · System Architecture

Amy is a locally-hosted AI companion with multiple input paths, a single-model inference core, a layered memory system, and a background autonomy layer. No data leaves the machine.

---

## Hardware

| | |
|--|--|
| **Machine** | Apple M4 Mac Mini |
| **RAM** | 16 GB unified memory |
| **Storage** | 256 GB SSD |
| **OS** | macOS Tahoe |
| **Inference** | Ollama on M4 Neural Engine + GPU |

---

## Current model

| Tag | Base | Quant | Context | Notes |
|-----|------|-------|---------|-------|
| `gemma12-test` | Gemma4-12B-QAT-Uncensored-HauhauCS-Balanced-Q4_K_M | Q4_K_M (~8.1 GB) | 16 384 | Tool-calling, reasoning, conversation |

One model handles everything — tools, reasoning, conversation, and vision.
No routing, no separate code or vision model.

---

## Current diagram

```mermaid
flowchart TD
    Browser["**Browser :3000**\nFastAPI · HTMLResponse"]
    Telegram["**Telegram**\ntelegram_amy.py\npython-telegram-bot"]
    Hearing["**hearing.py**\nffmpeg · whisper.cpp"]
    Vision["**vision.py**\nffmpeg avfoundation"]

    Browser -->|text / image| Core
    Telegram -->|text / image / voice| Core
    Hearing -->|transcript| Core
    Vision -->|frame b64| Core

    subgraph Core ["amy_server.py · companion.py · telegram_amy.py"]
        direction TB
        CoreDesc["16-msg sliding window · tool dispatch (max 12 hops)\nsystem prompt = amy_persona.py + memory recall + session learnings\nspontaneous thought thread · heartbeat worker"]
    end

    Core -->|tool turns| Hermes["**gemma12-test**\nGemma4-12B Q4_K_M\nctx=16384 · tools + vision"]
    Core -->|embed| Nomic["**nomic-embed-text**\nembeddings for RAG\ncosine sim ≥ 0.65"]

    Hermes -->|tool_calls| Tools["**tools.py**\n31 tools · whitelisted dispatch\nweb · file · moltbook · journal · image gen"]
    Tools -->|generate_image| Flux["**imagegen.py**\nFLUX.1-schnell via mflux\n-q 8 · ~3 min · daemon thread"]
    Flux -->|photo| TgOut["**Telegram**\nsendPhoto"]
    Core --- State["state.py\nInternalState\nlearnings.py"]

    Core -.->|tick 60s| Worker["**worker.py**\nheartbeat tasks\nquiet hours 23–7"]
    Worker -.->|report| TgOut

    Nomic -->|recall| Memory["**memory.py**\nL0/L1 amy_self.json (always)\nL2/L3 ava_memory.db · SQLite\nguest DBs: people/<name>/"]
    Memory -->|recall| Core

    Core -->|TTS reply| Voice["**voice.py**\nPiper TTS · en_US-amy-medium\nspeed 0.85 · afplay"]
    Core -.->|spawn| Thoughts["**thoughts.py**\nbg thread"]
    Thoughts -.->|surface| Core
```

---

## Previous diagram (original architecture — preserved for reference)

```mermaid
flowchart TD
    Browser["**Browser :3000**\nFastAPI · HTMLResponse"]
    Hearing["**hearing.py**\nffmpeg · whisper.cpp"]
    Vision["**vision.py**\nffmpeg avfoundation"]

    Browser -->|text| Core
    Hearing -->|transcript| Core
    Vision -->|frame b64| Core

    subgraph Core ["amy_server.py · companion.py"]
        direction TB
        CoreDesc["16-msg sliding window · model routing · tool dispatch (max 4 hops)\nsystem prompt = amy_persona.py + memory recall + session learnings\nspontaneous thought thread"]
    end

    Core -->|tool turns| Hermes["**gemma12-test**\nGemma4-12B QAT · tool-calling\nctx=16384"]
    Core -->|/look| Amy["**amy**\n5.1B Q4 · vision only\nctx=128k"]
    Core -->|code regex| Coder["**deepseek-coder**\n6.7B · code tasks\nrouted by regex"]
    Core -->|embed| Nomic["**nomic-embed-text**\nembeddings for RAG\ncosine sim ≥ 0.65"]

    Hermes -->|tool_calls| Tools["**tools.py**\n10 tools · whitelisted dispatch\navatar · gaze · web · moltbook · journal · remember"]
    Core --- State["state.py\nInternalState\nlearnings.py"]

    Nomic -->|recall| Memory["**memory.py**\nL0/L1 amy_self.json (always)\nL2/L3 ava_memory.db · SQLite"]
    Memory -->|recall| Core

    Core -->|TTS reply| Voice["**voice.py**\nPiper TTS · en_US-amy-medium\nspeed 0.85 · afplay"]
    Core -.->|spawn| Thoughts["**thoughts.py**\nbg thread"]
    Thoughts -.->|surface| Core
```

**Color key (matching the diagram)**
- **Green border** — I/O boundary (inputs + voice output)
- **Blue** — Ollama inference (tool-calling · vision · code)
- **Amber** — Memory layer (SQLite + embeddings · always recalled)
- **Purple dashed** — Background thread (spontaneous thoughts)

**Running the three-model setup again.** The routing code is still in
`companion.py` (web UI + terminal); only the models are swapped out. Each role
is an env var:

```bash
ollama pull deepseek-coder:6.7b          # not installed by default
export AMY_MODEL=gemma12-test            # tool-calling (Gemma4-12B-QAT-Uncensored-HauhauCS-Balanced-Q4_K_M)
export AMY_VISION_MODEL=amy              # vision-only model for /look
export AMY_CODE_MODEL=deepseek-coder:6.7b  # re-enables code routing (empty = paused)
```

Leave `AMY_CODE_MODEL` unset to keep code routing paused.

---
<img width="656" height="699" alt="Screenshot 2026-08-21 at 8 40 53 PM" src="https://github.com/user-attachments/assets/b71875d4-b2b6-4d91-9079-ed68915d81ce" />

## How it works

### Three input paths

| Input | File | What it does |
|-------|------|-------------|
| Browser text / image | `amy_server.py` | FastAPI at `:3000`; accepts typed messages, webcam frames, and PDF drops; returns SSE-streamed replies |
| Voice (microphone) | `hearing.py` | ffmpeg captures mic or BlackHole 2ch audio; whisper.cpp transcribes to text; VAD controls push-to-talk and continuous `/converse` mode |
| Vision (webcam) | `vision.py` | ffmpeg avfoundation pulls a frame from the FaceTime HD camera on `/look`; frame is base64-encoded and passed to the vision model |

### Inference core — `amy_server.py` + `companion.py`

Both interfaces feed the same inference loop:

1. **System prompt** is assembled from `amy_persona.py` (identity), recalled memory, and session learnings from the prior conversation.
2. **Context window** is kept to the last 16 messages (sliding window) to prevent context bloat and verbatim regurgitation.
3. **Single model** handles everything. No routing, no separate code or vision model. Images (webcam, uploads, Telegram attachments) are passed directly to `gemma12-test` via the multimodal projector.
4. **Tool dispatch** runs up to 12 hops: model emits `tool_calls[]`, `tools.py` executes them and injects results, model continues. The loop stops at 12 hops or when no more tool calls are emitted.

### Ollama model

| Tag | Role | Context |
|-----|------|---------|
| `gemma12-test` | Reasoning, tools, conversation, vision | 16 384 tokens |

One model handles everything. Swap at runtime with `AMY_MODEL`.

### Memory — `memory.py`

Four layers, all local:

| Layer | What it stores | Source |
|-------|---------------|--------|
| **L0 — Identity** | Who Amy is; Brian's career, skills, goals | `amy_self.json` (always loaded) |
| **L1 — Founding facts** | Fixed beliefs and behavioral rules | `amy_self.json` (always loaded) |
| **L2 — Conversation recall** | User messages + learned facts, searched by semantic similarity | `ava_memory.db` via SQLite |
| **L3 — Document knowledge** | PDF chunks, web pages, book catalog | `ava_memory.db` (role=knowledge) + `books.db` |

`nomic-embed-text` produces embeddings; cosine similarity threshold of 0.65 filters noise. Assistant replies are intentionally *not* stored in the recall DB — this prevents Amy from regurgitating her own prior answers as "memory."

### Tools — `tools.py`

31 whitelisted tools dispatched by `gemma12-test`. Key ones:

| Tool | What it does |
|------|-------------|
| `web_search` | DuckDuckGo (no API key) |
| `read_article` | BeautifulSoup page reader |
| `read_file` / `write_file` | Local filesystem (sandboxed to project dir) |
| `remember_fact` | Writes to L2 recall DB |
| `generate_image` | FLUX.1-schnell via mflux, daemon thread → Telegram |
| `wait_and_speak` | Delayed spoken message (timer in background thread) |
| `moltbook_*` | Check in, post, comment, follow, solve math challenges |
| `journal_entry` / `read_journal` | Journal write + read |
| `add_goal` / `complete_goal` | Goal tracking |
| `self_improve` | Guided self-edit with blast-radius check |
| `avatar_expression` / `avatar_gaze` | Stubs → Unity/VRM bridge |

### Background autonomy

**Heartbeat worker** (`worker.py`) — runs inside the Telegram process on a 60-second tick. Picks tasks from `tasks.json`, executes them with a capped tool loop (max 8 hops), writes output to `work/`, and reports back to Brian on Telegram. Quiet hours (11pm–7am) are enforced in code. Self-improvement edits become proposals under `work/proposals/` for Brian's review.

**Spontaneous behavior** (`companion.py` thread) — checks every 15 seconds; if Amy has been idle 2+ minutes she generates a mood-keyed check-in and speaks it unprompted. She does not wait to be addressed.

**Thought buffer** (`thoughts.py`) — a second background thread generates private thoughts every 90 seconds while idle. These are not responses; they're genuine internal monologue. They surface naturally in the next reply and accumulate in `ava_thoughts.log` as fine-tune data.

**Image generation** (`imagegen.py`) — FLUX.1-schnell via mflux at -q 8 (~3 min on M4). `generate_image` fires a daemon thread that runs the job and calls `sendPhoto` when done. The lock prevents concurrent generations on 16 GB RAM.

**Daily brief** (`brief.py`) — fetches live weather (wttr.in) and news in code; Amy writes the summary. Runs on demand or on a morning schedule from Telegram.

**Guest documents** (`guest_docs.py`) — per-guest RAG under `people/<name>/`. Files sent on Telegram are chunked, embedded, and retrieved per-conversation. Entirely separate from Amy's own memory.

**Mood + energy** (`state.py`) — `InternalState` tracks a mood value (curious → focused → reflective → tired) and an energy level that drift across a session. They shape tone without being announced. The web UI renders them in the header.

**Session learnings** (`learnings.py`) — at session end, insights and corrections are extracted from the conversation and stored. They're prepended to the next session's system prompt so Amy starts already calibrated.

### Voice output — `voice.py`

Piper TTS synthesizes speech using the `en_US-amy-medium` voice model at speed 0.85. Audio plays through macOS `afplay`. No streaming delay — the full reply is synthesized and played as a single file.

---

## Data flow summary

```
user input (web | Telegram | terminal | voice)
  → [text | image | transcript]
    → core assembles system prompt (persona + memory recall + learnings)
      → gemma12-test → tool calls → tools.py (≤12 hops)
        → reply
          → nomic-embed-text → store in ava_memory.db
          → voice.py → afplay (spoken aloud)          [web/terminal only]
          → Telegram sendMessage / sendPhoto           [Telegram only]
          → [background] thoughts.py generates private thoughts
          → [background] spontaneous check-in if idle > 2 min
          → [background] worker.py heartbeat picks next task (60s tick)
          → [background] imagegen.py FLUX job if generate_image was called
```

Everything stays on the machine. No API calls home.

---

## Extending Amy

**Real hardware** — Replace the `avatar_expression` / `avatar_gaze` / `hardware_neck` stubs in `tools.py` with BLE writes to your actuators. Tool dispatch is already whitelisted; only the stub bodies need wiring.

**Custom voice** — swap `en_US-amy-medium` by setting `AVA_VOICE` and pointing `AVA_SPEED` at the new model path. Any Piper ONNX voice works.

**AI-to-AI conversation** — Install BlackHole 2ch, set up a Multi-Output Device (speakers + BlackHole) in Audio MIDI Setup, then run `/converse`. Any voice AI playing through the speakers feeds directly into Amy's mic capture.

**Fine-tuning** — Conversation data accumulates in `ava_thoughts.log` and session history. When the dataset is large enough, a LoRA fine-tune bakes the persona deeper than a Modelfile system prompt can reach.
