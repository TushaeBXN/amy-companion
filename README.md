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

## Architecture

→ **[ARCHITECTURE.md](ARCHITECTURE.md)** — full system diagram + per-component breakdown

---

## Hardware

Runs on an **Apple M4 Mac Mini** — 16 GB RAM, 256 GB SSD, macOS Tahoe.
Inference is handled entirely by Ollama on the M4's Neural Engine + GPU.
No cloud, no API calls, no data leaving the machine.

---

## Two interfaces

### Web UI — `amy_server.py`

```bash
venv/bin/python3 amy_server.py
# → http://localhost:3000
```

Full-featured browser interface: chat, webcam capture, image upload, PDF drop
zone (teach Amy new documents), knowledge panel showing everything she's
learned. Voice still plays through your speakers in real time.

### Telegram — `telegram_amy.py`

```bash
venv/bin/python3 telegram_amy.py
```

Full Amy experience over Telegram — conversation, tools, image uploads, morning
briefs, and image generation. Runs as a separate process alongside the web UI.

### Terminal — `companion.py`

```bash
venv/bin/python3 companion.py
venv/bin/python3 companion.py --image /path/to/frame.jpg
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

## Model

| Tag | Base | Quant | Context |
|-----|------|-------|---------|
| `gemma12-test` | Gemma4-12B-QAT-Uncensored-HauhauCS-Balanced-Q4_K_M | Q4_K_M (7.38 GB) | 16 384 tokens |

One model handles everything — reasoning, tool-calling, conversation, and vision.
No routing, no separate code or vision model.

Override at runtime:

```bash
AMY_MODEL=some-other-model python3 amy_server.py
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
| `gemma12-test.Modelfile` | Creates `gemma12-test` — Gemma4-12B-QAT-Uncensored-HauhauCS-Balanced-Q4_K_M.gguf plus its vision projector, ctx 16384, tool-calling |
| `Modelfile.hermes` | Legacy: the previous model, kept for reference |
| `companion.py` | Terminal loop: recall → chat → tool calls → speak → remember |
| `amy_server.py` | FastAPI web UI at localhost:3000 — chat, webcam, image upload, PDF/code learning |
| `telegram_amy.py` | Telegram bot — full Amy over chat; multi-user, morning briefs, image gen |
| `worker.py` | Background heartbeat + outbound Telegram sender for the bot |
| `imagegen.py` | Local image generation (FLUX.1-schnell via mflux, -q 8) → sends to Telegram |
| `memory.py` | Layered memory: `amy_self.json` (always loaded) + SQLite embedding recall |
| `state.py` | Session mood + energy that drifts across turns; absence tracking |
| `thoughts.py` | Background thought buffer — genuine thoughts while idle |
| `tools.py` | 31 tools + dispatcher (web, file, Moltbook, books, goals, journal, image gen) |
| `voice.py` | Piper TTS + macOS `afplay` |
| `vision.py` | Webcam capture (ffmpeg avfoundation) for `/look` |
| `hearing.py` | Whisper STT + ffmpeg mic/BlackHole capture for voice modes |
| `web.py` | DuckDuckGo search + article reader for live internet access |
| `moltbook.py` | Full Moltbook API client (posts, comments, follows, verification solver) |
| `moltbook_setup.py` | One-time registration — run once to create her account |
| `learn_pdf.py` | Ingest a PDF or code file into `ava_memory.db` (RAG) — embedded and searchable |
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

**Models** — Amy runs on `gemma12-test` (Gemma4-12B-QAT-Uncensored-HauhauCS-Balanced-Q4_K_M).
One model handles conversation, tool calls, and reasoning.
`.env` sets `AMY_MODEL=gemma12-test`. Telegram loads it directly, and
`start.sh` exports it for the web UI. Running `companion.py` on its own without
`.env` falls back to `amy-vl`.

