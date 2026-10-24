"""Anchors: what Amy learns, saved with the limits of where it applies, and brought back when it matters.

The loop: observe -> save an anchor -> it is recalled when a message or task matches -> she applies it,
and asks whether the same idea fits somewhere else (a "stretch") -> she tests it and records the result
-> open questions pull her toward learning more (growth list, fed into her drive and free-time tasks).

Design rules (each one exists because the first draft of this idea broke it when tested):
  * Confidence is computed from receipts, never stored, so editing a file changes nothing.
  * Her own "worked" counts for little: a capped bonus, evidence required, repeats count once.
    A "failed" always counts more than a self-reported win. Only Brian's /confirm in Telegram
    gives full weight; confirm() needs a token held only inside the Telegram process.
  * Receipts are hash-chained, so a deleted, edited or fabricated line is detected by lint().
  * Where an anchor came from must be typed and checkable: url:, file: (must exist), receipt:
    (must exist) or brian:. Free text such as "observed in a dream" is rejected.
  * last_reviewed changes only through review(), never as a side effect of a logged attempt.
"""
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

DIR = Path(os.environ.get("AMY_ANCHOR_DIR", str(Path.home() / "ai-knowledge" / "anchors")))

OWNER_TOKEN = secrets.token_hex(16)   # per process: only code inside the Telegram bot can pass it to confirm()

STALE_DAYS = 30
LOW_CONF = 0.5
SELF_CAP = 0.6                # without Brian's confirmation, her own wins can't push an anchor past this
SELF_WIN, SELF_WIN_MAX = 0.05, 10
CONFIRMED_WIN = 0.15
FAILED, INCONCLUSIVE = -0.10, -0.02
ORIGIN_BASE = {"url": 0.4, "file": 0.6, "book": 0.6, "receipt": 0.6, "brian": 0.8}   # starting confidence by source
ORIGIN_CAP = {"url": 0.5, "file": 0.7, "book": 0.7, "receipt": 0.7, "brian": 0.9}
REQUIRED = ["id", "title", "what_it_is", "applies_when", "do_not_apply_when", "breaks_when", "origin"]
STRETCH_KINDS = {"same_mechanism_other_field", "flip_constraint", "scale_up", "scale_down"}
RESULTS = {"worked", "failed", "inconclusive"}
TRIGGERS = {"recurrence", "load_bearing_low_conf", "unexplained_error", "doc_contradicts_memory", "curiosity"}
KINDS = {"add", "recall", "stretch", "attempt", "confirm", "review", "defer", "contradict", "resolve",
         "topic", "source", "drill", "teach"}   # the last four belong to study.py, same chain
HEDGE = re.compile(r"\b(apparently|probably|seems|likely)\b", re.I)
_STOP = set("the a an and or of to in on for with is are be it this that i you my your do does how what when "
            "where why can could would should have has had not no yes at as by from but if so we me".split())

_thread_lock = threading.Lock()
_last_logged: dict = {}       # anchor id -> last time an automatic recall was written (keeps the log quiet)


class AnchorError(ValueError):
    """Something she tried to save or record is invalid. The message says why."""


# ── storage ──────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _receipts_path() -> Path:
    return DIR / "receipts.jsonl"


