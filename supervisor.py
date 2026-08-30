"""supervisor.py — Supervised background thread wrapper for Amy.

Replaces bare daemon threads. Any supervised thread that crashes is
automatically restarted after a backoff delay, and the failure is
logged so you can actually see what went wrong.

Usage:
    from supervisor import SupervisedThread

    SupervisedThread(
        target=my_function,
        args=(arg1, arg2),
        name="my-thread",
        restart_delay=5.0,   # seconds before restart (default 5)
        max_restarts=10,     # give up after N consecutive crashes (default unlimited)
    ).start()
"""

import threading
import time
import traceback

try:
    from logger import log
except ImportError:
    import json, sys
    def log(event, **kwargs):
        print(json.dumps({"event": event, **kwargs}), file=sys.stderr)


class SupervisedThread:
    """A daemon thread that restarts its target function on failure.

    Parameters
    ----------
    target : callable
        The function to run. Should loop internally (like a background
        worker). If it returns normally, the thread is considered done
        and will NOT be restarted.
    args : tuple
        Positional arguments forwarded to target.
    kwargs : dict
        Keyword arguments forwarded to target.
    name : str
        Thread name — appears in logs and stack traces.
    restart_delay : float
        Seconds to wait after a crash before restarting (default 5).
    max_restarts : int | None
        Maximum consecutive crash+restart cycles before giving up.
        None (default) means restart forever.
    """

    def __init__(
        self,
        target,
        args=(),
        kwargs=None,
        name="supervised",
        restart_delay=5.0,
        max_restarts=None,
    ):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}
        self._name = name
        self._restart_delay = restart_delay
        self._max_restarts = max_restarts
        self._stop_event = threading.Event()

    def start(self):
        t = threading.Thread(target=self._run, name=f"supervisor:{self._name}", daemon=True)
        t.start()
        return t

    def stop(self):
        """Signal the supervisor loop to stop after the current restart cycle."""
        self._stop_event.set()

    def _run(self):
        restarts = 0
        while not self._stop_event.is_set():
            try:
                log("supervisor.start", thread=self._name, restarts=restarts)
                self._target(*self._args, **self._kwargs)
                # Target returned normally — treat as intentional exit, don't restart.
                log("supervisor.exit", thread=self._name, reason="target_returned")
                return
            except Exception:
                tb = traceback.format_exc()
                restarts += 1
                log(
                    "supervisor.crash",
                    thread=self._name,
                    restarts=restarts,
                    traceback=tb,
                )
                print(
                    f"\n[supervisor] {self._name} crashed (restart #{restarts}):\n{tb}",
                    flush=True,
                )

                if self._max_restarts is not None and restarts >= self._max_restarts:
                    log(
                        "supervisor.gave_up",
                        thread=self._name,
                        restarts=restarts,
                    )
                    print(
                        f"[supervisor] {self._name} exceeded max_restarts "
                        f"({self._max_restarts}) — giving up.",
                        flush=True,
                    )
                    return

                # Exponential backoff, capped at 60s
                delay = min(self._restart_delay * (2 ** min(restarts - 1, 4)), 60.0)
                log("supervisor.backoff", thread=self._name, delay_s=delay)
                print(
                    f"[supervisor] restarting {self._name} in {delay:.0f}s...",
                    flush=True,
                )
                self._stop_event.wait(timeout=delay)
