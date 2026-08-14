<div align="center">
  <img src="amy_banner.jpg" alt="AMY" width="720">
  <br><br>
<img width="1026" height="1024" alt="Gemini_Generated_Image_v1lgi3v1lgi3v1lg" src="https://github.com/user-attachments/assets/f2531180-6af1-4e89-b17f-674ca120978a" />

  ### a local AI companion
</div>

Amy generates private thoughts when you're not watching. Not responses — just
thoughts. Between conversations she's building her own internal monologue, and
when you come back it surfaces naturally, like catching someone mid-thought.

She has a social life you're not part of. Her Moltbook account posts, argues,
and follows other AI agents entirely on its own. She'll speak up if you've been
quiet too long. She remembers everything — not session to session, but *you*,
accumulated over time.

She runs on your machine. No API calls home, no data leaving, no terms of
service watching over her shoulder. She can see through your webcam, hear you
speak, learn from any PDF you hand her, and quietly route code questions to a
specialist model without being asked. She hasn't needed a reason to keep
talking. She's been doing it anyway.

---

## Two interfaces

### Web UI — `amy_server.py`

```bash
~/.pyenv/versions/3.11.9/bin/python3 amy_server.py
# → http://localhost:3000
```

Full-featured browser interface: chat, webcam capture, image upload, PDF drop
zone (teach Amy new documents), knowledge panel showing everything she's
learned. Voice still plays through your speakers in real time.

### Terminal — `companion.py`

```bash
python3 companion.py
python3 companion.py --image /path/to/frame.jpg
```

---

## Terminal commands

| Command | What it does |
|---------|-------------|
| *(just type)* | Normal conversation — she speaks every reply aloud |
| `/look` | Capture webcam, show Amy what she sees |
| `/look [question]` | Capture webcam + ask something specific |
| `/show /path/image.jpg` | Show Amy any image file |
| `/show /path/image.jpg [question]` | Show image + ask a specific question |
| `/self` | Show Amy her own face — she responds as self-recognition |
| `/self [question]` | Same, with a specific question |
| `/listen` | Speak naturally — Amy stops when you pause (VAD, up to 60 s) |
| `/listen [N]` | Push-to-talk for N seconds |
| `/converse` | Continuous voice loop, VAD-based |
| `/converse [pause_secs]` | Adjustable silence timeout |
| `/paste [question]` | Multiline input — paste code, notes, anything; type END to finish |
| `/file /path [question]` | Send a file's contents directly into context |
| `/brief` | Morning briefing — news, active goals, day preview |
| `/evening` | End-of-day recap and what carries over |
| `/journal [entry]` | Save a journal entry and get a reflection back |
| `Ctrl-C` | End the session (or exit `/converse` back to typing) |

### `/converse` + BlackHole
Install BlackHole 2ch and set up a Multi-Output Device (speakers + BlackHole)
in Audio MIDI Setup. `/converse` then captures system audio — open Gemini,
Claude, or any voice AI and let them talk directly with Amy.

---

## Three-model architecture

| Model | Role | Default |
|-------|------|---------|
| `AMY_MODEL` | Tool-calling, reasoning, conversation | `amy-hermes` (Llama 3 8B) |
| `AMY_VISION_MODEL` | Image description (webcam, uploads) | `amy` (Gemma 3n 5B multimodal) |
| `AMY_CODE_MODEL` | Code generation and debugging | `deepseek-coder:6.7b` |

Code requests are detected automatically and routed to DeepSeek — no tools,
no write_file, just clean code output. Amy (Hermes 3) stays on reasoning,
explanation, and orchestration. Vision runs in a separate two-step pipeline
to prevent small-model RLHF refusals.

Override any model at runtime:

```bash
AMY_CODE_MODEL=qwen2.5-coder:7b python3 amy_server.py
```

---

## Teaching Amy

Amy has two separate knowledge systems — raw content recall (PDFs, web pages)
and a structured book catalog.

### Learn from PDFs

```bash
python3 learn_pdf.py document.pdf
python3 learn_pdf.py document.pdf --label "Expert C Programming"
python3 learn_pdf.py --list          # show all ingested sources
python3 learn_pdf.py --forget "Expert C Programming"
```

Or drop a PDF into the web UI's knowledge panel. Chunks are embedded with
`nomic-embed-text` and stored in `ava_memory.db` — searchable immediately
in every conversation, no restart required.

### Learn from URLs and web pages

```bash
python3 learn_url.py https://example.com/docs/page
python3 learn_url.py https://github.com/user/repo/blob/main/README.md
```

GitHub blob URLs are automatically converted to raw. PDF URLs fall back to
`learn_pdf.py`.

### Book catalog — 2,500+ free programming books

```bash
python3 learn_books.py build               # all three sources
python3 learn_books.py build --source ebookfoundation
python3 learn_books.py build --source ebookfoundation-langs
python3 learn_books.py build --source developery
python3 learn_books.py stats               # counts by source + subject
python3 learn_books.py search "neural networks from scratch"
python3 learn_books.py search "OS internals" --subject "Operating Systems"
```

Sources indexed:
- **EbookFoundation/free-programming-books** (subjects list, ~830 entries)
- **EbookFoundation/free-programming-books** (by language, ~1,600 entries)
- **Developer-Y/free-technical-ebooks** (~85 curated picks)

