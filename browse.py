"""Amy's web browsing: the part that runs inside her process (see browse_worker.py for the browser).

The browser lives in its own process so a crash or a leak can't take Amy down. It starts on
the first use, restarts if it dies, is killed if it hangs, and shuts itself down after a few idle
minutes (a page costs 0.5-1 GB, and her model needs the memory), and is restarted if it ever grows
past 1.5 GB. It refuses to run while an image job holds the machine."""
import json
import os
import select
import subprocess
import sys
import threading
import time
from pathlib import Path

import heavy_job

HERE = Path(__file__).resolve().parent
LOG = HERE / "logs" / "browse.log"
IDLE_S = 180
CALL_TIMEOUT_S = 45
MAX_TREE_MB = 1500


class BrowseError(RuntimeError):
    pass


class Browser:
    def __init__(self, cmd=None, allow_private: bool = False, idle_s: float = IDLE_S,
                 call_timeout: float = CALL_TIMEOUT_S, max_mb: float = MAX_TREE_MB):
        self.cmd = cmd or [sys.executable, str(HERE / "browse_worker.py")] + (["--allow-private"] if allow_private else [])
        self.idle_s, self.call_timeout, self.max_mb = idle_s, call_timeout, max_mb
        self._lock = threading.Lock()
        self._proc = None
        self._timer = None
        self._next_id = 0

    # -- process management -----------------------------------------------------------
    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _start(self):
        self._proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, text=True, bufsize=1, cwd=str(HERE))

    def _tree_mb(self) -> float:
        """Memory of the browser and everything it started (Chromium, Playwright's driver)."""
        try:
            out = subprocess.run(["ps", "-axo", "pid=,ppid=,rss="], capture_output=True, text=True, timeout=5).stdout
            kids, rss = {}, {}
            for line in out.splitlines():
                parts = line.split()
                if len(parts) == 3:
                    pid, ppid, r = map(int, parts)
                    kids.setdefault(ppid, []).append(pid)
                    rss[pid] = r
            total, stack = 0, [self._proc.pid]
            while stack:
                p = stack.pop()
                total += rss.get(p, 0)
                stack += kids.get(p, [])
            return total / 1024
        except Exception:
            return 0.0

    def _kill(self):
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.stdin.write(json.dumps({"action": "close"}) + "\n")
                proc.stdin.flush()
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
                try:
                    proc.wait(timeout=3)
                except Exception:
                    pass

    def _arm_idle_timer(self):
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(self.idle_s, self.shutdown)
        self._timer.daemon = True
        self._timer.start()

    def shutdown(self):
        """Close the browser and free its memory. Safe to call any time."""
        if self._timer:
            self._timer.cancel()
        with self._lock:
            self._kill()

    # -- the call ---------------------------------------------------------------------
    def call(self, **cmd) -> str:
        busy = heavy_job.held()
        if busy:
            return f"Browsing is off while {busy} runs, because it needs the memory. Try again in a few minutes."
        with self._lock:
            if not self._alive():
                self._start()
            self._next_id += 1
            cmd["id"] = self._next_id
            t0 = time.time()
            try:
                self._proc.stdin.write(json.dumps(cmd) + "\n")
                self._proc.stdin.flush()
                ready, _, _ = select.select([self._proc.stdout], [], [], self.call_timeout)
                if not ready:
                    self._kill()
                    raise BrowseError(f"the browser took longer than {self.call_timeout:.0f}s and was stopped")
                line = self._proc.stdout.readline()
                if not line:
                    self._kill()
                    raise BrowseError("the browser closed unexpectedly; it will restart on the next call")
                reply = json.loads(line)
            except (BrokenPipeError, ValueError, OSError) as e:
                self._kill()
                raise BrowseError(f"the browser stopped responding ({type(e).__name__}); it will restart on the next call")
            self._log(cmd, reply, time.time() - t0)
            if self._tree_mb() > self.max_mb:       # a leak or a monster page: start clean next time
                self._kill()
            else:
                self._arm_idle_timer()
            if not reply.get("ok"):
                raise BrowseError(reply.get("error") or "unknown browser error")
            return reply["result"]

    @staticmethod
    def _log(cmd: dict, reply: dict, secs: float):
        """One line per call (action, address or element, outcome), so you can see where she went."""
        try:
            LOG.parent.mkdir(exist_ok=True)
            what = cmd.get("url") or (f"n={cmd.get('n')}" if cmd.get("n") is not None else cmd.get("selector", ""))
            with open(LOG, "a") as f:
                outcome = ("ERROR" if not reply.get("ok") else
                           "BLOCKED" if str(reply.get("result", "")).startswith("Blocked") else "ok")
                f.write(f"{time.strftime('%F %T')} {cmd.get('action')} {what} {outcome} {secs:.1f}s\n")
        except Exception:
            pass


_default = Browser()


def call(**cmd) -> str:
    return _default.call(**cmd)


def shutdown():
    _default.shutdown()
