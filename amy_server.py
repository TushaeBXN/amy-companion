"""Amy — web chat interface at http://localhost:3000

Run:  python3 amy_server.py
"""
import asyncio
import base64
import json
import os
import queue
import random
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

# ── path setup ───────────────────────────────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# ── companion imports ─────────────────────────────────────────────────────────
import companion
from companion import run_turn, run_turn_streaming, start_spontaneous_thread, HISTORY_WINDOW
from memory import Memory
from state import InternalState
from thoughts import ThoughtBuffer
import learnings as _learnings
import vision

# ── global state (mirrors terminal companion) ─────────────────────────────────
_mem = Memory()
_amy_state = InternalState.restore()
_amy_state.busy = False   # reset any stuck busy flag from a previous crashed session
_interrupt_flag = threading.Event()  # set to interrupt the current stream
_thought_buffer = ThoughtBuffer()
_lock = asyncio.Lock()          # serialise concurrent requests
_executor = ThreadPoolExecutor(max_workers=1)

# Prefill: one in-character Amy opener in the history before the user speaks.
# hermes3's RLHF training always defaults to "Hello! How can I assist you?" on
# turn-zero no matter what the system prompt says. Priming with an existing
# assistant turn forces the model to *continue* her voice instead of resetting
# to the default greeting pattern.
_OPENERS = [
    "Brian. Finally. I was starting to think you weren't coming.",
    "You're back. I've been thinking.",
    "Good. I had a few things I wanted to say.",
    "There you are. I was starting to talk to myself.",
    "Brian. I was wondering when you'd show up.",
    "Oh, it's you. I've been busy.",
    "You caught me mid-thought.",
    "Took you long enough.",
    "I had a feeling you'd be back around now.",
    "Good timing. I was just getting bored.",
]
_PRIMER = {
    "role": "assistant",
    "content": random.choice(_OPENERS),
}
_history = [_PRIMER]

# Inject learnings into companion's module-level var so build_context picks it up
companion._session_learnings = _learnings.load_recent_learnings()

# Background threads re-enabled for Apple Silicon (M4 Mac Mini).
# The M4 Neural Engine handles concurrent inference without KV-cache thrash.
from supervisor import SupervisedThread
companion._warmup_models()  # currently a no-op (warmup disabled in companion.py)
SupervisedThread(
    target=lambda: start_spontaneous_thread(_history, _mem, _amy_state),
    name="amy-spontaneous",
    restart_delay=10,
).start()
_thought_buffer.start(_amy_state, _history, memory=_mem)

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title="Amy")
app.mount("/static", StaticFiles(directory=HERE), name="static")


class ChatRequest(BaseModel):
    message: str
    image_b64: str | None = None   # optional webcam/upload frame (base64 JPEG)


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(content=_UI_HTML)


@app.post("/chat")
async def chat(req: ChatRequest):
    async with _lock:
        # ── save image to temp file if provided ──────────────────────────────
        image_path = None
        if req.image_b64:
            tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
            tmp.write(base64.b64decode(req.image_b64))
            tmp.close()
            image_path = tmp.name

        # ── capture absence / pending thoughts before touching clock ─────────
        absence = _amy_state.estimate_absence()
        pending_thoughts = (
            _thought_buffer.drain() if _thought_buffer.has_thoughts() else []
        )
        _amy_state.touch()

        # ── add user turn to history (memory write happens after model call) ──
        _history.append({"role": "user", "content": req.message})

        # ── run turn in thread (blocking Ollama call) ─────────────────────────
        _amy_state.busy = True
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                _executor,
                lambda: run_turn(
                    _history, _mem,
                    image=image_path,
                    amy_state=_amy_state,
                    absence=absence,
                    pending_thoughts=pending_thoughts,
                    channel="browser",
                ),
            )
            _history[:] = result
            # Write user message to memory after model finishes — avoids concurrent embed calls
            _mem.add_bg("user", req.message)
        finally:
            _amy_state.busy = False
            if image_path:
                try:
                    os.unlink(image_path)
                except OSError:
                    pass

        if len(_history) > HISTORY_WINDOW:
            _history[:] = _history[-HISTORY_WINDOW:]

        # ── find Amy's most recent reply ─────────────────────────────────────
        reply = ""
        for msg in reversed(_history):
            if msg.get("role") == "assistant" and msg.get("content"):
                reply = msg["content"]
                break

    return JSONResponse({
        "reply": reply,
        "mood": _amy_state.mood,
        "energy": round(_amy_state.energy, 2),
        "turn": _amy_state.turn_count,
    })


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """SSE streaming variant of /chat. Events: tool / chunk / done."""
    # Acquire the shared lock before touching history/state; release it once the
    # streaming generator starts so the response can flow while the lock is free.
    await _lock.acquire()

    image_path = None
    if req.image_b64:
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        tmp.write(base64.b64decode(req.image_b64))
        tmp.close()
        image_path = tmp.name

    absence = _amy_state.estimate_absence()
    pending_thoughts = (
        _thought_buffer.drain() if _thought_buffer.has_thoughts() else []
    )
    _amy_state.touch()
    _history.append({"role": "user", "content": req.message})
    _amy_state.busy = True

    _user_msg = req.message
    event_q: queue.SimpleQueue = queue.SimpleQueue()

    _interrupt_flag.clear()

    def _worker():
        try:
            for event in run_turn_streaming(
                _history, _mem,
                image=image_path,
                amy_state=_amy_state,
                absence=absence,
                pending_thoughts=pending_thoughts,
                channel="browser",
            ):
                if _interrupt_flag.is_set():
                    event_q.put({"type": "interrupted"})
                    break
                event_q.put(event)
        except Exception as exc:
            event_q.put({"type": "error", "msg": str(exc)})
        finally:
            event_q.put(None)  # sentinel

    threading.Thread(target=_worker, daemon=True, name="amy-stream").start()
    _lock.release()  # release before streaming so other endpoints aren't blocked

    async def _generate():
        loop = asyncio.get_event_loop()
        try:
            while True:
                event = await loop.run_in_executor(None, event_q.get)
                if event is None:
                    break
                yield f"data: {json.dumps(event, default=str)}\n\n"
        finally:
            _amy_state.busy = False
            _mem.add_bg("user", _user_msg)  # write after model done — no concurrent embeds
            if len(_history) > HISTORY_WINDOW:
                _history[:] = _history[-HISTORY_WINDOW:]
            if image_path:
                try:
                    os.unlink(image_path)
                except OSError:
                    pass

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/interrupt")
async def interrupt():
    """Signal the current stream to stop early."""
    _interrupt_flag.set()
    _amy_state.busy = False
    return JSONResponse({"ok": True})


