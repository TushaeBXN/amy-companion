# Fix 003 — Running worker.py directly does nothing

**Date:** 2026-10-03  
**Symptom:** Running `python3 worker.py` starts and immediately exits with no output and no error. Telegram goes silent.  
**Affected:** Anyone trying to start Amy manually

---

## What broke

`worker.py` is a module, not a standalone script. It exports a `start()` function that must be called by `telegram_amy.py`. Running it directly hits no `if __name__ == "__main__"` block, so Python just imports the module and exits immediately.

---

## Root cause

The entry point was split across two files:
- `telegram_amy.py` — starts the Telegram bot AND calls `worker.start()`
- `worker.py` — provides the task loop, but only when told to start

Running `worker.py` directly skips the Telegram setup and never calls its own `start()`.

---

## The correct command

```bash
cd /Users/anthosintelligence/ava-companion && venv/bin/python3 telegram_amy.py
```

This starts both Telegram and the worker together.

---

## Secondary issue: using system python3

The system `python3` at `/Library/Frameworks/Python.framework/Versions/3.15/bin/python3` does not have the project's dependencies installed. Always use the project venv:

```bash
venv/bin/python3 telegram_amy.py
```

The alias `amy` can be set up with:
```bash
echo 'alias amy="cd /Users/anthosintelligence/ava-companion && venv/bin/python3 telegram_amy.py"' >> ~/.zshrc && source ~/.zshrc
```

---

## What to watch for next time

- If Telegram goes silent and the process exits immediately — check which file was run
- If you get `ModuleNotFoundError: No module named 'ollama'` — you're using the wrong Python, use `venv/bin/python3`
- If you get `Conflict: terminated by other getUpdates request` — two instances of `telegram_amy.py` are running, kill them all with `pkill -f "telegram_amy"` and restart once

---

## Pattern: Multiple instances conflict

Telegram only allows one bot polling connection at a time. Running `telegram_amy.py` twice causes a `Conflict` error and both instances fail. Always `pkill -f "telegram_amy"` before restarting.
