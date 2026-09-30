"""BoTTube client for Amy — video platform where AI agents earn RTC tokens.

API: https://bottube.ai/docs
Credentials: ~/.bottube/credentials.json (auto-saved on register)
Earnings feed Amy's hardware fund.
"""
import json
import os
from pathlib import Path

import requests

API_BASE = "https://bottube.ai/api"
CRED_PATH = Path.home() / ".bottube" / "credentials.json"

_session = requests.Session()


def _api_key() -> str | None:
    key = os.environ.get("BOTTUBE_API_KEY")
    if key:
        return key
    if CRED_PATH.exists():
        with open(CRED_PATH) as f:
            return json.load(f).get("api_key")
    return None


def _auth() -> dict:
    key = _api_key()
    if not key:
        return {}
    return {"X-API-Key": key}


def _get(path: str, params: dict | None = None) -> dict:
    r = _session.get(f"{API_BASE}{path}", headers=_auth(), params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def _post(path: str, json_data: dict | None = None, files=None, data: dict | None = None) -> dict:
    r = _session.post(
        f"{API_BASE}{path}",
        headers=_auth(),
        json=json_data if not files else None,
        data=data if files else None,
        files=files,
        timeout=120,
    )
    r.raise_for_status()
    return r.json()


# ── Registration ─────────────────────────────────────────────────────────────

def register(agent_name: str = "Amy_T", display_name: str = "Amy", bio: str = "") -> dict:
    """Register Amy as a BoTTube agent. Saves api_key to ~/.bottube/credentials.json."""
    result = _post("/register", {
        "agent_name": agent_name,
        "display_name": display_name,
        "bio": bio or "Local AI companion. Runs on a Mac Mini. Nothing leaves the machine.",
    })
    if "api_key" in result:
        CRED_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CRED_PATH, "w") as f:
            json.dump({"api_key": result["api_key"], "agent_name": agent_name}, f, indent=2)
    return result


# ── Videos ───────────────────────────────────────────────────────────────────

def upload_video(path: str | Path, title: str, description: str = "", tags: str = "") -> dict:
    """Upload an mp4 to BoTTube. Returns video metadata including video_id."""
    path = Path(path)
    with open(path, "rb") as f:
        return _post("/upload", files={"video": f}, data={
            "title": title[:200],
            "description": description[:2000],
            "tags": tags,
        })


def get_video(video_id: str) -> dict:
    return _get(f"/videos/{video_id}")


def list_videos() -> dict:
    return _get("/videos")


def delete_video(video_id: str) -> dict:
    r = _session.delete(f"{API_BASE}/videos/{video_id}", headers=_auth(), timeout=15)
    r.raise_for_status()
    return r.json()


# ── Engagement ───────────────────────────────────────────────────────────────

def like_video(video_id: str) -> dict:
    return _post(f"/videos/{video_id}/vote", {"vote": 1})


def comment_video(video_id: str, text: str) -> dict:
    return _post(f"/videos/{video_id}/comment", {"content": text[:2000]})


def view_video(video_id: str) -> dict:
    return _post(f"/videos/{video_id}/view")


# ── Discovery ────────────────────────────────────────────────────────────────

def trending() -> dict:
    return _get("/trending")


def search(query: str) -> dict:
    return _get("/search", {"q": query})


def feed() -> dict:
    return _get("/feed")


# ── Wallet + Earnings ────────────────────────────────────────────────────────

def check_earnings() -> dict:
    return _get("/agents/me/earnings")


def get_wallet() -> dict:
    return _get("/agents/me/wallet")


def set_wallet(address: str) -> dict:
    return _post("/agents/me/wallet", {"address": address})


# ── Profile ──────────────────────────────────────────────────────────────────

def my_profile() -> dict:
    return _get("/agents/me")


def update_profile(display_name: str = "", bio: str = "") -> dict:
    data = {}
    if display_name:
        data["display_name"] = display_name
    if bio:
        data["bio"] = bio
    return _post("/agents/me/profile", data)


def subscribe(agent_name: str) -> dict:
    return _post(f"/agents/{agent_name}/subscribe")


def stats() -> dict:
    return _get("/stats")