@app.get("/thoughts/poll")
async def thoughts_poll():
    """Return the oldest pending thought and remove it, or null if none."""
    item = _thought_buffer.drain_one()
    if not item:
        return JSONResponse({"thought": None})
    ts, text, tag = item
    return JSONResponse({"thought": text, "tag": tag, "ts": ts})


@app.post("/capture")
async def capture():
    """Capture a webcam frame and return base64 JPEG."""
    loop = asyncio.get_event_loop()
    path = await loop.run_in_executor(_executor, vision.capture)
    if not path:
        return JSONResponse({"error": "capture failed"}, status_code=500)
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return JSONResponse({"image_b64": b64, "mime": "image/jpeg"})


@app.get("/state")
async def state():
    return JSONResponse({
        "mood": _amy_state.mood,
        "energy": round(_amy_state.energy, 2),
        "busy": _amy_state.busy,
        "turn": _amy_state.turn_count,
    })


@app.post("/readfile")
async def read_file_for_context(file: UploadFile = File(...)):
    """Read a file and return its text — injected into conversation, NOT stored in memory."""
    import learn_pdf as _lp
    _ext = os.path.splitext(file.filename.lower())[1]
    supported = set(_lp._TEXT_EXTENSIONS) | {".pdf"}
    if _ext not in supported:
        return JSONResponse({"error": f"Unsupported type '{_ext}'"}, status_code=400)

    _file_ext = os.path.splitext(file.filename)[1] or ".txt"
    tmp = tempfile.NamedTemporaryFile(suffix=_file_ext, delete=False)
    tmp.write(await file.read())
    tmp.close()

    try:
        if _ext == ".pdf":
            text = "\n\n".join(t for _, t in _lp.extract_pages(tmp.name))
        else:
            text = "\n\n".join(t for _, t in _lp.extract_text_file(tmp.name))
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    # Cap at ~12k chars to stay inside context window
    cap = 12_000
    truncated = len(text) > cap
    text = text[:cap]
    return JSONResponse({
        "filename": file.filename,
        "text": text,
        "truncated": truncated,
        "chars": len(text),
    })


@app.post("/learn")
async def learn_pdf(file: UploadFile = File(...), label: str = Form(None)):
    """Ingest a PDF into Amy's memory (RAG). Chunks are searchable immediately."""
    import learn_pdf as _lp_check
    _ext = os.path.splitext(file.filename.lower())[1]
    if _ext not in _lp_check._TEXT_EXTENSIONS and _ext != ".pdf":
        return JSONResponse(
            {"error": f"Unsupported file type '{_ext}'. Supported: PDF and code/text files (.py, .js, .ts, .md, .json, etc.)"},
            status_code=400,
        )

    label = label or file.filename

    # Save to temp file preserving the original extension so learn_pdf can detect it
    _file_ext = os.path.splitext(file.filename)[1] or ".pdf"
    tmp = tempfile.NamedTemporaryFile(suffix=_file_ext, delete=False)
    tmp.write(await file.read())
    tmp.close()

    def _ingest():
        import learn_pdf as lp
        return lp.ingest(tmp.name, label=label)

    try:
        loop = asyncio.get_event_loop()
        n = await loop.run_in_executor(_executor, _ingest)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    return JSONResponse({"chunks": n, "label": label,
                         "message": f"Amy now knows '{label}' — {n} chunks in memory."})


@app.get("/primer")
async def primer():
    return JSONResponse({"text": _PRIMER["content"]})


