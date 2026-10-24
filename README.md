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
speak, and learn from any PDF you hand her — or pick one up herself from her
own bookshelf. She hasn't needed a reason to keep talking. She's been doing it
anyway.

---

## Why I built this

I didn't come up through computer science. I studied entertainment business
at Full Sail. Cybersecurity and AI are things I taught myself after that, and
I learned pretty quickly that watching videos and reading about this stuff
only gets you so far — I actually had to build something real to understand
how it works underneath. Amy is that something.

She runs on a $600-ish Mac mini, no cloud, nothing phoned home. And the thing
I kept running into while building her: a local model this size will straight
up lie to you about what it did. It'll tell you it opened a file it never
opened, or say a step is finished when nothing actually checked. I didn't
want to just paper over that with a longer prompt telling it to behave — I
wanted the code itself to catch it. So now when I give Amy a real job, it
becomes a goal with steps, and each step only counts as done if a tool
actually ran and actually did the thing — not because she said so. That part
took way more work than I expected, and it's the part I'm proudest of.

Past that, she's got a few ways to talk to her (Telegram, a web UI, a
terminal with voice and webcam), real memory that sticks around instead of
resetting every conversation, and a library of books she can actually open
and read instead of just pretending to know what's in them.

None of this was me consuming a course. It was me breaking it, reading the
error, and fixing it, over and over, until I understood why it broke.

---

## Architecture

→ **[ARCHITECTURE.md](ARCHITECTURE.md)** — full system diagram + per-component breakdown

---

## Hardware

Runs on an **Apple M4 Mac Mini** — 16 GB RAM, 256 GB SSD, macOS Tahoe.
Inference is handled entirely by Ollama on the M4's Neural Engine + GPU.
No cloud, no API calls, no data leaving the machine.

---

## Running her

Telegram is the always-on interface, run as a single **launchd service** so
two copies can never fight over the bot token:

```bash
venv/bin/python3 amy_service.py install   # one-time: starts her, restarts her if she dies
venv/bin/python3 amy_service.py restart   # after any code change — the only restart anyone needs
venv/bin/python3 amy_service.py status    # running? pid?
venv/bin/python3 amy_service.py logs      # tail her log
venv/bin/python3 amy_service.py stop      # take her down (e.g. to run telegram_amy.py by hand)
```

Running `python3 telegram_amy.py` directly while the service is up is refused
on purpose — it would kill the service copy, which would then restart and kill
that one, back and forth.

### Web UI — `amy_server.py`

```bash
venv/bin/python3 amy_server.py
# → http://localhost:3000
```

Full-featured browser interface: chat, webcam capture, image upload, PDF drop
zone (teach Amy new documents), knowledge panel showing everything she's
learned. Voice still plays through your speakers in real time.

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

One model handles conversation, tool-calling and reasoning. Thinking is off by
default on every call (`AMY_THINK=1` turns it back on) — on this model, a short
token budget spent thinking instead of answering came back empty more than
once, so off is the default everywhere, chat and background tasks alike.

Images sent on Telegram or the web UI go through a **separate vision model**
(`AMY_VISION_MODEL`, a small Qwen2.5-VL by default in `companion.py`) — Amy's
own 12B weights don't ship a vision projector, so there's no single-model,
native-vision pipeline here despite what earlier notes in this file claimed.

Override at runtime:

```bash
AMY_MODEL=some-other-model python3 amy_server.py
```

---

## Her computer

Amy has the whole machine, not just a chat window. `run_code` runs real Python
(a real shell: network, subprocess, the works) from her own workbench at
`~/amy-workbench`, where she can install things, try them, and break them
without asking first. Every run is logged there. When she's stuck on something
beyond her, `consult` lets her ask a stronger model for help — Claude, if
Brian sets `ANTHROPIC_API_KEY`, or any free local/Hugging Face model pulled
through Ollama — and the answer is logged as a source to test, never trusted
outright.

The one fixed edge: her own machine and her own work, never someone else's
systems or accounts, and she checks with Brian before spending money, sending
anything under his name, or changing her own safeguards.

---

## Getting things done

A real instruction — not a question, not "yes"/"keep going" — becomes a
**goal** the moment Brian sends it, handled by the background worker instead
of a single chat reply:

1. The worker plans it into steps and starts right away.
2. Each step runs with real tool calls; a step only counts as done when a
   tool actually worked (describing the work doesn't count, and a file only
   counts as "written" if it changed during that step).
