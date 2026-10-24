"""Ask a stronger model for help: Claude over the API, or a local model such as a small coder.

Her answer is a source, not a fact: it is labelled with the model's name, logged in the workbench, and
she tests what it says by running it. Needs ANTHROPIC_API_KEY in her environment for Claude; local
models need nothing but Ollama. Daily call cap so a loop can't run up a bill."""
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

CLAUDE_MODEL = os.environ.get("AMY_CONSULT_MODEL", "claude-sonnet-5-5")
DAILY_CAP = int(os.environ.get("AMY_CONSULT_DAILY", "30"))
MAX_TOKENS = 2500
URL = "https://api.anthropic.com/v1/messages"
SYSTEM = ("You are helping Amy, a small local AI running on Brian's Mac mini (a ~12B model with a limited context). "
          "She is trying to get something working. Give a direct, correct answer. For code, give complete runnable "
          "code, keep it short, and say exactly how to run it and what output she should see so she can check it. "
          "Say plainly what you are unsure of.")


class ConsultError(RuntimeError):
    pass


def _workbench() -> Path:
    import tools
    return tools.WORKBENCH


def _log_path() -> Path:
    return _workbench() / ".consult_log.jsonl"


def calls_today() -> int:
    try:
        today = time.strftime("%Y-%m-%d")
        return sum(1 for l in _log_path().read_text().splitlines() if json.loads(l)["ts"].startswith(today))
    except (OSError, ValueError, KeyError):
        return 0


def _log(model: str, question: str, answer: str) -> None:
    p = _log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "model": model,
                            "q": question[:300], "a": answer[:600]}) + "\n")


def _claude(question: str, post=None) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise ConsultError("no ANTHROPIC_API_KEY in my environment, so I can't reach Claude. Brian can add one to "
                           ".env.amy. A local model works without it: model='ollama:<name>'")
    body = json.dumps({"model": CLAUDE_MODEL, "max_tokens": MAX_TOKENS, "system": SYSTEM,
                       "messages": [{"role": "user", "content": question}]}).encode()
    req = urllib.request.Request(URL, data=body, headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                                          "content-type": "application/json"})
    try:
        data = (post or _post)(req)
    except urllib.error.HTTPError as e:
        raise ConsultError(f"Claude answered with an error ({e.code}). Try again later or use a local model")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ConsultError(f"couldn't reach Claude: {e}")
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
    if not text:
        raise ConsultError("Claude sent back nothing")
    return text


def _post(req) -> dict:
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def _local(name: str, question: str, chat=None) -> str:
    if chat is None:
        import ollama
        chat = ollama.chat
    try:
        r = chat(model=name, messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}],
                 keep_alive=0, options={"num_ctx": 8192})
    except Exception as e:
        raise ConsultError(f"couldn't use local model {name!r}: {e}. If it isn't installed: ollama pull {name}")
    msg = r["message"] if isinstance(r, dict) else r.message
    text = (msg["content"] if isinstance(msg, dict) else msg.content or "").strip()
    if not text:
        raise ConsultError(f"{name} sent back nothing")
    return text


def ask(question: str, model: str = "claude", *, post=None, chat=None) -> str:
    question = (question or "").strip()
    if len(question) < 15:
        raise ConsultError("ask a real question, with the code or error you're stuck on")
    if calls_today() >= DAILY_CAP:
        raise ConsultError(f"I've used today's {DAILY_CAP} consults. Try again tomorrow, or ask Brian to raise AMY_CONSULT_DAILY")
    model = (model or "claude").strip()
    if model.lower() == "claude":
        answer, label = _claude(question[:12000], post), CLAUDE_MODEL
    elif model.lower().startswith("ollama:") and len(model) > 7:
        label = model[7:].strip()
        answer = _local(label, question[:12000], chat)
    else:
        raise ConsultError("model is 'claude' or 'ollama:<model name>', e.g. ollama:qwen2.5-coder:7b")
    _log(label, question, answer)
    return (f"[Answer from {label}. A teacher's answer is a source, not a fact: run it and see before you trust it.]\n"
            + answer)