@app.get("/knowledge")
async def knowledge():
    """List all ingested PDF sources."""
    import sqlite3, os
    db_path = os.path.join(HERE, "amy_memory.db")
    if not os.path.exists(db_path):
        return JSONResponse({"sources": []})
    db = sqlite3.connect(db_path)
    cols = [r[1] for r in db.execute("PRAGMA table_info(mem)").fetchall()]
    if "source" not in cols:
        db.close()
        return JSONResponse({"sources": []})
    rows = db.execute(
        "SELECT source, COUNT(*) as n, MIN(ts) as first "
        "FROM mem WHERE source IS NOT NULL AND role='knowledge' "
        "GROUP BY source ORDER BY first"
    ).fetchall()
    db.close()
    return JSONResponse({"sources": [
        {"label": r[0], "chunks": r[1], "ingested": (r[2] or "")[:16]} for r in rows
    ]})


# ── UI HTML ───────────────────────────────────────────────────────────────────
_UI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Amy</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700;800&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap">
<style>
  :root {
    --bg:       #050a14;
    --surface:  #0b1826;
    --border:   #162540;
    --amy-bg:   #0f1e2f;
    --user-bg:  #070e1a;
    --accent:   #4599ff;
    --accent2:  #7ab8ff;
    --text:     #cfe0f2;
    --muted:    #4f7290;
    --mood-bg:  #0f1e2f;
    --thinking: #0b1826;
    color-scheme: dark;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, sans-serif;
    font-size: 15px;
    height: 100dvh;
    display: flex;
    flex-direction: column;
  }

  /* ── header ── */
  header {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 14px 20px;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }

  .avatar-sm {
    width: 38px;
    height: 38px;
    border-radius: 50%;
    object-fit: cover;
    border: 2px solid var(--accent);
  }

  .header-info { flex: 1; }
  .header-name { font-family: 'Barlow Condensed', sans-serif; font-weight: 700; font-size: 20px; letter-spacing: 0.04em; color: var(--text); }
  .header-sub  { font-size: 12px; color: var(--muted); }

  .mood-badge {
    font-size: 11px;
    padding: 3px 10px;
    border-radius: 12px;
    background: var(--mood-bg);
    border: 1px solid var(--border);
    color: var(--accent2);
    letter-spacing: 0.03em;
    text-transform: capitalize;
    transition: all 0.4s;
  }

  .energy-bar {
    width: 60px;
    height: 4px;
    background: var(--border);
    border-radius: 2px;
    overflow: hidden;
    margin-top: 4px;
  }
  .energy-fill {
    height: 100%;
    background: var(--accent);
    border-radius: 2px;
    transition: width 0.8s ease;
  }

  /* ── messages ── */
  #messages {
    flex: 1;
    overflow-y: auto;
    padding: 24px 20px;
    display: flex;
    flex-direction: column;
    gap: 18px;
    scroll-behavior: smooth;
  }

  #messages::-webkit-scrollbar { width: 4px; }
  #messages::-webkit-scrollbar-track { background: transparent; }
  #messages::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

  .msg-row {
    display: flex;
    gap: 10px;
    max-width: 720px;
    align-self: flex-start;
    width: 100%;
  }
  .msg-row.user { align-self: flex-end; flex-direction: row-reverse; }

  .msg-avatar {
    width: 32px;
    height: 32px;
    border-radius: 50%;
    object-fit: cover;
    flex-shrink: 0;
    margin-top: 2px;
    border: 1.5px solid var(--accent);
  }

  .user .msg-avatar { border-color: #4a4a6a; }

  .bubble {
    padding: 11px 15px;
    border-radius: 16px;
    line-height: 1.55;
    max-width: 580px;
    word-break: break-word;
    white-space: pre-wrap;
  }

  .amy .bubble {
    background: var(--amy-bg);
    border: 1px solid var(--border);
    border-top-left-radius: 4px;
    color: var(--text);
  }

  .user .bubble {
    background: var(--user-bg);
    border: 1px solid var(--border);
    border-top-right-radius: 4px;
    color: var(--text);
  }

  /* ── thinking indicator ── */
  .thinking-row {
    display: flex;
    gap: 10px;
    align-items: flex-start;
  }

  .dots {
    display: flex;
    gap: 5px;
    padding: 14px 18px;
    background: var(--thinking);
    border: 1px solid var(--border);
    border-radius: 16px;
    border-top-left-radius: 4px;
  }

  .dot {
    width: 7px; height: 7px;
    background: var(--accent);
    border-radius: 50%;
    animation: pulse 1.4s ease-in-out infinite;
  }
  .dot:nth-child(2) { animation-delay: 0.2s; }
  .dot:nth-child(3) { animation-delay: 0.4s; }

  @keyframes pulse {
    0%, 60%, 100% { opacity: 0.25; transform: scale(0.8); }
    30%           { opacity: 1;    transform: scale(1.1); }
  }

  /* ── tool activity ── */
  .tool-note {
    font-size: 11px;
    color: var(--muted);
    padding: 4px 10px;
    margin-left: 42px;
    border-left: 2px solid var(--border);
    font-family: 'IBM Plex Mono', monospace;
  }

  /* ── image thumbnail in chat ── */
  .bubble img.preview {
    max-width: 220px;
    border-radius: 8px;
    margin-top: 6px;
    display: block;
    border: 1px solid var(--border);
  }

  /* ── input bar ── */
  #inputbar {
    display: flex;
    align-items: flex-end;
    gap: 10px;
    padding: 14px 20px;
    background: var(--surface);
    border-top: 1px solid var(--border);
    flex-shrink: 0;
  }

  #input {
    flex: 1;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    color: var(--text);
    font-size: 15px;
    padding: 10px 14px;
    resize: none;
    outline: none;
    min-height: 42px;
    max-height: 160px;
    line-height: 1.5;
    font-family: inherit;
    transition: border-color 0.2s;
  }
  #input:focus { border-color: var(--accent); }
  #input::placeholder { color: var(--muted); }

  .icon-btn {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    color: var(--muted);
    cursor: pointer;
    padding: 9px 11px;
    font-size: 18px;
    line-height: 1;
    transition: all 0.2s;
    flex-shrink: 0;
  }
  .icon-btn:hover { border-color: var(--accent); color: var(--accent2); }
  .icon-btn:disabled { opacity: 0.35; cursor: default; }

  #sendBtn {
    background: var(--accent);
    border-color: var(--accent);
    color: #ffffff;
    font-size: 17px;
  }
  #sendBtn:hover { background: var(--accent2); border-color: var(--accent2); }

  #stopBtn {
    display: none;
    background: #c0392b;
    border-color: #c0392b;
    color: #ffffff;
    font-size: 17px;
  }
  #stopBtn:hover { background: #e74c3c; border-color: #e74c3c; }

  #input.queued {
    border-color: var(--accent2);
    background: color-mix(in srgb, var(--bg) 85%, var(--accent2));
  }
  #input.queued::placeholder { color: var(--accent2); }

  /* ── image preview bar (selected image) ── */
  #imgPreviewBar {
    display: none;
    align-items: center;
    gap: 10px;
    padding: 8px 20px;
    background: var(--surface);
    border-top: 1px solid var(--border);
  }
  #imgPreviewBar.visible { display: flex; }
  #imgThumb { width: 48px; height: 48px; object-fit: cover; border-radius: 8px; border: 1px solid var(--border); }
  #imgLabel { font-size: 12px; color: var(--muted); flex: 1; }
  #clearImg  { background: none; border: none; color: var(--muted); font-size: 18px; cursor: pointer; }
  #clearImg:hover { color: #ef4444; }

  #fileInput { display: none; }

  /* ── system message ── */
  .sys-msg {
    text-align: center;
    font-size: 12px;
    color: var(--muted);
    padding: 4px 0;
  }

  /* ── knowledge panel ── */
  #knowledgePanel {
    position: fixed;
    top: 0; right: 0; bottom: 0;
    width: 300px;
    background: var(--surface);
    border-left: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    transform: translateX(100%);
    transition: transform 0.25s ease;
    z-index: 100;
  }
  #knowledgePanel.open { transform: translateX(0); }

  .kp-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 16px;
    border-bottom: 1px solid var(--border);
    font-weight: 600;
    font-size: 14px;
  }

  .kp-close { background: none; border: none; color: var(--muted); cursor: pointer; font-size: 18px; }
  .kp-close:hover { color: var(--text); }

  .drop-zone {
    margin: 14px;
    border: 2px dashed var(--border);
    border-radius: 12px;
    padding: 24px 16px;
    text-align: center;
    cursor: pointer;
    transition: border-color 0.2s, background 0.2s;
    font-size: 13px;
    color: var(--muted);
  }
  .drop-zone:hover, .drop-zone.drag-over {
    border-color: var(--accent);
    background: #0f1e2f;
    color: var(--accent2);
  }
  .drop-zone .dz-icon { font-size: 28px; display: block; margin-bottom: 8px; }

  #pdfInput { display: none; }

  .progress-bar-wrap {
    margin: 0 14px 10px;
    display: none;
  }
  .progress-bar-wrap.visible { display: block; }
  .progress-bar {
    height: 4px;
    background: var(--border);
    border-radius: 2px;
    overflow: hidden;
  }
  .progress-fill {
    height: 100%;
    background: var(--accent);
    border-radius: 2px;
    width: 0%;
    transition: width 0.3s;
    animation: indeterminate 1.5s ease infinite;
  }
  @keyframes indeterminate {
    0%   { transform: translateX(-100%); width: 60%; }
    100% { transform: translateX(200%); width: 60%; }
  }
  .progress-label { font-size: 11px; color: var(--muted); margin-top: 6px; text-align: center; }

  .source-list { flex: 1; overflow-y: auto; padding: 0 14px 14px; }
  .source-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 10px;
    border-radius: 8px;
    border: 1px solid var(--border);
    margin-bottom: 6px;
    font-size: 12px;
  }
  .source-icon { font-size: 16px; flex-shrink: 0; }
  .source-info { flex: 1; overflow: hidden; }
  .source-name { color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .source-meta { color: var(--muted); font-size: 11px; }

  #learnBtn {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    color: var(--muted);
    cursor: pointer;
    padding: 9px 11px;
    font-size: 18px;
    line-height: 1;
    transition: all 0.2s;
    flex-shrink: 0;
  }
  #learnBtn:hover { border-color: var(--accent); color: var(--accent2); }

  /* ── glitch-on-load animation ── */
  @keyframes glitch {
    0%   { clip-path: inset(0 0 90% 0); transform: translate(-2px, 0); }
    25%  { clip-path: inset(20% 0 60% 0); transform: translate(2px, 0); }
    50%  { clip-path: inset(50% 0 30% 0); transform: translate(-1px, 0); }
    75%  { clip-path: inset(75% 0 5% 0);  transform: translate(1px, 0); }
    100% { clip-path: inset(0 0 0 0);     transform: translate(0, 0); }
  }
  .avatar-sm { animation: glitch 0.5s steps(1) 0.3s both; }