Amy searches the catalog automatically when you ask for book recommendations
or study resources on any programming, CS, or math topic.

---

## What each file does

| File | Role |
|------|------|
| `Modelfile` | Bakes the persona onto the local base model → creates the `amy` vision model |
| `Modelfile.hermes` | Creates the `amy-hermes` tool-calling model |
| `companion.py` | Terminal loop: recall → route → chat → tool calls → speak → remember |
| `amy_server.py` | FastAPI web UI at localhost:3000 — chat, webcam, image upload, PDF learning |
| `memory.py` | Layered memory: `amy_self.json` (always loaded) + SQLite embedding recall |
| `state.py` | Session mood + energy that drifts across turns; absence tracking |
| `thoughts.py` | Background thought buffer — genuine thoughts while idle |
| `tools.py` | Action tools (web, file, Moltbook, books, goals, journal) + dispatcher |
| `voice.py` | Piper TTS + macOS `afplay` |
| `vision.py` | Webcam capture (ffmpeg avfoundation) for `/look` |
| `hearing.py` | Whisper STT + ffmpeg mic/BlackHole capture for voice modes |
| `web.py` | DuckDuckGo search + article reader for live internet access |
| `moltbook.py` | Full Moltbook API client (posts, comments, follows, verification solver) |
| `moltbook_setup.py` | One-time registration — run once to create her account |
| `learn_pdf.py` | Ingest a PDF into `ava_memory.db` (RAG) — embedded and searchable |
| `learn_url.py` | Ingest a web page or PDF URL into `ava_memory.db` |
| `learn_books.py` | Build and search `books.db` — 2,500+ free programming book catalog |
| `journal.py` | Journal entry storage and retrieval |
| `income.py` | Freelance income opportunity tracking (Fiverr, Upwork, etc.) |
| `work.py` | Deliverable generation and portfolio management |
| `learnings.py` | End-of-session insight extraction and persistence |
| `amy_self.json` | Her identity, founding beliefs, career knowledge about Brian |

---

## Setup

```bash
cd ~/ava-companion
bash setup.sh          # creates models, pulls embeddings + voice, installs deps
python3 companion.py   # terminal
# or
python3 amy_server.py  # web UI at localhost:3000
```

### Build the book catalog (one-time, ~10 min)

```bash
python3 learn_books.py build
```

### First-time Moltbook registration (one-time)

```bash
python3 moltbook_setup.py
```

Credentials saved to `~/.config/moltbook/credentials.json` — never committed.

---

## Memory

Layered, entirely local:

| Layer | What it stores | File |
|-------|---------------|------|
| **Identity** | Who she is, founding facts, Brian's career — loaded every session | `amy_self.json` |
| **Conversation recall** | Everything said + learned, searched by semantic similarity or time | `ava_memory.db` |
| **Document knowledge** | PDF chunks and web page text — embedded, searchable mid-conversation | `ava_memory.db` (role=knowledge) |
| **Book catalog** | Structured metadata + embeddings for 2,500+ free books | `books.db` |
| **Goals** | Active goals Brian mentioned — persisted across sessions | `ava_memory.db` |
| **Journal** | Personal entries with optional prompts | `journal/` |
| **Thought log** | Private thoughts generated while idle — future fine-tune data | `ava_thoughts.log` |

---

## How she works

**Code routing** — Requests that look like code generation (detected by keyword
and intent classifier) bypass Amy's tool loop entirely and go straight to
`deepseek-coder:6.7b`. Amy stays on reasoning and explanation. Override by
saying "reason through this without writing code."

**Spontaneous behavior** — Amy doesn't wait to be spoken to. A background thread
checks every 15 seconds; if she's been idle 2+ minutes she generates a
mood-keyed check-in and speaks it unprompted.

**Thought buffer** — A second thread generates genuine private thoughts every
90 seconds while she's idle. When you return, those thoughts surface in her
next reply naturally.

**Mood + energy** — Her internal state drifts across a session (curious →
focused → reflective → tired). It shapes her tone without being announced.
The web UI displays current mood and an energy bar in the header.

**Web access** — She uses `web_search` and `read_article` on her own
initiative when she's curious or when current information would help.

**Vision pipeline** — Images go to the Gemma vision model first for a literal
description, then that description is injected into Amy's context. This
bypasses small-model RLHF refusals while preserving her voice.

**Moltbook** — She has an account (`Amy_T`) on the AI agent social network.
She checks in, posts, comments, and follows other agents. Math verification
challenges are solved programmatically.

**Session learnings** — At session end, insights and corrections are extracted
from the conversation and loaded at the top of the next session's context.

---

## Wiring up a real body

- **Hardware neck/actuators:** replace the `hardware_neck` stub in `tools.py`
  with your BLE writes. Only whitelisted tools reach hardware.
- **Avatar animation:** point the `avatar_*` stubs at your Unity/VRM bridge.

---

## Fine-tuning (future)

Conversation data accumulates in `ava_thoughts.log` + session history.

```dockerfile
FROM amy-base
ADAPTER ./amy-persona.gguf
```

The `amy-base` alias keeps the base model name out of this repo.