@contextmanager
def _locked():
    """One writer at a time across threads and across her processes (bot, web server, terminal)."""
    DIR.mkdir(parents=True, exist_ok=True)
    with _thread_lock, open(DIR / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _lines() -> list:
    try:
        return [l for l in _receipts_path().read_text(encoding="utf-8").splitlines() if l.strip()]
    except FileNotFoundError:
        return []


def _sha(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def _emit(kind: str, **fields) -> dict:
    """Append one receipt. Each carries the hash of the line before it (the chain)."""
    with _locked():
        lines = _lines()
        rec = {"id": uuid.uuid4().hex[:12], "ts": _now(), "kind": kind, **fields,
               "prev": _sha(lines[-1]) if lines else "0" * 64}
        with open(_receipts_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    return rec


def receipts() -> list:
    out = []
    for line in _lines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def verify_chain() -> list:
    """Problems with the receipt chain: unreadable lines, unknown kinds, or a link that doesn't match."""
    problems, prev = [], "0" * 64
    for n, line in enumerate(_lines(), 1):
        try:
            rec = json.loads(line)
        except ValueError:
            problems.append(f"receipt line {n} is not valid JSON")
            prev = _sha(line)
            continue
        if rec.get("kind") not in KINDS:
            problems.append(f"receipt line {n} has unknown kind {rec.get('kind')!r}")
        if rec.get("prev") != prev:
            problems.append(f"receipt line {n} breaks the chain (a line before it was changed, removed or inserted)")
        prev = _sha(line)
    return problems


def _anchor_file(anchor_id: str) -> Path:
    return DIR / f"{anchor_id}.yaml"


def load_all() -> dict:
    anchors = {}
    for path in sorted(DIR.glob("anchor-*.yaml")) if DIR.exists() else []:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and data.get("id"):
            anchors[str(data["id"])] = data
    return anchors


def get(anchor_id: str) -> dict:
    anchors = load_all()
    if anchor_id not in anchors:
        raise AnchorError(f"no anchor with id {anchor_id!r}. Use action=list to see the ids.")
    return anchors[anchor_id]


# ── validation ───────────────────────────────────────────────────────────────

def parse_origin(origin: str):
    """('url'|'file'|'book'|'receipt'|'brian', value), or AnchorError. Where it came from must be checkable."""
    origin = (origin or "").strip()
    kind, _, value = origin.partition(":")
    kind, value = kind.strip().lower(), value.strip()
    if kind == "url":
        if not re.match(r"^https?://\S+\.\S+", value):
            raise AnchorError("origin 'url:' needs a real http(s) address, e.g. url:https://example.com/page")
    elif kind == "file":
        if not value or not Path(value).expanduser().exists():
            raise AnchorError(f"origin 'file:' must point to a file that exists; {value!r} doesn't")
    elif kind == "book":
        title, _, num = value.rpartition("#")
        if not title or not num.isdigit():
            raise AnchorError("origin 'book:' looks like book:<title>#<passage number>, e.g. book:Becoming the Hacker#120")
        import bookshelf
        try:
            bookshelf.passage(title, int(num))
        except bookshelf.BookError as e:
            raise AnchorError(f"origin 'book:' must name a passage on your shelf: {e}")
    elif kind == "receipt":
        if not any(r.get("id") == value for r in receipts()):
            raise AnchorError(f"origin 'receipt:' must be an id from your receipts; {value!r} isn't one")
    elif kind == "brian":
        if len(value) < 8:
            raise AnchorError("origin 'brian:' needs what Brian actually told you, in a few words")
    else:
        raise AnchorError("origin must be url:<address>, file:<existing path>, book:<title>#<passage>, receipt:<id> or brian:<what he said>. "
                          "Where you learned it has to be something that can be checked.")
    return kind, value


def _hedge_problem(**texts):
    for name, text in texts.items():
        if text and HEDGE.search(str(text)) and "[inferred]" not in str(text):
            return f"{name} uses hedged wording (apparently/probably/seems/likely). Say what you know, or tag the guess with [inferred]."
    return None


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "untitled"


# ── confidence (computed, never stored) ───────────────────────────────────────

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def effective_confidence(anchor: dict, recs: list = None) -> float:
    recs = receipts() if recs is None else recs
    aid = anchor["id"]
    base = float(anchor.get("confidence", 0.4))
    attempts = {r["id"]: r for r in recs if r.get("kind") == "attempt" and r.get("anchor") == aid}
    confirmed = {r.get("attempt") for r in recs if r.get("kind") == "confirm" and r.get("anchor") == aid
                 and attempts.get(r.get("attempt"), {}).get("result") == "worked"}
    seen, self_wins, fails, inconclusive = set(), 0, 0, 0
    for r in attempts.values():
        if r["result"] == "failed":
            fails += 1
        elif r["result"] == "inconclusive":
            inconclusive += 1
        elif r["id"] not in confirmed:
            key = _norm(r.get("evidence", ""))
            if key not in seen:                      # the same evidence counts once
                seen.add(key)
                self_wins += 1
    score = (base + SELF_WIN * min(self_wins, SELF_WIN_MAX) + CONFIRMED_WIN * len(confirmed)
             + FAILED * fails + INCONCLUSIVE * inconclusive)
    if not confirmed:
        score = min(score, max(base, SELF_CAP))
    return round(max(0.0, min(1.0, score)), 3)


def last_review_date(anchor: dict, recs: list = None) -> date:
    """Only adding, reviewing or Brian's confirming counts. A logged attempt does not."""
    recs = receipts() if recs is None else recs
    stamps = [r["ts"] for r in recs if r.get("anchor") == anchor["id"] and r.get("kind") in ("add", "review", "confirm")]
    if anchor.get("created"):
        stamps.append(str(anchor["created"]) + "T00:00:00Z")
    stamps = [s for s in stamps if len(s) >= 10]
    return date.fromisoformat(max(stamps)[:10]) if stamps else date.min


def is_stale(anchor: dict, recs: list = None) -> bool:
    return (date.today() - last_review_date(anchor, recs)).days > STALE_DAYS


# ── the loop ─────────────────────────────────────────────────────────────────

def add_anchor(*, title, what_it_is, applies_when, do_not_apply_when, breaks_when, origin,
               tags=None, supersedes=None, resolves_deferred=None, topic=None) -> dict:
    fields = {"title": title, "what_it_is": what_it_is, "applies_when": applies_when,
              "do_not_apply_when": do_not_apply_when, "breaks_when": breaks_when, "origin": origin}
    for name, value in fields.items():
        if not str(value or "").strip():
            raise AnchorError(f"{name} is required. A lesson without its limits is a guess.")
    if str(applies_when).strip().lower() == str(do_not_apply_when).strip().lower():
        raise AnchorError("applies_when and do_not_apply_when are the same, which makes the anchor useless")
    problem = _hedge_problem(**fields)
    if problem:
        raise AnchorError(problem)
    kind, _ = parse_origin(origin)
    anchor_id = f"anchor-{slug(title)}"
    anchors = load_all()
    if anchor_id in anchors:
        raise AnchorError(f"{anchor_id} already exists. To change what you know, add a new anchor with supersedes={anchor_id}.")
    if supersedes and supersedes not in anchors:
        raise AnchorError(f"supersedes points at {supersedes!r}, which doesn't exist")
    data = {"id": anchor_id, **{k: str(v).strip() for k, v in fields.items()},
            "tags": [str(t).lower() for t in (tags or [])], "confidence": ORIGIN_BASE[kind],
            "created": date.today().isoformat()}
    if supersedes:
        data["supersedes"] = [supersedes]
    if topic:
        data["topic"] = str(topic)          # the topic this rule came from (study.py checks it exists)
    with _locked():
        _anchor_file(anchor_id).write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    _emit("add", anchor=anchor_id, origin=origin, resolves=resolves_deferred or "")
    return data


def _tokens(text: str) -> set:
    out = set()
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if w in _STOP or len(w) < 3:
            continue
        for suffix in ("ing", "ed", "es", "s"):
            if w.endswith(suffix) and len(w) - len(suffix) >= 3 and not (suffix == "s" and w.endswith("ss")):
                w = w[: -len(suffix)]
                break
        if w.endswith("e") and len(w) > 3:      # so produce / produces / produced all become the same stem
            w = w[:-1]
        out.add(w)
    return out


def _superseded(anchors: dict) -> set:
    return {s for a in anchors.values() for s in (a.get("supersedes") or [])}


def match(text: str, k: int = 2, min_score: int = 2) -> list:
    """The saved anchors that fit this text best, with their current confidence. Pure lookup, writes nothing."""
    want = _tokens(text)
    if not want:
        return []
    anchors, recs = load_all(), receipts()
    gone = _superseded(anchors)
    scored = []
    for aid, a in anchors.items():
        if aid in gone:
            continue
        have = _tokens(" ".join([a["title"], a["what_it_is"], a["applies_when"], " ".join(a.get("tags") or [])]))
        hit = len(want & have) + len(want & _tokens(" ".join(a.get("tags") or [])))
        conf = effective_confidence(a, recs)
        if hit >= min_score and conf >= 0.25:
            scored.append((hit * conf, aid, {**a, "effective_confidence": conf}))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [a for _, _, a in scored[:k]]


def recall(query: str, k: int = 3) -> list:
    """She asked. Always logged: a miss is a question worth chasing."""
    hits = match(query, k=k, min_score=1)
    _emit("recall", query=(query or "")[:200], anchors_hit=[h["id"] for h in hits], none_found=not hits)
    return hits


def context_for(message: str) -> str:
    """What gets added to her context when a message or task matches something she learned."""
    hits = match(message)
    if not hits:
        return ""
    now = time.time()
    fresh = [h["id"] for h in hits if now - _last_logged.get(h["id"], 0) > 3600]
    if fresh:                                    # one automatic receipt per anchor per hour
        _emit("recall", query=(message or "")[:200], anchors_hit=fresh, none_found=False, auto=True)
        for aid in fresh:
            _last_logged[aid] = now
    lines = ["[Things you've learned that may apply]"]
    for h in hits:
        lines.append(f"- {h['title']} (confidence {h['effective_confidence']:.2f}): {h['what_it_is']}")
        lines.append(f"  Use when: {h['applies_when']} Not when: {h['do_not_apply_when']} Breaks when: {h['breaks_when']} [{h['id']}]")
    lines.append("Use it where it fits. Could the same idea work somewhere else here? If you try a new use, "
                 "log it with the anchor tool (action=stretch: kind, idea, test; then action=attempt with the honest result and your evidence).")
    return "\n".join(lines)[:1400]


def stretch(anchor_id: str, *, kind: str, idea: str, test: str) -> dict:
    get(anchor_id)
    if kind not in STRETCH_KINDS:
        raise AnchorError(f"kind must be one of {sorted(STRETCH_KINDS)}")
    if not str(idea or "").strip() or not str(test or "").strip():
        raise AnchorError("a stretch needs a concrete idea AND a test: how would you know it worked?")
    return _emit("stretch", anchor=anchor_id, stretch_kind=kind, idea=idea.strip(), test=test.strip())


def attempt(anchor_id: str, *, result: str, evidence: str = "") -> dict:
    anchor = get(anchor_id)
    if result not in RESULTS:
        raise AnchorError(f"result must be one of {sorted(RESULTS)}")
    if result == "worked" and len((evidence or "").strip()) < 10:
        raise AnchorError("a 'worked' needs evidence: what you saw, a tool result, a URL or a file (at least a sentence).")
    rec = _emit("attempt", anchor=anchor_id, result=result, evidence=(evidence or "").strip()[:400])
    rec["confidence"] = effective_confidence(anchor)
    return rec


def confirm(anchor_id: str, attempt_id: str, *, owner_token: str) -> dict:
    """Brian's word that an attempt really worked. Only the Telegram bot holds the token."""
    if not hmac.compare_digest(str(owner_token), OWNER_TOKEN):
        raise AnchorError("only Brian can confirm, through his message in Telegram")
    get(anchor_id)
    recs = receipts()
    target = next((r for r in recs if r.get("id") == attempt_id and r.get("kind") == "attempt" and r.get("anchor") == anchor_id), None)
    if not target:
        raise AnchorError(f"anchor {anchor_id} has no attempt with id {attempt_id}")
    if target["result"] != "worked":
        raise AnchorError("only an attempt recorded as 'worked' can be confirmed")
    if any(r.get("kind") == "confirm" and r.get("attempt") == attempt_id for r in recs):
        raise AnchorError("that attempt is already confirmed")
    return _emit("confirm", anchor=anchor_id, attempt=attempt_id, confirmed_by="brian")


def review(anchor_id: str, *, note: str) -> dict:
    get(anchor_id)
    if len((note or "").strip()) < 10:
        raise AnchorError("a review needs a note on what you re-checked")
    return _emit("review", anchor=anchor_id, note=note.strip()[:300])


def defer(concept: str, *, trigger: str, why: str) -> dict:
    if trigger not in TRIGGERS:
        raise AnchorError(f"trigger must be one of {sorted(TRIGGERS)}")
    if not str(concept or "").strip() or not str(why or "").strip():
        raise AnchorError("a deferred dive needs the concept and why it is being put off")
    return _emit("defer", concept=concept.strip()[:160], trigger=trigger, why=why.strip()[:200])


def contradict(anchor_id: str, *, evidence: str) -> dict:
    get(anchor_id)
    if len((evidence or "").strip()) < 10:
        raise AnchorError("say what contradicts this anchor and where you saw it")
    return _emit("contradict", anchor=anchor_id, evidence=evidence.strip()[:300])


def resolve(anchor_id: str, *, resolution: str) -> dict:
    get(anchor_id)
    if len((resolution or "").strip()) < 10:
        raise AnchorError("say how the contradiction was resolved")
    return _emit("resolve", anchor=anchor_id, resolution=resolution.strip()[:300])


# ── wanting to learn more ────────────────────────────────────────────────────

def growth() -> list:
    """What should pull her toward free-time learning next."""
    anchors, recs = load_all(), receipts()
    gone = _superseded(anchors)
    items = []
    for aid, a in sorted(anchors.items()):
        if aid in gone:
            continue
        conf = effective_confidence(a, recs)
        if conf < LOW_CONF:
            items.append(f"low-confidence anchor {aid} ({conf:.2f}): find a second source or test it")
        if is_stale(a, recs):
            items.append(f"stale anchor {aid}: not reviewed in {STALE_DAYS}+ days, re-check it")
        opened = sum(r.get("kind") == "contradict" and r.get("anchor") == aid for r in recs)
        closed = sum(r.get("kind") == "resolve" and r.get("anchor") == aid for r in recs)
        if opened > closed:
            items.append(f"unresolved contradiction on {aid}")
    resolved = {r.get("resolves") for r in recs if r.get("kind") == "add" and r.get("resolves")}
    for r in recs:
        if r.get("kind") == "defer" and r["concept"] not in resolved:
            items.append(f"deferred dive: {r['concept']} ({r['trigger']})")
    misses = []
    for r in recs:
        if r.get("kind") == "recall" and r.get("none_found") and r.get("query") and r["query"] not in misses:
            misses.append(r["query"])
    items += [f"recall miss: {q}" for q in misses[-5:]]
    return items


# ── checking the whole store ─────────────────────────────────────────────────

def lint() -> list:
    """Everything wrong with the saved anchors and receipts (an empty list means clean)."""
    problems = list(verify_chain())
    anchors, recs = load_all(), receipts()
    ids = set(anchors)
    for aid, a in anchors.items():
        for f in REQUIRED:
            if not str(a.get(f) or "").strip():
                problems.append(f"{aid}: missing {f}")
        try:
            c = float(a.get("confidence"))
            if not 0 <= c <= 1:
                problems.append(f"{aid}: confidence {c} out of range")
        except (TypeError, ValueError):
            problems.append(f"{aid}: confidence is not a number")
        try:
            kind, _ = parse_origin(a.get("origin", ""))
            if float(a.get("confidence", 0)) > ORIGIN_CAP[kind]:
                problems.append(f"{aid}: confidence {a.get('confidence')} is above what its source allows ({ORIGIN_CAP[kind]} for {kind}:)")
        except AnchorError as e:
            problems.append(f"{aid}: {e}")
        except (TypeError, ValueError):
            pass
        hedge = _hedge_problem(title=a.get("title"), what_it_is=a.get("what_it_is"), applies_when=a.get("applies_when"),
                               do_not_apply_when=a.get("do_not_apply_when"), breaks_when=a.get("breaks_when"))
        if hedge:
            problems.append(f"{aid}: {hedge}")
        for s in a.get("supersedes") or []:
            if s not in ids:
                problems.append(f"{aid}: supersedes unknown anchor {s}")
    attempts = {r["id"]: r for r in recs if r.get("kind") == "attempt"}
    for r in recs:
        if r.get("anchor") and r["anchor"] not in ids:
            problems.append(f"receipt {r['id']} refers to unknown anchor {r['anchor']}")
        if r.get("kind") == "attempt" and r.get("result") == "worked" and len((r.get("evidence") or "").strip()) < 10:
            problems.append(f"receipt {r['id']}: a 'worked' attempt has no evidence")
        if r.get("kind") == "confirm" and attempts.get(r.get("attempt"), {}).get("result") != "worked":
            problems.append(f"receipt {r['id']}: confirms an attempt that isn't a recorded 'worked'")
    return problems


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "lint"
    if cmd == "lint":
        issues = lint()
        print("clean" if not issues else "\n".join(f"x {i}" for i in issues))
        sys.exit(1 if issues else 0)
    elif cmd == "growth":
        print("\n".join(f"-> {i}" for i in growth()) or "nothing open")
    elif cmd == "list":
        recs = receipts()
        for aid, a in sorted(load_all().items()):
            print(f"{aid}  conf {effective_confidence(a, recs):.2f}  {a['title']}")
    else:
        print("usage: python3 anchors.py lint|growth|list")