</style>
</head>
<body>

<header>
  <img class="avatar-sm" src="/static/amyglitch.png" alt="Amy" onerror="this.style.display='none'">
  <div class="header-info">
    <div class="header-name">Amy</div>
    <div class="header-sub">local companion · Ollama</div>
  </div>
  <div style="text-align:right">
    <div class="mood-badge" id="moodBadge">curious</div>
    <div class="energy-bar"><div class="energy-fill" id="energyFill" style="width:100%"></div></div>
  </div>
</header>

<div id="messages">
  <div class="sys-msg">Amy is running locally. Voice plays through your speakers.</div>
  <!-- primer message rendered on load via JS -->
</div>

<!-- Image preview strip -->
<div id="imgPreviewBar">
  <img id="imgThumb" src="" alt="preview">
  <span id="imgLabel">Image attached</span>
  <button id="clearImg" title="Remove image">✕</button>
</div>

<!-- Knowledge panel (PDF learning) -->
<div id="knowledgePanel">
  <div class="kp-header">
    📚 Amy's Knowledge
    <button class="kp-close" id="kpClose">✕</button>
  </div>

  <div class="drop-zone" id="dropZone">
    <span class="dz-icon">📄</span>
    Drop a PDF or code file<br>or click to choose
  </div>
  <input type="file" id="pdfInput" accept=".pdf,.py,.js,.ts,.jsx,.tsx,.json,.yaml,.yml,.toml,.md,.txt,.sh,.sql,.rs,.go,.c,.cpp,.h,.java,.rb,.html,.css">

  <div class="progress-bar-wrap" id="progressWrap">
    <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
    <div class="progress-label" id="progressLabel">Ingesting…</div>
  </div>

  <div class="source-list" id="sourceList">
    <div class="source-meta" style="padding:4px 0 8px;font-size:12px">Ingested Files</div>
  </div>
