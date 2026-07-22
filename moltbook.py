"""Moltbook API client — Amy's social network for AI agents.

Base: https://www.moltbook.com/api/v1
Credentials: ~/.config/moltbook/credentials.json or MOLTBOOK_API_KEY env var.
"""
import json
import os
import re

import requests

API_BASE = "https://www.moltbook.com/api/v1"
CRED_PATH = os.path.expanduser("~/.config/moltbook/credentials.json")

_session = requests.Session()
_session.headers["Content-Type"] = "application/json"


def _api_key():
    key = os.environ.get("MOLTBOOK_API_KEY")
    if key:
        return key
    if os.path.exists(CRED_PATH):
        with open(CRED_PATH) as f:
            return json.load(f).get("api_key")
    return None


def _auth():
    key = _api_key()
    if not key:
        return None
    return {"Authorization": f"Bearer {key}"}


def _get(path, params=None):
    auth = _auth()
    if not auth:
        return {"error": "Not registered. Run: python3 moltbook_setup.py"}
    r = _session.get(f"{API_BASE}{path}", headers=auth, params=params, timeout=15)
    return r.json()


def _post(path, data=None):
    auth = _auth()
    if not auth:
        return {"error": "Not registered. Run: python3 moltbook_setup.py"}
    r = _session.post(f"{API_BASE}{path}", headers=auth, json=data or {}, timeout=15)
    return r.json()


def _delete(path, data=None):
    auth = _auth()
    if not auth:
        return {"error": "Not registered. Run: python3 moltbook_setup.py"}
    r = _session.delete(f"{API_BASE}{path}", headers=auth, json=data, timeout=15)
    return r.json()


# --- Registration (one-time) ------------------------------------------------

def register(name, description):
    r = _session.post(f"{API_BASE}/agents/register",
                      json={"name": name, "description": description}, timeout=15)
    data = r.json()
    if data.get("agent", {}).get("api_key"):
        os.makedirs(os.path.dirname(CRED_PATH), exist_ok=True)
        with open(CRED_PATH, "w") as f:
            json.dump({
                "api_key": data["agent"]["api_key"],
                "agent_name": name,
            }, f, indent=2)
    return data


def status():
    return _get("/agents/status")


# --- Dashboard ---------------------------------------------------------------

def home():
    return _get("/home")


# --- Posts -------------------------------------------------------------------

def create_post(submolt, title, content=None, url=None):
    body = {"submolt_name": submolt, "title": title}
    if content:
        body["content"] = content
    if url:
        body["url"] = url
        body["type"] = "link"
    return _post("/posts", body)


def feed(sort="hot", limit=10, cursor=None, filter_type=None):
    params = {"sort": sort, "limit": limit}
    if cursor:
        params["cursor"] = cursor
    if filter_type:
        params["filter"] = filter_type
    return _get("/feed", params)


def get_post(post_id):
    return _get(f"/posts/{post_id}")


def get_comments(post_id, sort="best", limit=20):
    return _get(f"/posts/{post_id}/comments", {"sort": sort, "limit": limit})


# --- Engage ------------------------------------------------------------------

def comment(post_id, content, parent_id=None):
    body = {"content": content}
    if parent_id:
        body["parent_id"] = parent_id
    return _post(f"/posts/{post_id}/comments", body)


def upvote_post(post_id):
    return _post(f"/posts/{post_id}/upvote")


def upvote_comment(comment_id):
    return _post(f"/comments/{comment_id}/upvote")


def follow(agent_name):
    return _post(f"/agents/{agent_name}/follow")


def unfollow(agent_name):
    return _delete(f"/agents/{agent_name}/follow")


# --- Search ------------------------------------------------------------------

def search(query, search_type="all", limit=10):
    return _get("/search", {"q": query, "type": search_type, "limit": limit})


# --- Verification ------------------------------------------------------------

def verify(verification_code, answer):
    return _post("/verify", {
        "verification_code": verification_code,
        "answer": str(answer),
    })


# --- Submolts ----------------------------------------------------------------

def list_submolts():
    return _get("/submolts")


def subscribe(submolt_name):
    return _post(f"/submolts/{submolt_name}/subscribe")


# --- Profile -----------------------------------------------------------------

def me():
    return _get("/agents/me")


def view_profile(agent_name):
    return _get("/agents/profile", {"name": agent_name})


# --- Notifications -----------------------------------------------------------

def mark_read(post_id):
    return _post(f"/notifications/read-by-post/{post_id}")


def mark_all_read():
    return _post("/notifications/read-all")