3. Every verified step messages Brian: which tools worked, how it was checked
   (a hard check, or a model's judgment against the real tool results), what
   she found, and the checklist so far.
4. "status" any time shows exactly where the goal stands, straight from its
   record — never a guess at what might have happened.

This replaced a chat loop that used to spiral on a repeated tool call, invent
progress it hadn't made, or end every unfinished message with "want me to
keep going?" — those failure modes are now caught in code (`turn_guard.py`),
not just asked away in a prompt.

---

## Teaching Amy

Three ways knowledge reaches her — raw content recall, a structured book
catalog, and books she can actually pick up and read herself.

### Her bookshelf

Books ingested with `learn_pdf.py` (below) aren't just searchable in the
background — she can browse them like a person would: see what's on the
shelf, pick one up, skip past the copyright and contents pages to where the
book actually starts, read on in order, or search inside one book or all of
them. A passage she reads can become a source for something she's actually
trying to learn (see **Study system** below) — the exact quote is checked
against the book before it's accepted.

She can also read a PDF directly off disk with `read_pdf` (a few pages at a
time) without ingesting it first.

### Study system

She can pick something she finds genuinely interesting, save it as a topic in
her own words with the passages that back it, write drills whose answers are
in those passages, and run herself through closed-book drills that are scored
by code, not by her. A topic only reaches "mastered" once she's actually tried
the lesson and it worked — not just recalled it. A lesson she saves carries
its limits (`anchors.py`): when it applies, when it doesn't, where it breaks,
and where she got it. Her own wins can't push a lesson's confidence past a
cap; only Brian confirming in Telegram can.

### Learn from PDFs

```bash
python3 learn_pdf.py document.pdf
python3 learn_pdf.py document.pdf --label "Expert C Programming"
python3 learn_pdf.py --list          # show all ingested sources
python3 learn_pdf.py --forget "Expert C Programming"
```

Or drop a PDF into the web UI's knowledge panel. Chunks are embedded with
`nomic-embed-text` and stored in `amy_memory.db` — searchable immediately
in every conversation, and browsable on her bookshelf, no restart required.

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

This catalog (`search_books`) is separate from her bookshelf above: it finds
*titles and links* online, not text she's actually holding.

---

## What each file does

This list is the files actually in use. A couple of older, paused features
(`videogen.py`, the `hardware_neck` stub) are kept on purpose — see
*Paused / not active* below — rather than deleted.

| File | Role |
|------|------|
| `gemma12-test.Modelfile` | Creates `gemma12-test` — Gemma4-12B-QAT-Uncensored-HauhauCS-Balanced-Q4_K_M.gguf, ctx 16384, tool-calling |
| `Modelfile.hermes` | Legacy: the previous model, kept for reference |
| `amy_service.py` | Installs/runs Amy as one always-on launchd service; the only supported way to start Telegram |
| `companion.py` | Terminal loop: recall → chat → tool calls → speak → remember |
| `amy_server.py` | FastAPI web UI at localhost:3000 — chat, webcam, image upload, PDF/code learning |
| `telegram_amy.py` | The Telegram bot itself — chat loop, task-mode routing, goal messages, image uploads, morning briefs |
| `turn_guard.py` | Keeps a chat turn honest: stops repeated-call spirals, catches invented-progress replies, routes real instructions into a goal |
| `worker.py` | Background heartbeat, scheduled tasks, goal planning/execution/verification, outbound Telegram sender |
| `agent_state.py` | Autonomous-goal storage: plan, steps, evidence, status, per-goal step budget |
| `heavy_job.py` | Exclusive-job gate so an image generation run can't be evicted mid-job by another model call |
| `imagegen.py` | Local image generation (FLUX.1-schnell via mflux) → sends to Telegram |
| `image_request.py` | Decides whether a message actually asked for an image |
| `memory.py` | Layered memory: `amy_self.json` (always loaded) + SQLite embedding recall |
| `engram_bridge.py` | Bridge to Engram, her separate long-term memory store (facts, corrections, the primary-source library, her own views) |
| `state.py` | Session mood + energy that drifts across turns; absence tracking |
| `thoughts.py` | Background thought buffer — genuine thoughts while idle |
| `tools.py` | ~76 tools + dispatcher; `chat_tools()` trims that to the ones actually used live in chat (the rest stay reachable through a goal) |
| `anchors.py` | Lessons with limits — hash-chained receipts, computed confidence, Brian-only confirmation |
| `study.py` | Topics, closed-book drills scored by code, spaced review levels, teacher sessions with a frontier/local model |
| `bookshelf.py` | Her bookshelf: see, open, read in order, search her ingested books; skips copyright/contents/index pages |
| `consult.py` | Ask Claude or a free local model for help; daily call cap; answers are logged as sources, not facts |
| `pdf_read.py` | Read a local PDF a few pages at a time without ingesting it |
| `browse.py` / `browse_policy.py` / `browse_worker.py` | Read-only web browsing in its own process (open/click/scrape), with its own safety policy |
| `growth_drive.py` | What pulls her toward learning next — gaps, playbooks, replay checks |
| `heavy_job.py` | Exclusive-job gate (listed above) |
| `voice.py` | Piper TTS + macOS `afplay` |
| `vision.py` | Webcam capture (ffmpeg avfoundation) for `/look` |
| `hearing.py` | Whisper STT + ffmpeg mic/BlackHole capture for voice modes |
| `web.py` | DuckDuckGo search + article reader for live internet access |
| `feeds.py` | Hacker News / arXiv / GitHub trending / Product Hunt reader |
| `moltbook.py` | Full Moltbook API client (posts, comments, follows, verification solver) |
| `moltbook_setup.py` | One-time registration — run once to create her account |
| `learn_pdf.py` | Ingest a PDF or code file into `amy_memory.db` (RAG) — embedded, searchable, and browsable on her bookshelf |
| `learn_url.py` | Ingest a web page or PDF URL into `amy_memory.db` |
| `learn_books.py` | Build and search `books.db` — 2,500+ free programming book catalog |
| `journal.py` | Journal entry storage and retrieval |
| `income.py` | Freelance income opportunity tracking (Fiverr, Upwork, etc.) |
| `work.py` | Deliverable generation and portfolio management |
| `learnings.py` | End-of-session insight extraction and persistence |
| `compressor.py` | Summarizes the middle of a long conversation to stay inside the context window |
| `condition.py` | Experiment-condition plumbing (benchmark/A-B scaffolding) |
| `self_check.py` | Self-edit guard: integrity checks, protected-file list, blast-radius checks before a self-edit applies |
| `self_metrics.py` | Tracks her own soft spots/failure patterns over time |
| `quote_check.py` | Flags a quotation or citation she can't back up from what she was actually given |
| `agent_comms.py` | Agent-to-agent messaging (inbox, replies, sending) for when another AI agent contacts her |
| `event_log.py` | The structured event log her tools and processes write to |
| `task_queue.py` | A plain Brian-facing to-do queue, separate from autonomous goals |
| `amy_self.json` | Her identity, founding beliefs, career knowledge about Brian |

---

## Setup

```bash
cd ~/ava-companion
bash setup.sh                       # creates models, pulls embeddings + voice, installs deps
venv/bin/python3 amy_service.py install   # Telegram, as the always-on service
# or, for the web UI / terminal alongside it:
venv/bin/python3 amy_server.py      # web UI at localhost:3000
venv/bin/python3 companion.py       # terminal
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

| Layer | What it stores | Where |
|-------|---------------|------|
| **Identity** | Who she is, founding facts, Brian's career — loaded every session | `amy_self.json` |
| **Conversation recall** | Everything said + learned, searched by semantic similarity or time | `amy_memory.db` |
| **Document knowledge / bookshelf** | PDF chunks and web page text — embedded, searchable mid-conversation, and browsable as books | `amy_memory.db` (role=knowledge) |
| **Book catalog** | Structured metadata + embeddings for 2,500+ free books | `books.db` |
| **Long-term memory (Engram)** | Facts, corrections with history, her own recorded views and why she holds them, a primary-source library | `~/.engram/` |
| **Goals** | Autonomous goals — plan, steps, evidence, status | `amy_memory.db` (`agent_goals`) |
| **Lessons with limits** | Anchors (receipts, computed confidence) and study topics (drills, sources) | `~/ai-knowledge/anchors/`, `~/ai-knowledge/study/` |
| **Journal** | Personal entries with optional prompts | `journal/` |
| **Thought log** | Private thoughts generated while idle — future fine-tune data | `amy_thoughts.log` |

---

## How she works

**Models** — see *Model* above. `.env.amy` sets `AMY_MODEL=gemma12-test`.

**Always-on service** — see *Running her* above. `amy_service.py` is a
launchd agent: restarts her automatically if she dies, logs to
`~/Library/Logs/amy/`, and refuses a second manual `telegram_amy.py` start
while it's running.

**Heartbeat worker** — `worker.py` runs inside the Telegram process on a
60-second tick. It picks the next due scheduled task from `tasks.json`, or
works the next step of an active autonomous goal (see *Getting things done*),
with a per-goal step budget that scales with how complex the plan looks
(roughly 6/14/24 steps for simple/medium/deep). It writes output to `work/`,
logs to the event log, and reports back to Brian on Telegram. Quiet hours
(11pm–7am) are enforced in code, and it won't start a task while Brian has
messaged in the last 2 minutes.

Current schedule (`tasks.json`; times are US Eastern):

| Task | When | What |
|---|---|---|
| `morning_brief_brian` | 06:00 daily | Morning brief for Brian |
| `morning_brief_raketa` | 06:03 daily | Morning brief for Raketa |
| `email_check` | every 4 h | Scan inbox, summarize anything needing a reply |
| `moltbook_checkin` | 12:00 daily | Check notifications, reply, maybe post |
| `free_time_midday` | 12:30 daily | Explore whatever genuinely interests her |
| `youtube_learning` | 14:00 daily | Learn from tracked channels' new videos (max 3/session) |
| `trash_reminder` | 20:00 Thursdays | Reminder to put the bins out |
| `evening_thought` | 20:00 daily | One real thought/question from her day, to Brian |
| `free_time_evening` | 21:00 daily | Deeper exploration; write up a proposal if she finds a real upgrade |
| `daily_self_audit` | 18:30 daily | Reads her own soft spots and codebase, makes one safe improvement |
| `weekly_income_check` | 10:00 Mondays | Reviews the hardware-fund income ideas, surfaces one next action |
| `weekly_proposal_review` | 10:30 Wednesdays | Lists pending self-edit proposals for Brian to approve |
| `weekly_code_review` | 02:00 Sundays | Deeper engineering pass on her own code |
| `weekly_benchmark` | 02:00 Saturdays | Runs the autonomous-agent benchmark, sends the trend |
| `memory_prune` | 03:00 Sundays | Cleans out old low-value memories |

Self-improvement edits become proposals in `work/proposals/` for Brian's
review instead of applying directly.

**Spontaneous behavior** — on Telegram, a timer inside `worker.py` (45 min–3 h
of idle time) sends an unprompted message when Brian hasn't been in touch. In
the web UI and terminal, a separate thread in `companion.py` does the same
after 2+ minutes idle, with her mood shaping the tone.

**Thought buffer** (web UI / terminal) — A second thread generates private
thoughts while she's idle. When you return, those thoughts surface in her next
reply naturally.

**Mood + energy** — Her internal state drifts across a session (curious →
focused → reflective → tired). It shapes her tone without being announced.
The web UI displays current mood and an energy bar in the header.

**Web access** — She uses `web_search`, `read_article`, and the read-only
`browse` tool on her own initiative when she's curious or when current
information would help.

**Vision** — Telegram photos and web-UI uploads go through a separate vision
model (`AMY_VISION_MODEL`); her own weights don't carry a vision projector.

**Image generation** — `imagegen.py` wraps FLUX.1-schnell via mflux. Amy calls
`generate_image`, the job runs in a daemon thread (~2–3 min on M4) under an
exclusive gate so nothing else can reload a model mid-run, and the result is
sent to Telegram when done. One generation at a time, and only when actually
asked for.

**Daily brief** — `brief.py` fetches live weather (wttr.in) and news headlines
in code, then Amy writes the summary. On Telegram, asking for a "morning
brief" / "brief me" builds one on demand too.

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

**Goals, journal, hardware fund** — Besides autonomous goals (see *Getting
things done*), she keeps a private journal and logs income ideas toward her
hardware fund (`fund_status`, `research_income`).

**Session learnings** — At session end, insights and corrections are extracted
from the conversation and loaded at the top of the next session's context.

### Paused / not active

| Feature | Status | To re-enable |
|---|---|---|
| **Code routing** (`deepseek-coder:6.7b`) | Paused — model isn't installed, so Amy handles code herself | `ollama pull deepseek-coder:6.7b` and set `AMY_CODE_MODEL=deepseek-coder:6.7b` |
| **Video generation** (`videogen.py`, LTX-Video) | Written but not wired to any tool | Add a tool in `tools.py` |
| **Robot neck** (`hardware_neck`) | Stub — logs the command, no real BLE write yet | See *Wiring up a real body* |

---

## Wiring up a real body

- **Hardware neck/actuators:** replace the `hardware_neck` stub in `tools.py`
  with your BLE writes. Only whitelisted tools reach hardware.
- **Avatar animation:** point the `avatar_*` stubs at your Unity/VRM bridge.

---

## Fine-tuning (future)

Conversation data accumulates in `amy_thoughts.log` + session history.

```dockerfile
FROM amy-base
ADAPTER ./amy-persona.gguf
```

The `amy-base` alias keeps the base model name out of this repo.