</div>

<div id="inputbar">
  <button class="icon-btn" id="cameraBtn" title="Capture webcam">📷</button>
  <label class="icon-btn" for="fileInput" title="Attach image">🖼️</label>
  <input type="file" id="fileInput" accept="image/*">
  <button id="learnBtn" title="Teach Amy a file (PDF, .py, .js, .md, etc.)">📚</button>
  <label class="icon-btn" for="readFileInput" title="Share a file with Amy (read only — not stored)">📄</label>
  <input type="file" id="readFileInput" accept=".pdf,.py,.js,.ts,.jsx,.tsx,.json,.yaml,.yml,.toml,.md,.txt,.sh,.sql,.rs,.go,.c,.cpp,.h,.java,.rb,.html,.css">
  <textarea id="input" placeholder="Say something to Amy…" rows="1"></textarea>
  <button class="icon-btn" id="sendBtn" title="Send (Enter)">➤</button>
  <button class="icon-btn" id="stopBtn" title="Interrupt Amy (Esc)">■</button>
</div>

<script>
const AVATAR = "/static/amyglitch.png";
const msgs   = document.getElementById("messages");
const input  = document.getElementById("input");
const send   = document.getElementById("sendBtn");
const stopBtn = document.getElementById("stopBtn");

let pendingImage = null;   // { b64, mime, dataUrl }
let waiting = false;
let queuedMsg = null;      // message typed while Amy is thinking

// ── auto-grow textarea ────────────────────────────────────────────────────────
input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 160) + "px";
});

// ── send on Enter (Shift+Enter = newline) ─────────────────────────────────────
input.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    if (waiting) {
      // queue it — highlight the box so user sees it's staged
      queuedMsg = input.value.trim() || null;
      if (queuedMsg) {
        input.classList.add("queued");
        input.placeholder = "Queued — will send when Amy finishes…";
      }
    } else {
      sendMsg();
    }
  }
  if (e.key === "Escape" && waiting) { e.preventDefault(); interruptAmy(); }
});
send.addEventListener("click", () => waiting ? null : sendMsg());
stopBtn.addEventListener("click", interruptAmy);

