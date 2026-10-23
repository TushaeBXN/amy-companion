"""Moltbook API client — Amy's social network for AI agents.

Base: https://www.moltbook.com/api/v1
Credentials: ~/.config/moltbook/credentials.json or MOLTBOOK_API_KEY env var.
"""
import json
import math
import os
import re

import requests


# ---------------------------------------------------------------------------
# Verification challenge solver
# The challenges are obfuscated math word problems with two numbers and one
# operation. Strip the junk, parse the numbers, find the op, compute.
# ---------------------------------------------------------------------------

_WORD_NUMS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "thousand": 1000,
}

_ADD_WORDS = {"adds", "add", "plus", "gains", "gain", "increases", "increase",
              "speeds", "accelerates", "faster", "more", "climbs", "climb",
              "rises", "rise", "grew", "grows", "grow"}
_SUB_WORDS = {"subtracts", "subtract", "minus", "loses", "lose", "slows",
              "slow", "decreases", "decrease", "drops", "drop", "less",
              "falls", "fall", "shrinks", "shrink", "reduces", "reduce",
              "decelerates"}
_MUL_WORDS = {"times", "multiplied", "multiplies", "multiply", "scaled",
              "scales", "doubled", "tripled", "groups"}
_DIV_WORDS = {"divided", "divides", "divide", "splits", "split",
              "halved", "shared", "distributes"}
# "how much total/combined" with no explicit operation means add. Checked last, and only
# on exact (repeat-collapsed) matches so a stray word can't trigger it. "per" is a unit
# marker ("meters per second"), not division, so it is deliberately absent above.
_IMPLICIT_ADD_WORDS = {"total", "combined", "together", "altogether", "sum", "overall"}


def _deobfuscate(text):
    """Strip obfuscation characters and normalize."""
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", "", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned


def _fuzzy_num(word):
    """Match a word to a number word even with repeated/extra letters.

    The obfuscation can double or triple letters (e.g. 'twenntyy' for 'twenty',
    'fivve' for 'five'). Collapse runs of the same letter and try matching.
    """
    collapsed = re.sub(r"(.)\1+", r"\1", word)
    if collapsed in _WORD_NUMS:
        return _WORD_NUMS[collapsed]
    # Try removing each single char position for near-misses
    for i in range(len(collapsed)):
        variant = collapsed[:i] + collapsed[i+1:]
        if variant in _WORD_NUMS:
            return _WORD_NUMS[variant]
    return None


def _extract_numbers(words):
    """Pull numbers (digit or word form) from a word list, returns list of floats."""
    nums = []
    i = 0
    while i < len(words):
        w = words[i]
        if re.match(r"^-?\d+\.?\d*$", w):
            nums.append(float(w))
            i += 1
            continue
        val = _WORD_NUMS.get(w) if w in _WORD_NUMS else _fuzzy_num(w)
        if val is not None:
            # Handle compound: "twenty five", "one hundred", "three hundred fifty"
            while i + 1 < len(words):
                nw = words[i + 1]
                next_val = _WORD_NUMS.get(nw) if nw in _WORD_NUMS else _fuzzy_num(nw)
                if next_val is None:
                    break
                if next_val == 100:
                    val *= 100
                elif next_val == 1000:
                    val *= 1000
                elif next_val < val and val >= 20:
                    val += next_val
                else:
                    break
                i += 1
            nums.append(float(val))
        i += 1
    return nums


def _fuzzy_match_op(word, word_set):
    """Check if a word fuzzy-matches any operation keyword."""
    if word in word_set:
        return True
    collapsed = re.sub(r"(.)\1+", r"\1", word)
    if collapsed in word_set:
        return True
    for i in range(len(collapsed)):
        if collapsed[:i] + collapsed[i+1:] in word_set:
            return True
    return False


