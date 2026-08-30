"""Amy on Telegram — same memory, goals, tools, and vision as the browser companion."""
import asyncio, base64, io, os, sys
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
_here = os.path.dirname(__file__)
load_dotenv(os.path.join(_here, ".env"))
load_dotenv(os.path.join(_here, ".env.amy"), override=False)

import json, ollama
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

from amy_persona import SYSTEM_PROMPT
from memory import Memory
import state as _state
import tools
try:
    import learnings as _learnings
except ModuleNotFoundError:
    _learnings = None

MODEL        = "amy-hermes"
VISION_MODEL = "gemma4:e2b"
MAX_HOPS     = 6
CTX_WINDOW   = 8192
HISTORY_FILE = os.path.join(_here, "telegram_history.json")
HISTORY_KEEP = 60   # messages to persist across restarts

mem        = Memory()
amy_state  = _state.InternalState()
_learnings_ctx = _learnings.load_recent_learnings() if _learnings else ""


def _load_history() -> list:
    """Load persisted Telegram history from disk (images already stripped)."""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                data = json.load(f)
            print(f"  [telegram] loaded {len(data)} messages from history")
            return data
        except Exception as e:
            print(f"  [telegram] could not load history: {e}")
    return []


def _msg_to_dict(msg) -> dict:
    """Convert an Ollama Message (Pydantic) or plain dict to a serialisable dict."""
    if hasattr(msg, "model_dump"):
        return msg.model_dump()
    if hasattr(msg, "dict"):
        return msg.dict()
    return dict(msg)


def _save_history(history: list):
    """Persist recent history to disk, stripping any image bytes first."""
    trimmed = []
    for msg in history[-HISTORY_KEEP:]:
        try:
            d = _msg_to_dict(msg)
        except Exception:
            continue
        clean = {k: v for k, v in d.items() if k != "images"}
        if clean.get("role") in ("user", "assistant", "tool"):
            trimmed.append(clean)
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(trimmed, f)
    except Exception as e:
        print(f"  [telegram] could not save history: {e}")


history = _load_history()


def _build_system(history_len: int = 0) -> str:
    s = SYSTEM_PROMPT + "\n\n" + mem.wake_up()
    if _learnings_ctx:
        s += "\n\n" + _learnings_ctx
    s += "\n\n[Internal state]\n" + amy_state.state_summary()
    s += (
        "\n\n[Interface: Telegram — Brian is texting you like a friend. "
        "Be warm, natural, conversational. Short replies are fine. "
        "You remember previous conversations — reference them naturally when relevant.]"
    )
    # If history is thin (fresh start / JSON missing), reconstruct from SQLite
    if history_len < 6:
        recent = mem.recent_telegram(limit=30)
        if recent:
            lines = []
            for role, text, ts in recent:
                label = "Brian" if role == "user" else "Amy"
                ts_str = (ts or "")[:16]
                lines.append(f"[{ts_str}] {label}: {text[:300]}")
            s += (
                "\n\n[Recent Telegram conversation history — reconstructed from memory]\n"
                + "\n".join(lines)
            )
    return s


def _describe_image(image_bytes: bytes) -> str:
    """Run image bytes through the vision model and return a description."""
    b64 = base64.b64encode(image_bytes).decode()
    vision_messages = [
        {"role": "system", "content": (
            "You are a vision assistant. Look at the image carefully and describe "
            "exactly what you see — people, expressions, setting, objects, colors, "
            "mood, any text visible. Be specific and natural, as if describing a photo "
            "a friend sent you. 2-4 sentences."
        )},
        {"role": "user", "content": "What do you see in this image?", "images": [b64]},
    ]
    resp = ollama.chat(model=VISION_MODEL, messages=vision_messages,
                       keep_alive="30m", options={"num_ctx": CTX_WINDOW})
    return resp["message"].get("content", "").strip()


def _chat(user_text: str, image_bytes: bytes | None = None) -> str | None:
    # If a photo was sent, describe it first then fold the description into the message
    if image_bytes:
        print("  [vision] describing Telegram photo...")
        description = _describe_image(image_bytes)
        print(f"  [vision] {description[:100]}...")
        # Durably remember the photo so it survives future restarts
        from datetime import datetime as _dt
        _ts = _dt.now().strftime("%b %d %H:%M")
        mem.remember(f"[Telegram photo — {_ts}] Brian sent a photo. I saw: {description}")
        # Build the message Amy sees — caption + what she actually saw
        caption = f"{user_text}\n\n" if user_text else ""
        user_text = (
            caption
            + f"[Photo from Brian — you looked at it and here's what you see: {description}]\n"
            "React naturally to what's in the photo, like a friend would."
        )

    user_msg = {"role": "user", "content": user_text}
    messages = [{"role": "system", "content": _build_system(len(history))}] + history[-20:] + [user_msg]
    history.append(user_msg)
    mem.add("user", user_text[:500], source="telegram")

    msg = None
    seen = {}
    for _ in range(MAX_HOPS):
        resp = ollama.chat(model=MODEL, messages=messages, tools=tools.TOOLS,
                           keep_alive="30m", options={"num_ctx": CTX_WINDOW})
        msg  = resp["message"]
        messages.append(msg)
        history.append(msg)
        calls = msg.get("tool_calls") or []
        if not calls:
            break
        for call in calls:
            fn   = call["function"]
            name = fn["name"]
            args = fn.get("arguments", {})
            key  = (name, tuple(sorted((k, str(v)) for k, v in args.items()) if isinstance(args, dict) else ()))
            result = seen[key] if key in seen else tools.dispatch(name, args, mem)
            seen[key] = result
            messages.append({"role": "tool", "content": str(result) if result else "done"})
            history.append(messages[-1])

    if msg and msg.get("content"):
        mem.add("assistant", msg["content"], source="telegram")
        amy_state.after_turn()
        _save_history(history)
        return msg["content"]
    return None


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle plain text messages."""
    text = update.message.text if update.message else None
    if not text:
        return
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    loop  = asyncio.get_event_loop()
    reply = await loop.run_in_executor(None, _chat, text, None)
    if reply:
        await update.message.reply_text(reply)


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle photos — with or without a caption."""
    if not update.message:
        return
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # Grab the highest-resolution version Telegram offers
    photo = update.message.photo[-1]
    file  = await ctx.bot.get_file(photo.file_id)
    buf   = io.BytesIO()
    await file.download_to_memory(buf)
    image_bytes = buf.getvalue()

    caption = update.message.caption or ""

    loop  = asyncio.get_event_loop()
    reply = await loop.run_in_executor(None, _chat, caption, image_bytes)
    if reply:
        await update.message.reply_text(reply)


def main():
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        sys.exit("TELEGRAM_TOKEN not set — check your .env.amy file")
    app = Application.builder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    print("Amy is on Telegram. She can now see photos you send her.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