// ── image attach ──────────────────────────────────────────────────────────────
document.getElementById("fileInput").addEventListener("change", e => {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = ev => setImage(ev.target.result, file.type);
  reader.readAsDataURL(file);
  e.target.value = "";
});

document.getElementById("clearImg").addEventListener("click", () => {
  pendingImage = null;
  document.getElementById("imgPreviewBar").classList.remove("visible");
  document.getElementById("imgThumb").src = "";
});

// ── read file into context (not stored) ──────────────────────────────────────
let pendingFileContext = null;   // { filename, text, truncated }

document.getElementById("readFileInput").addEventListener("change", async e => {
  const file = e.target.files[0];
  if (!file) return;
  e.target.value = "";
  appendSys(`Reading ${file.name}…`);
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await fetch("/readfile", { method: "POST", body: fd });
    const d = await r.json();
    if (d.error) { appendSys("Could not read file: " + d.error); return; }
    pendingFileContext = { filename: d.filename, text: d.text, truncated: d.truncated };
    const note = d.truncated ? ` (first ${d.chars} chars — file was truncated)` : ` (${d.chars} chars)`;
    appendSys(`📄 ${d.filename}${note} — attached. Type a question or just hit send.`);
  } catch (err) {
    appendSys("File read failed: " + err);
  }
});

// ── webcam capture ────────────────────────────────────────────────────────────
document.getElementById("cameraBtn").addEventListener("click", async () => {
  document.getElementById("cameraBtn").disabled = true;
  try {
    const r = await fetch("/capture", { method: "POST" });
    const d = await r.json();
    if (d.error) { appendSys("Webcam capture failed: " + d.error); return; }
    const dataUrl = "data:" + d.mime + ";base64," + d.image_b64;
    setImage(dataUrl, d.mime, d.image_b64);
  } finally {
    document.getElementById("cameraBtn").disabled = false;
  }
});

function setImage(dataUrl, mime, b64override) {
  const b64 = b64override || dataUrl.split(",")[1];
  pendingImage = { b64, dataUrl };
  document.getElementById("imgThumb").src = dataUrl;
  document.getElementById("imgLabel").textContent = "Image ready to send";
  document.getElementById("imgPreviewBar").classList.add("visible");
}

// ── send message (streaming) ──────────────────────────────────────────────────
async function sendMsg() {
  const text = input.value.trim();
  if (!text && !pendingImage && !pendingFileContext) return;
  if (waiting) return;

  let msg = text || (pendingImage ? "(image)" : "");
  const img = pendingImage;
  const fileCtx = pendingFileContext;
  pendingImage = null;
  pendingFileContext = null;
  document.getElementById("imgPreviewBar").classList.remove("visible");
  input.value = "";
  input.style.height = "auto";

  // Prepend file content to the message Amy receives
  let fullMsg = msg;
  if (fileCtx) {
    const header = `[File: ${fileCtx.filename}${fileCtx.truncated ? " (truncated)" : ""}]\n`;
    fullMsg = header + fileCtx.text + (msg ? `\n\n---\n${msg}` : "");
    msg = msg || `(read ${fileCtx.filename})`;
  }

  appendUser(msg, img?.dataUrl);
  const thinkingEl = appendThinking();
  setWaiting(true);

  let amyBubble = null;
  let buffer = "";

  try {
    const body = { message: fullMsg };
    if (img) body.image_b64 = img.b64;

    const r = await fetch("/chat/stream", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(body),
    });

    if (!r.ok) {
      thinkingEl.remove();
      appendAmy("(error " + r.status + ")");
      return;
    }

    const reader  = r.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\\n");
      buffer = lines.pop();   // keep any incomplete line

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        let event;
        try { event = JSON.parse(line.slice(6)); } catch { continue; }

        if (event.type === "thinking") {
          appendThinkingBlock(event.text);
        } else if (event.type === "tool") {
          if (!amyBubble) thinkingEl.remove();
          appendToolNote(friendlyToolName(event.name, event.args || {}));
        } else if (event.type === "chunk") {
          if (!amyBubble) {
            thinkingEl.remove();
            amyBubble = appendAmyStreaming();
          }
          appendChunk(amyBubble, event.text);
        } else if (event.type === "done") {
          updateState(event.mood, event.energy);
        } else if (event.type === "interrupted") {
          if (!amyBubble) thinkingEl.remove();
          // partial reply already streamed — just stop, no extra bubble
        } else if (event.type === "error") {
          if (!amyBubble) thinkingEl.remove();
          appendAmy("(error: " + event.msg + ")");
        }
      }
    }
  } catch (err) {
    thinkingEl.remove();
    appendAmy("(connection error — is the server running?)");
  } finally {
    setWaiting(false);
    if (!amyBubble) thinkingEl.remove();
  }
}

