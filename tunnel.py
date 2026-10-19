"""tunnel.py — Cloudflare quick tunnel for Amy's agent inbox.

Starts a cloudflared quick tunnel pointing at localhost:3000, captures the
public URL, and writes it to public_endpoint.json so Amy and other modules
can read it.

Usage (standalone):
    python3 tunnel.py          # blocks — run in background with &
    python3 tunnel.py --check  # print current URL from public_endpoint.json

Called by start.sh before starting amy_server.py.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

_HERE   = os.path.dirname(os.path.abspath(__file__))
_STATE  = os.path.join(_HERE, "public_endpoint.json")
_READY  = threading.Event()
_URL: list[str] = []  # [0] set once URL is found

_URL_RE = re.compile(r'https://[a-z0-9\-]+\.trycloudflare\.com', re.IGNORECASE)


# ── public API ────────────────────────────────────────────────────────────────

def get_public_url() -> str | None:
    """Return Amy's current public URL, or None if no tunnel is running."""
    if not os.path.exists(_STATE):
        return None
    try:
        return json.loads(open(_STATE).read()).get("url")
    except Exception:
        return None


def _save_url(url: str):
    with open(_STATE, "w") as f:
        json.dump({"url": url, "agent_endpoint": url + "/agent/message"}, f, indent=2)
    print(f"[tunnel] public URL: {url}", flush=True)
    print(f"[tunnel] agent inbox: {url}/agent/message", flush=True)


def _clear_url():
    if os.path.exists(_STATE):
        os.remove(_STATE)


# ── install ───────────────────────────────────────────────────────────────────

def _ensure_cloudflared():
    if shutil.which("cloudflared"):
        return True
    print("[tunnel] cloudflared not found — installing via Homebrew...")
    result = subprocess.run(["brew", "install", "cloudflared"], capture_output=False)
    if result.returncode != 0:
        print("[tunnel] brew install failed. Install manually: brew install cloudflared", file=sys.stderr)
        return False
    return bool(shutil.which("cloudflared"))


# ── tunnel process ────────────────────────────────────────────────────────────

def _read_output(proc: subprocess.Popen):
    """Stream cloudflared stderr, extract the public URL when it appears."""
    assert proc.stderr is not None
    for raw in proc.stderr:
        line = raw.decode(errors="replace").rstrip()
        print(f"[cloudflared] {line}", flush=True)
        if not _URL:
            m = _URL_RE.search(line)
            if m:
                url = m.group(0).rstrip("/")
                _URL.append(url)
                _save_url(url)
                _READY.set()


def start(local_port: int = 3000, timeout: int = 30) -> str | None:
    """Start the tunnel and block until the public URL is known (or timeout)."""
    if not _ensure_cloudflared():
        return None

    cmd = ["cloudflared", "tunnel", "--url", f"http://localhost:{local_port}"]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    t = threading.Thread(target=_read_output, args=(proc,), daemon=True)
    t.start()

    if not _READY.wait(timeout=timeout):
        print("[tunnel] timed out waiting for public URL", file=sys.stderr)
        proc.terminate()
        return None

    # Keep tunnel alive — run in background thread so caller can proceed
    def _watch():
        proc.wait()
        print("[tunnel] cloudflared exited", flush=True)
        _clear_url()

    threading.Thread(target=_watch, daemon=True).start()
    return _URL[0] if _URL else None


# ── entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Print current URL and exit")
    parser.add_argument("--port", type=int, default=3000)
    args = parser.parse_args()

    if args.check:
        url = get_public_url()
        print(url if url else "(no tunnel running)")
        sys.exit(0)

    url = start(local_port=args.port)
    if not url:
        sys.exit(1)

    # Block forever so the tunnel stays alive (start.sh runs this in background)
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        _clear_url()