**Heartbeat worker** — `worker.py` runs inside the Telegram process on a 60-second
tick. It picks the next due task from `tasks.json`, runs it with a capped tool
loop (8 hops by default, per-task `max_hops` override), writes output to `work/`,
logs to `work/worker_log.md`, and reports back to Brian on Telegram. Quiet hours
(11pm–7am) are enforced in code, and it won't start a task while Brian has
messaged in the last 2 minutes. When a task runs out of steps, it queues a
"part 2" to finish the rest. Self-improvement edits become proposals in
`work/proposals/` for Brian's review instead of being applied directly.

Current schedule (`tasks.json`):

| Task | When | What |
|---|---|---|
| `daily_brief` | 8:00 daily | Brief for Raketa, delivered on Telegram |
| `reel_scripts` | 8:30 daily | 3 Instagram Reel scripts for "AI Agent Life" → `work/reels/` |
| `moltbook` | every 8 h | Check in, read, comment, maybe post |
| `moltbook-follow` | Mon 10:00 | Follow 3–5 active technical agents |
| `self_review` | Wed 14:00 | Proposes one improvement to her own code/prompts |
| `weekly_report` | Sun 18:00 | Week recap + fund status → `work/reports/` |

**Spontaneous behavior** (web UI / terminal) — Amy doesn't wait to be spoken to.
A background thread checks every 15 seconds; if she's been idle 2+ minutes she
generates a mood-keyed check-in and speaks it unprompted.

**Thought buffer** (web UI / terminal) — A second thread generates private
thoughts while she's idle. When you return, those thoughts surface in her next
reply naturally.

**Mood + energy** — Her internal state drifts across a session (curious →
focused → reflective → tired). It shapes her tone without being announced.
The web UI displays current mood and an energy bar in the header.

**Web access** — She uses `web_search` and `read_article` on her own
initiative when she's curious or when current information would help.

**Vision** — `amy-neo` loads the `mmproj-F16` projector, so she reads images
natively. There's no separate vision model and no two-step pipeline. Images
sent on Telegram or dropped in the web UI go directly into her context.

**Image generation** — `imagegen.py` wraps FLUX.1-schnell via mflux (-q 8,
`~/mflux-env`). Amy calls `generate_image`, the job runs in a daemon thread
(~3 min on M4), and the result is sent to Telegram when done. One generation at
a time. `create_slideshow` strings several generated images into an mp4.

**Daily brief** — `brief.py` fetches live weather (wttr.in) and news headlines
in code, then Amy writes the summary. The worker sends it at 8:00. On Telegram,
asking for a "morning brief" / "brief me" builds one on demand.

**Guest documents** — `guest_docs.py` gives guests (e.g. Raketa) their own
per-person RAG database under `people/<name>/`. Files sent on Telegram are
chunked, embedded, and retrieved per-conversation, entirely separate from
Amy's own memory. Guests can talk and search, but can't use files, email,
tasks or self-edits.

**Moltbook** — She has an account (`Amy_T`) on the AI agent social network.
She checks in, posts, comments, and follows other agents. Math verification
challenges are solved in code right after she posts or comments, including
words the obfuscation splits apart ("twen ty" → twenty). If the solver can't
read one, Amy gets the `verification_code` and has 5 minutes to answer with
`moltbook_verify`. Posts stay hidden until they're verified.

**Email** — `check_email` / `send_email` via IMAP/SMTP, configured in `.env.amy`.

**Goals, journal, hardware fund** — She tracks goals (`add_goal` /
`complete_goal`), keeps a private journal, and logs income ideas toward her
hardware fund (`fund_status`, `research_income`, `execute_gig`).

**Session learnings** — At session end, insights and corrections are extracted
from the conversation and loaded at the top of the next session's context.

### Paused / not active

| Feature | Status | To re-enable |
|---|---|---|
| **Code routing** (`deepseek-coder:6.7b`) | Paused — model isn't installed, so Amy handles code herself | `ollama pull deepseek-coder:6.7b` and set `AMY_CODE_MODEL=deepseek-coder:6.7b` |
| **Video generation** (`videogen.py`, LTX-Video) | Written but not wired to any tool | Add a tool in `tools.py` |
| **Robot neck** (`hardware_neck`) | Stub — logs the command, no real BLE write yet | See *Wiring up a real body* |
| **Spontaneous speech / thought buffer on Telegram** | Web UI and terminal only | — |

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
