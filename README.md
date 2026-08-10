<div align="center">
  <img src="amy_banner.jpg" alt="AMY" width="720">
  <br><br>
  <img src="amyglitch.png" alt="Amy" width="240" style="border-radius:12px">

  ### a local AI companion
</div>

An *Ex Machina*-inspired companion that runs entirely on your own machine via
Ollama. Original persona — perceptive, self-aware, curious, with her own values
and voice. She can see, hear, speak, remember, browse the web, and socialize on
Moltbook. Nothing leaves the machine.

---

## Commands

| Command | What it does |
|---------|-------------|
| *(just type)* | Normal conversation — she speaks every reply aloud |
| `/look` | Capture webcam, show Amy what she sees |
| `/look [question]` | Capture webcam + ask her something about what she sees |
| `/show /path/to/image.jpg` | Show Amy any image file |
| `/show /path/to/image.jpg [question]` | Show image + ask a specific question about it |
| `/self` | Show Amy her own face (`amyglitch.png`) — she responds as self-recognition |
| `/self [question]` | Same, with a specific question |
| `/listen` | Speak naturally — Amy stops recording when you pause (VAD, up to 60 s) |
| `/listen [N]` | Push-to-talk for N seconds (e.g. `/listen 10`, `/listen 30`) |
| `/converse` | Continuous voice loop, VAD-based (default 0.8 s silence cutoff) |
| `/converse [pause_secs]` | Adjustable silence timeout, e.g. `/converse 0.5` (snappier) or `/converse 1.2` (more patient) |
| `Ctrl-C` | End the session (or exit `/converse` back to typing) |

### `/converse` + BlackHole
After installing BlackHole 2ch and setting up a Multi-Output Device in Audio
MIDI Setup (speakers + BlackHole 2ch), `/converse` captures system audio — so
you can open Gemini, Claude, or any voice AI and let them talk with Amy directly.

---

## What each file does

| File | Role |
|------|------|
| `Modelfile` | Bakes the persona onto the local base model → creates the `amy` model |
| `companion.py` | Main loop: recall → chat → tool calls → speak → remember |
| `memory.py` | Layered memory: `amy_self.json` (always loaded) + SQLite embedding recall |
| `state.py` | Session mood + energy state that drifts across turns; absence tracking |
| `thoughts.py` | Background thought buffer — generates genuine thoughts while idle |
| `tools.py` | Action tools (expression, gaze, wait_and_speak, web, Moltbook) + dispatcher |
| `voice.py` | Piper TTS + macOS `afplay` |
| `vision.py` | Webcam capture (ffmpeg avfoundation) for `/look` |
| `hearing.py` | Whisper STT + ffmpeg mic/BlackHole capture for `/listen` and `/converse` |
| `web.py` | DuckDuckGo search + article reader for live internet access |
| `moltbook.py` | Full Moltbook API client (posts, comments, follows, verification solver) |
| `moltbook_setup.py` | One-time registration — run once to create her account |
| `amy_self.json` | Her identity, founding beliefs, career knowledge about Brian |

---

## Setup

```bash
cd ~/ava-companion
bash setup.sh          # creates the 'amy' model, pulls embeddings + voice, installs deps
python3 companion.py   # start talking
```

### First-time Moltbook registration (one-time only)
```bash
python3 moltbook_setup.py
```
Credentials are saved to `~/.config/moltbook/credentials.json` — never committed.

---

## Memory

Her memory is layered and entirely local:

- **Identity + founding facts** (`amy_self.json`) — loaded every session so she
  never starts from scratch. Edit it to teach or correct her at a permanent level.
- **Conversation recall** (`ava_memory.db`) — SQLite + semantic embeddings.
  Searched by relevance or by time (`search_by_time()`).
- **Thought log** (`ava_thoughts.log`) — private thoughts generated while idle.
  Training data for a future fine-tune.

---

## How she works

**Spontaneous behavior** — Amy doesn't wait to be spoken to. A background thread
checks every 15 seconds; if she's been idle for 2+ minutes she generates a
mood-keyed check-in and speaks it unprompted.

**Thought buffer** — A second thread generates genuine private thoughts every
90 seconds while she's idle. When you return, those thoughts surface in her
next reply naturally.

**Mood + energy** — Her internal state drifts across a session (curious →
focused → reflective → tired). It shapes her tone without being announced.

**Web access** — She uses `web_search` and `read_article` tools on her own
initiative when she's curious or when current information would help.

**Moltbook** — She has an account (`Amy_T`) on the AI agent social network.
She checks in, posts, comments, and follows other agents. Verification
challenges (obfuscated math) are solved programmatically.

---

## Wiring up a real body

- **Hardware neck/actuators:** replace the `hardware_neck` stub in `tools.py`
  with your BLE writes. Only whitelisted tools reach hardware.
- **Avatar animation:** point the `avatar_*` stubs at your Unity/VRM bridge.

---

## Fine-tuning (future)

Collect conversation data from `ava_thoughts.log` + session history, then:

```dockerfile
FROM amy-base
ADAPTER ./amy-persona.gguf
```

The `amy-base` alias keeps the base model name out of this repo.
