"""Exclusive-job gate: while a heavy job (FLUX image generation) runs, nothing may load the chat model.

FLUX peaks at ~21.8 GB of GPU memory on a 16 GB Mac. Evicting the chat model once at the start
is not enough: her worker, her chat and her thought threads call Ollama again within seconds
and reload it, which aborted mflux ("Insufficient Memory") and froze the machine on 2026-10-23.

Two phases, so the image request doesn't cut off her own reply:
  pending  Background callers (worker tasks, thoughts, reflection) are refused. Chat calls are
           still allowed, so the turn that asked for the image can finish and answer.
           The gate waits for in-flight calls to end and for a quiet moment.
  active   Everything is refused. The caller unloads the model and runs FLUX.

The gate is a flag file, so Amy's other processes (web server, terminal) honour it too.
A flag left by a dead process is ignored.
"""
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

FLAG = Path(os.environ.get("AMY_HEAVY_JOB_FLAG", str(Path(__file__).resolve().parent / ".heavy_job")))
QUIET_S = 20          # no model call for this long before the machine is taken
WAIT_S = 300          # give up if there is no quiet moment by then


class JobBusy(RuntimeError):
    """A heavy job holds the machine. Raised instead of loading the model."""


_cond = threading.Condition()
_inflight = 0
_last_end = 0.0


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read() -> dict | None:
    """The live flag, or None. A flag whose process has died is stale and removed."""
    try:
        info = json.loads(FLAG.read_text())
    except (FileNotFoundError, ValueError):
        return None
    if not _pid_alive(int(info.get("pid", 0))):
        FLAG.unlink(missing_ok=True)
        return None
    return info


def _write(job: str, phase: str):
    FLAG.write_text(json.dumps({"job": job, "pid": os.getpid(), "phase": phase, "started": time.time()}))


def active() -> str | None:
    """Name of the job if it has taken the machine (phase 'active'), else None."""
    info = _read()
    return (info.get("job") or "a heavy job") if info and info.get("phase") == "active" else None


def held() -> str | None:
    """Name of the job in either phase. The scheduler uses this to start nothing new."""
    info = _read()
    return (info.get("job") or "a heavy job") if info else None


@contextmanager
def model_call(chat: bool = False):
    """Wrap every call that would load or use the chat model.

    chat=True is a live conversation turn: allowed while the job is only pending, so the turn
    that asked for the image can finish. Everything else is refused as soon as a job is pending."""
    global _inflight, _last_end
    with _cond:
        info = _read()
        if info and (info.get("phase") == "active" or not chat):
            raise JobBusy(f"busy: {info.get('job') or 'a heavy job'} is running, so the model is off until it finishes")
        _inflight += 1
    try:
        yield
    finally:
        with _cond:
            _inflight -= 1
            _last_end = time.time()
            _cond.notify_all()


def call(fn, *args, chat: bool = False, **kwargs):
    """Run one model call (e.g. ollama.chat) under the gate."""
    with model_call(chat=chat):
        return fn(*args, **kwargs)


@contextmanager
def exclusive(job: str, quiet_s: float = QUIET_S, wait_s: float = WAIT_S):
    """Take the machine for a heavy job. Raises JobBusy if another job holds it or there is no quiet moment."""
    with _cond:
        if _read():
            raise JobBusy(f"busy: {held()} is already running")
        _write(job, "pending")                       # background callers are refused from here
        deadline = time.time() + wait_s
        while True:
            if _inflight == 0 and time.time() - _last_end >= quiet_s:
                _write(job, "active")                # atomic with admission: nothing can slip in now
                break
            if time.time() >= deadline:
                FLAG.unlink(missing_ok=True)
                raise JobBusy(f"no quiet moment within {wait_s:.0f}s ({_inflight} call(s) running); not starting {job}")
            _cond.wait(timeout=1)
    try:
        yield
    finally:
        FLAG.unlink(missing_ok=True)
        with _cond:
            _cond.notify_all()