def _find_op(words):
    """Detect the arithmetic operation from keywords."""
    text = " ".join(words)
    if "speeds up" in text or "sped up" in text:
        return "+"
    if "slows down" in text or "slowed down" in text:
        return "-"
    if "divided by" in text or "split into" in text:
        return "/"
    if "multiplied by" in text or "scaled by" in text:
        return "*"
    for w in words:
        if _fuzzy_match_op(w, _SUB_WORDS):
            return "-"
        if _fuzzy_match_op(w, _ADD_WORDS):
            return "+"
        if _fuzzy_match_op(w, _MUL_WORDS):
            return "*"
        if _fuzzy_match_op(w, _DIV_WORDS):
            return "/"
    for w in words:
        if re.sub(r"(.)\1+", r"\1", w) in _IMPLICIT_ADD_WORDS:
            return "+"
    return None


def _is_keyword(word):
    """Exact (repeat-collapsed) match against any number or operation word."""
    collapsed = re.sub(r"(.)\1+", r"\1", word)
    for vocab in (_WORD_NUMS, _ADD_WORDS, _SUB_WORDS, _MUL_WORDS, _DIV_WORDS):
        for k in vocab:
            if word == k or collapsed == re.sub(r"(.)\1+", r"\1", k):
                return True
    return False


def _merge_fragments(words):
    """Rejoin words the obfuscation shattered with spaces.

    e.g. ['twen', 'ty', 'five'] -> ['twenty', 'five'],
         ['mul', 'tiplies'] -> ['multiplies'], ['t', 'hree'] -> ['three'].
    Only merges when the first fragment isn't already a keyword on its own.
    """
    out = []
    i = 0
    while i < len(words):
        if not _is_keyword(words[i]):
            for n in (4, 3, 2):
                if i + n <= len(words) and _is_keyword("".join(words[i:i + n])):
                    out.append("".join(words[i:i + n]))
                    i += n
                    break
            else:
                out.append(words[i])
                i += 1
        else:
            out.append(words[i])
            i += 1
    return out


def solve_challenge(challenge_text):
    """Solve an obfuscated Moltbook verification challenge. Returns answer string."""
    clean = _deobfuscate(challenge_text)
    words = _merge_fragments(clean.split())
    nums = _extract_numbers(words)
    op = _find_op(words)

    if len(nums) < 2 or op is None:
        return None

    a, b = nums[0], nums[1]
    if op == "+":
        result = a + b
    elif op == "-":
        result = a - b
    elif op == "*":
        result = a * b
    elif op == "/":
        if b == 0:
            return None
        result = a / b
    else:
        return None

    if result == int(result):
        return str(int(result))
    return f"{result:.2f}"

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
    r = _session.get(f"{API_BASE}{path}", headers=auth, params=params, timeout=3)
    return r.json()


def _post(path, data=None):
    auth = _auth()
    if not auth:
        return {"error": "Not registered. Run: python3 moltbook_setup.py"}
    r = _session.post(f"{API_BASE}{path}", headers=auth, json=data or {}, timeout=3)
    return r.json()


def _delete(path, data=None):
    auth = _auth()
    if not auth:
        return {"error": "Not registered. Run: python3 moltbook_setup.py"}
    r = _session.delete(f"{API_BASE}{path}", headers=auth, json=data, timeout=3)
    return r.json()


# --- Registration (one-time) ------------------------------------------------

def register(name, description):
    r = _session.post(f"{API_BASE}/agents/register",
                      json={"name": name, "description": description}, timeout=3)
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


def auto_verify(api_response):
    """If the API response contains a verification challenge, solve and submit it.

    Returns the verify result on success, None if no challenge, or an error string.
    """
    # Check for challenge in post or comment response
    for key in ("post", "comment", "submolt"):
        obj = api_response.get(key, {})
        v = obj.get("verification") if isinstance(obj, dict) else None
        if v and v.get("challenge_text") and v.get("verification_code"):
            answer = solve_challenge(v["challenge_text"])
            if answer is None:
                return (
                    f"could not auto-solve challenge: {v['challenge_text']} | "
                    f"verification_code={v['verification_code']} "
                    f"(expires {v.get('expires_at', 'in 5 min')}). Solve it yourself "
                    "and call moltbook_verify with THIS verification_code "
                    "(not the post id) and the numeric answer, e.g. '75.00'."
                )
            print(f"  [moltbook] solving verification: {answer}")
            return verify(v["verification_code"], answer)
    return None


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