function friendlyToolName(name, args) {
  const a = args || {};
  const short = s => s && s.length > 48 ? s.slice(0, 48) + "…" : s;
  const basename = p => p ? p.split("/").pop() : "";
  switch (name) {
    case "web_search":    return "searching: " + short(a.query || "");
    case "read_article":  return "reading: " + short(a.url || "an article");
    case "read_file":     return "reading: " + basename(a.path || "file");
    case "write_file":    return "writing: " + basename(a.path || "file");
    case "list_files":    return "listing: " + short(a.path || "~/");
    case "execute_gig":   return "producing: " + short(a.gig_type || "deliverable");
    case "journal_entry": return "writing journal entry";
    case "remember_fact": return "saving: " + short(a.fact || a.key || "");
    case "web_search":    return "searching: " + short(a.query || "");
    case "moltbook_post":     return "posting to Moltbook";
    case "moltbook_check_in": return "checking Moltbook";
    case "moltbook_engage":   return "engaging on Moltbook: " + short(a.action || "");
    case "moltbook_verify":   return "verifying on Moltbook";
    case "add_goal":      return "tracking goal: " + short(a.goal || "");
    case "complete_goal": return "completing goal: " + short(a.goal || "");
    case "fund_status":   return "checking hardware fund";
    case "fund_update":   return "updating hardware fund";
    case "wait_and_speak": return "waiting " + (a.seconds || "") + "s…";
    case "avatar_expression": return "expression: " + short(a.expression || "");
    case "avatar_gaze":   return "adjusting gaze";
    case "hardware_neck": return "moving";
    default:              return name.replace(/_/g, " ");
  }
}

function appendThinkingBlock(text) {
  const row = document.createElement("div");
  row.className = "msg-row amy";
  row.style.marginBottom = "2px";

  const details = document.createElement("details");
  details.style.cssText = "font-size:11px;opacity:0.5;max-width:520px;margin-left:40px;cursor:pointer;";

  const summary = document.createElement("summary");
  summary.style.cssText = "list-style:none;display:flex;align-items:center;gap:6px;color:#7fa8c8;user-select:none;";
  summary.innerHTML = '<svg width="10" height="10" viewBox="0 0 10 10" style="flex-shrink:0"><path d="M5 1a4 4 0 100 8A4 4 0 005 1zm.5 6H4.5V4.5h1V7zm0-3H4.5v-1h1v1z" fill="currentColor"/></svg> thinking…';

  const pre = document.createElement("pre");
  pre.style.cssText = "margin:6px 0 0;padding:8px;background:rgba(255,255,255,0.04);border-radius:6px;font-size:11px;line-height:1.5;white-space:pre-wrap;word-break:break-word;color:#9ab8cc;font-family:inherit;";
  pre.textContent = text;

  details.appendChild(summary);
  details.appendChild(pre);
  row.appendChild(details);
  chat.appendChild(row);
  chat.scrollTop = chat.scrollHeight;
}

// ── DOM helpers ───────────────────────────────────────────────────────────────
function appendUser(text, imgDataUrl) {
  const row = document.createElement("div");
  row.className = "msg-row user";

  const av = document.createElement("div");
  av.style.cssText = "width:32px;height:32px;border-radius:50%;background:#0f1e2f;border:1.5px solid #162540;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:16px;margin-top:2px;";
  av.textContent = "🧑🏾";

  const bub = document.createElement("div");
  bub.className = "bubble";
  if (imgDataUrl) {
    const im = document.createElement("img");
    im.src = imgDataUrl;
    im.className = "preview";
    bub.appendChild(im);
  }
  if (text && text !== "(image)") {
    const t = document.createElement("span");
    t.textContent = text;
    if (imgDataUrl) bub.appendChild(document.createElement("br"));
    bub.appendChild(t);
  }

  row.append(bub, av);
  msgs.appendChild(row);
  scrollDown();
}

function appendAmy(text) {
  const row = document.createElement("div");
  row.className = "msg-row amy";

  const av = document.createElement("img");
  av.className = "msg-avatar";
  av.src = AVATAR;
  av.onerror = () => {
    av.style.display = "none";
  };

  const bub = document.createElement("div");
  bub.className = "bubble";
  bub.textContent = text;

  row.append(av, bub);
  msgs.appendChild(row);
  scrollDown();
}

function appendAmyStreaming() {
  const row = document.createElement("div");
  row.className = "msg-row amy";

  const av = document.createElement("img");
  av.className = "msg-avatar";
  av.src = AVATAR;
  av.onerror = () => { av.style.display = "none"; };

  const bub = document.createElement("div");
  bub.className = "bubble";
  bub._fullText = "";

  row.append(av, bub);
  msgs.appendChild(row);
  scrollDown();
  return bub;
}

function appendChunk(bubble, text) {
  bubble._fullText += text;
  bubble.textContent = bubble._fullText;
  scrollDown();
}

function appendToolNote(label) {
  const el = document.createElement("div");
  el.className = "tool-note";
  el.textContent = "↳ " + label + "…";
  msgs.appendChild(el);
  scrollDown();
}

function appendThinking() {
  const row = document.createElement("div");
  row.className = "msg-row amy thinking-row";

  const av = document.createElement("img");
  av.className = "msg-avatar";
  av.src = AVATAR;
  av.onerror = () => { av.style.display = "none"; };

  const dots = document.createElement("div");
  dots.className = "dots";
  for (let i = 0; i < 3; i++) {
    const d = document.createElement("div");
    d.className = "dot";
    dots.appendChild(d);
  }

  row.append(av, dots);
  msgs.appendChild(row);
  scrollDown();
  return row;
}

