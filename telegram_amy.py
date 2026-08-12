"""Amy on Telegram — same memory, goals, and tools as the terminal companion."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

import ollama
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

MODEL     = "amy-hermes"
MAX_HOPS  = 6

mem        = Memory()
amy_state  = _state.InternalState()
history    = []
_learnings_ctx = _learnings.load_recent_learnings() if _learnings else ""

def _build_system():
    s = SYSTEM_PROMPT + "\n\n" + mem.wake_up()
    if _learnings_ctx:
        s += "\n\n" + _learnings_ctx
    s += "\n\n[Internal state]\n" + amy_state.state_summary()
    return s

def _chat(user_text: str) -> str | None:
    user_msg = {"role": "user", "content": user_text}
    messages = [{"role": "system", "content": _build_system()}] + history[-20:] + [user_msg]
    history.append(user_msg)
    mem.add("user", user_text)

    msg = None
    seen = {}
    for _ in range(MAX_HOPS):
        resp = ollama.chat(model=MODEL, messages=messages, tools=tools.TOOLS, keep_alive="30m")
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
        mem.add("assistant", msg["content"])
        amy_state.after_turn()
        return msg["content"]
    return None

async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text if update.message else None
    if not text:
        return
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    loop  = asyncio.get_event_loop()
    reply = await loop.run_in_executor(None, _chat, text)
    if reply:
        await update.message.reply_text(reply)

def main():
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        sys.exit("TELEGRAM_TOKEN not set — check your .env file")
    app = Application.builder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    print("Amy is on Telegram. Send her a message.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