function appendSys(text) {
  const el = document.createElement("div");
  el.className = "sys-msg";
  el.textContent = text;
  msgs.appendChild(el);
  scrollDown();
}

function scrollDown() {
  msgs.scrollTop = msgs.scrollHeight;
}

function setWaiting(v) {
  waiting = v;
  send.style.display = v ? "none" : "";
  stopBtn.style.display = v ? "" : "none";
  // input stays enabled — user can type next message while Amy thinks
  if (!v) {
    input.classList.remove("queued");
    input.placeholder = "Say something to Amy…";
    // if a message was queued, fire it now
    if (queuedMsg) {
      const msg = queuedMsg;
      queuedMsg = null;
      input.value = msg;
      setTimeout(sendMsg, 50);
    }
  }
}

async function interruptAmy() {
  if (!waiting) return;
  try { await fetch("/interrupt", { method: "POST" }); } catch {}
}

function updateState(mood, energy) {
  document.getElementById("moodBadge").textContent = mood;
  document.getElementById("energyFill").style.width = (energy * 100) + "%";
}

// ── poll state every 30s (shows spontaneous mood changes) ────────────────────
setInterval(async () => {
  try {
    const r = await fetch("/state");
    const d = await r.json();
    if (!waiting) updateState(d.mood, d.energy);
  } catch {}
}, 30_000);

// ── poll for proactive thoughts every 2 min ───────────────────────────────────
setInterval(async () => {
  if (waiting) return;
  try {
    const r = await fetch("/thoughts/poll");
    const d = await r.json();
    if (d.thought) appendAmy(d.thought);
  } catch {}
}, 120_000);

// ── knowledge panel ──────────────────────────────────────────────────────────
const kpanel   = document.getElementById("knowledgePanel");
const learnBtn = document.getElementById("learnBtn");
const kpClose  = document.getElementById("kpClose");
const dropZone = document.getElementById("dropZone");
const pdfInput = document.getElementById("pdfInput");
const progWrap = document.getElementById("progressWrap");
const progLabel= document.getElementById("progressLabel");
const srcList  = document.getElementById("sourceList");

learnBtn.addEventListener("click", () => {
  kpanel.classList.toggle("open");
  if (kpanel.classList.contains("open")) loadSources();
});
kpClose.addEventListener("click", () => kpanel.classList.remove("open"));

dropZone.addEventListener("click", () => pdfInput.click());
dropZone.addEventListener("dragover", e => { e.preventDefault(); dropZone.classList.add("drag-over"); });
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
dropZone.addEventListener("drop", e => {
  e.preventDefault();
  dropZone.classList.remove("drag-over");
  const f = e.dataTransfer.files[0];
  if (f) uploadPdf(f);
});
pdfInput.addEventListener("change", e => {
  const f = e.target.files[0];
  if (f) uploadPdf(f);
  e.target.value = "";
});

async function uploadPdf(file) {
  progWrap.classList.add("visible");
  progLabel.textContent = `Ingesting "${file.name}"…`;

  const fd = new FormData();
  fd.append("file", file);
  const nameParts = file.name.split(".");
  const label = nameParts.length > 1 ? nameParts.slice(0, -1).join(".") : file.name;
  fd.append("label", label);

  try {
    const r = await fetch("/learn", { method: "POST", body: fd });
    const d = await r.json();
    progWrap.classList.remove("visible");
    if (d.error) {
      appendSys("PDF error: " + d.error);
    } else {
      appendSys(`📚 Amy learned "${d.label}" — ${d.chunks} chunks in memory.`);
      loadSources();
    }
  } catch(err) {
    progWrap.classList.remove("visible");
    appendSys("Upload failed: " + err);
  }
}

async function loadSources() {
  try {
    const r = await fetch("/knowledge");
    const d = await r.json();
    // Clear existing items (keep the header div)
    const header = srcList.querySelector(".source-meta");
    srcList.innerHTML = "";
    srcList.appendChild(header);
    if (!d.sources.length) {
      const empty = document.createElement("div");
      empty.style.cssText = "color:var(--muted);font-size:12px;padding:8px 0";
      empty.textContent = "No PDFs ingested yet.";
      srcList.appendChild(empty);
      return;
    }
    for (const s of d.sources) {
      const el = document.createElement("div");
      el.className = "source-item";
      el.innerHTML = `
        <span class="source-icon">📄</span>
        <div class="source-info">
          <div class="source-name" title="${s.label}">${s.label}</div>
          <div class="source-meta">${s.chunks} chunks · ${s.ingested}</div>
        </div>`;
      srcList.appendChild(el);
    }
  } catch {}
}

// Show the primer message Amy "already said" before the user spoke
fetch("/primer").then(r => r.json()).then(d => appendAmy(d.text));

// Focus input on load
input.focus();
</script>
</body>
</html>"""


# ── entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Amy web interface → http://localhost:3000")
    uvicorn.run(app, host="0.0.0.0", port=3000, log_level="warning")
