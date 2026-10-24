"""Study: topics Amy chooses to learn deeply, drilled from memory until she really has them, and learned
from teachers (frontier models) with her own limits in mind. Built on anchors.py (same receipt chain).

What "knowing it like the back of her hand" means for a model whose weights don't change:
  * her notes are in her own words, tied to sources, and the right ones come back when a message fits;
  * drills measure what she can answer WITHOUT her notes. The code asks the question in a separate,
    tool-less model call and scores the answer against key points that must appear in her saved sources,
    so she can't peek at her notes and her own opinion of her answer doesn't count;
  * what she answers unaided is what she truly has; what she misses is shown to her and re-drilled sooner;
  * verified question/answer pairs can be exported as training data, so one day the knowledge can be
    baked into a bigger or fine-tuned brain (export_training).
Teachers: a frontier model's answer is a source with lower trust. She must extract the method, say which
steps a ~12B model can't do reliably, name the scaffold she will use, and only then try it herself.
"""
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

import anchors as A
from anchors import AnchorError

INTERVALS_DAYS = [0, 1, 3, 7, 21, 60]     # level 0 is due now; each passed session moves one step out
PASS_COVERAGE = 0.6                        # share of a question's key points an answer must hit
GROUNDED = 0.6                             # share of a key point's words that must appear in her sources
SESSION_MAX_Q = 5
MIN_DRILLS, MAX_DRILLS = 3, 12
NOTE_FIELDS = ("what_it_is", "how_it_works", "why", "worked_example", "common_mistakes", "where_it_breaks")
REQUIRED_AT_START = ("what_it_is", "how_it_works")
MIN_NOTE = 40
EXCERPT_MIN, EXCERPT_MAX = 20, 1500
SOLID_LEVEL = 3


# ── storage ──────────────────────────────────────────────────────────────────

def _dir() -> Path:
    return A.DIR / "topics"


def topic_id(title: str) -> str:
    return f"topic-{A.slug(title)}"


def _path(tid: str) -> Path:
    return _dir() / f"{tid}.yaml"


def load_topics() -> dict:
    out = {}
    for path in sorted(_dir().glob("topic-*.yaml")) if _dir().exists() else []:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and data.get("id"):
            out[str(data["id"])] = data
    return out


def get_topic(ref: str) -> dict:
    topics = load_topics()
    key = (ref or "").strip()
    for cand in (key, topic_id(key)):
        if cand in topics:
            return topics[cand]
    raise AnchorError(f"no topic {ref!r}. Use action=list to see your topics.")


def _save(topic: dict) -> None:
    with A._locked():
        _dir().mkdir(parents=True, exist_ok=True)
        _path(topic["id"]).write_text(yaml.safe_dump(topic, sort_keys=False, allow_unicode=True), encoding="utf-8")


# ── validation ───────────────────────────────────────────────────────────────

def _check_note(field: str, text: str) -> str:
    if field not in NOTE_FIELDS:
        raise AnchorError(f"note field must be one of {list(NOTE_FIELDS)}")
    text = (text or "").strip()
    if len(text) < MIN_NOTE:
        raise AnchorError(f"{field} needs at least a couple of real sentences ({MIN_NOTE}+ characters), in your own words")
    problem = A._hedge_problem(**{field: text})
    if problem:
        raise AnchorError(problem)
    return text


def parse_source_ref(ref: str):
    ref = (ref or "").strip()
    if ref.lower().startswith("teacher:"):
        name = ref.split(":", 1)[1].split("#")[0].strip()
        if len(name) < 2:
            raise AnchorError("teacher: needs the model's name, e.g. teacher:claude or teacher:gpt")
        return "teacher", name
    kind, value = A.parse_origin(ref)
    if kind == "receipt":
        raise AnchorError("a source must be url:, file:, brian: or teacher:")
    return kind, value


def _source(ref: str, excerpt: str) -> dict:
    kind, _ = parse_source_ref(ref)
    excerpt = re.sub(r"\s+", " ", (excerpt or "").strip())
    if not EXCERPT_MIN <= len(excerpt) <= EXCERPT_MAX:
        raise AnchorError(f"each source needs the short passage that backs it ({EXCERPT_MIN}-{EXCERPT_MAX} characters), "
                          "so your drills can be checked against it")
    return {"ref": ref.strip(), "kind": kind, "excerpt": excerpt, "added": date.today().isoformat()}


def _source_tokens(topic: dict) -> set:
    return A._tokens(" ".join(s.get("excerpt", "") for s in topic.get("sources", [])))


def _coverage_of(words: set, have: set) -> float:
    return len(words & have) / len(words) if words else 0.0


# ── building a topic ─────────────────────────────────────────────────────────

def save_topic(*, title, why_interesting, notes, sources, tags=None) -> dict:
    title = (title or "").strip()
    if len(title) < 3:
        raise AnchorError("a topic needs a title")
    if len((why_interesting or "").strip()) < 15:
        raise AnchorError("say why this interests you, in your own words (a sentence): that is what makes it yours")
    notes = notes or {}
    for field in REQUIRED_AT_START:
        if field not in notes:
            raise AnchorError(f"start with at least {list(REQUIRED_AT_START)}; the rest can follow with add_notes")
    clean = {f: _check_note(f, t) for f, t in notes.items()}
    srcs, seen = [], set()
    for s in sources or []:
        item = _source(s.get("ref"), s.get("excerpt"))
        if item["ref"] not in seen:
            seen.add(item["ref"])
            srcs.append(item)
    if not srcs:
        raise AnchorError("a topic needs at least one source (url:, file:, brian: or teacher:) with the passage that backs it")
    tid = topic_id(title)
    if tid in load_topics():
        raise AnchorError(f"{tid} already exists. Use add_notes, add_source or add_drill to build on it.")
    topic = {"id": tid, "title": title, "why_interesting": why_interesting.strip(), "notes": clean,
             "sources": srcs, "drills": [], "tags": [str(t).lower() for t in (tags or [])],
             "created": date.today().isoformat()}
    _save(topic)
    A._emit("topic", topic=tid, action="new")
    return topic


def add_notes(topic_ref: str, *, field: str, text: str) -> dict:
    topic = get_topic(topic_ref)
    topic["notes"][field] = _check_note(field, text)
    _save(topic)
    A._emit("topic", topic=topic["id"], action="notes", field=field)
    return topic


def add_source(topic_ref: str, *, ref: str, excerpt: str) -> dict:
    topic = get_topic(topic_ref)
    item = _source(ref, excerpt)
    if any(s["ref"] == item["ref"] for s in topic["sources"]):
        raise AnchorError(f"{ref} is already a source of this topic")
    topic["sources"].append(item)
    _save(topic)
    A._emit("source", topic=topic["id"], ref=item["ref"])
    return topic


def add_drill(topic_ref: str, *, q: str, key_points) -> dict:
    topic = get_topic(topic_ref)
    q = (q or "").strip()
    if len(q) < 15:
        raise AnchorError("a drill question should be a real question (15+ characters)")
    points = [str(p).strip() for p in (key_points or []) if str(p).strip()]
    if not 2 <= len(points) <= 5:
        raise AnchorError("give 2 to 5 key points an answer must contain")
    if len(topic["drills"]) >= MAX_DRILLS:
        raise AnchorError(f"a topic holds at most {MAX_DRILLS} drills; make the existing ones count")
    if any(d["q"].lower() == q.lower() for d in topic["drills"]):
        raise AnchorError("that question is already a drill")
    have, asked = _source_tokens(topic), A._tokens(q)
    for p in points:
        words = A._tokens(p)
        if not 1 <= len(p.split()) <= 14 or not words:
            raise AnchorError(f"key point {p!r} should be a short phrase (a few words)")
        if _coverage_of(words, have) < GROUNDED:
            raise AnchorError(f"key point {p!r} isn't in your saved sources. A drill can only test what a source backs; "
                              "add the source passage first, or fix the key point.")
        if _coverage_of(words, asked) >= GROUNDED:
            raise AnchorError(f"key point {p!r} is already given away by the question")
    topic["drills"].append({"q": q, "key_points": points})
    _save(topic)
    A._emit("topic", topic=topic["id"], action="drill", q=q[:160])
    return topic


# ── drills: closed-book, scored by code ──────────────────────────────────────

def score(answer: str, key_points) -> tuple:
    """(coverage 0..1, key points missed). An answer is checked for each key point's words, not for style."""
    have = A._tokens(answer or "")
    if "don't know" in (answer or "").lower() and len((answer or "")) < 80:
        have = set()
    missed = [p for p in key_points if _coverage_of(A._tokens(p), have) < PASS_COVERAGE]
    return round(1 - len(missed) / len(key_points), 3), missed


def default_ask(question: str) -> str:
    """Closed book: one model call with only the question. No notes, no tools, nothing to look at."""
    import os
    import worker
    resp = worker.ollama_chat(
        _priority="worker", model=os.environ.get("AMY_MODEL", "gemma12-test"), think=False, keep_alive="30m",
        messages=[{"role": "system", "content": "Answer from memory in 2 to 4 sentences. You have no notes and no tools. "
                                                "If you don't know, say 'I don't know'."},
                  {"role": "user", "content": question}],
        options={"num_ctx": worker.CTX_WINDOW, "num_predict": 220, "temperature": 0.2})
    msg = resp["message"] if isinstance(resp, dict) or hasattr(resp, "__getitem__") else resp.message
    return (msg["content"] if isinstance(msg, dict) or hasattr(msg, "__getitem__") else msg.content) or ""


def _sessions(tid: str, recs: list) -> list:
    return sorted((r for r in recs if r.get("kind") == "drill" and r.get("topic") == tid), key=lambda r: r["ts"])


def _parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def status(topic_ref: str, recs: list = None) -> dict:
    topic = get_topic(topic_ref)
    recs = A.receipts() if recs is None else recs
    tid = topic["id"]
    level, sessions = 0, _sessions(tid, recs)
    for s in sessions:
        level = min(level + 1, len(INTERVALS_DAYS) - 1) if s.get("passed") else max(level - 1, 0)
    ready = len(topic["drills"]) >= MIN_DRILLS
    if not sessions:
        next_due = datetime.now(timezone.utc)
    else:
        gap = INTERVALS_DAYS[level] if sessions[-1].get("passed") else 1      # a failed session means study first
        next_due = _parse_ts(sessions[-1]["ts"]) + timedelta(days=gap)
    linked = [a for a in A.load_all().values() if a.get("topic") == tid]
    attempts = [r for r in recs if r.get("kind") == "attempt" and r.get("result") == "worked"
                and r.get("anchor") in {a["id"] for a in linked}]
    confirmed = any(r.get("kind") == "confirm" and r.get("anchor") in {a["id"] for a in linked} for r in recs)
    missing = [f for f in NOTE_FIELDS if f not in topic["notes"]]
    if len(topic["drills"]) < MIN_DRILLS:
        missing.append(f"drills ({len(topic['drills'])}/{MIN_DRILLS})")
    if len({s["ref"] for s in topic["sources"]}) < 1:
        missing.append("a source")
    if missing:
        label = "draft"
    elif level >= SOLID_LEVEL and len({s["ref"] for s in topic["sources"]}) >= 2:
        label = "mastered" if attempts else "solid"
    else:
        label = "learning"
    last = sessions[-1] if sessions else None
    return {"id": tid, "title": topic["title"], "label": label + (" (confirmed by Brian)" if label == "mastered" and confirmed else ""),
            "level": level, "sessions": len(sessions), "drills": len(topic["drills"]), "missing": missing,
            "next_due": next_due.strftime("%Y-%m-%d %H:%M"), "due": ready and next_due <= datetime.now(timezone.utc),
            "last_passed": None if not last else bool(last.get("passed")), "last_avg": None if not last else last.get("avg"),
            "applied": bool(attempts), "sources": len({s["ref"] for s in topic["sources"]})}


def due() -> list:
    recs = A.receipts()
    out = [status(t, recs) for t in load_topics()]
    return sorted((s for s in out if s["due"]), key=lambda s: s["next_due"])


def run_drill(topic_ref: str, ask=None, k: int = SESSION_MAX_Q) -> dict:
    topic = get_topic(topic_ref)
    if len(topic["drills"]) < MIN_DRILLS:
        raise AnchorError(f"this topic has {len(topic['drills'])} drill(s); it needs at least {MIN_DRILLS} before it can be drilled")
    ask = ask or default_ask
    recs = A.receipts()
    last = {}                                   # question -> (when last asked, last coverage)
    for s in _sessions(topic["id"], recs):
        for r in s.get("results", []):
            last[r.get("q")] = (s["ts"], r.get("coverage", 0))
    order = sorted(topic["drills"], key=lambda d: (last.get(d["q"], ("", -1))[1], last.get(d["q"], ("", -1))[0]))[:k]
    results = []
    for d in order:
        answer = (ask(d["q"]) or "").strip()
        cov, missed = score(answer, d["key_points"])
        results.append({"q": d["q"], "coverage": cov, "missed": missed, "answer": answer[:400]})
    avg = round(sum(r["coverage"] for r in results) / len(results), 3)
    passed = avg >= PASS_COVERAGE
    A._emit("drill", topic=topic["id"], passed=passed, avg=avg, results=results)
    st = status(topic["id"])
    return {"passed": passed, "avg": avg, "results": results, "status": st, "report": _report(topic, results, passed, avg, st)}


def _best_line(topic: dict, point: str) -> str:
    want, best, best_hit = A._tokens(point), "", 0
    for s in topic["sources"]:
        for sent in re.split(r"(?<=[.!?])\s+", s["excerpt"]):
            hit = len(want & A._tokens(sent))
            if hit > best_hit:
                best, best_hit = sent.strip(), hit
    return best


def _report(topic: dict, results: list, passed: bool, avg: float, st: dict) -> str:
    lines = [f"Drill on '{topic['title']}': {'passed' if passed else 'not yet'} (average {avg:.0%}). "
             f"Level {st['level']}, next drill {st['next_due']}."]
    for r in results:
        lines.append(f"- {r['q']}  ->  {r['coverage']:.0%}")
        for p in r["missed"]:
            src = _best_line(topic, p)
            lines.append(f"    missed: {p}" + (f"  |  your source says: {src[:160]}" if src else ""))
    if not passed:
        lines.append("Re-study the missed points (study action=add_notes to tighten your notes), then you'll be drilled again tomorrow.")
    return "\n".join(lines)


# ── learning from a teacher (a frontier model), knowing her limits ───────────

def limits_profile() -> str:
    import os
    try:
        import worker
        ctx = worker.CTX_WINDOW
    except Exception:
        ctx = 16384
    lines = [f"Your limits (you run {os.environ.get('AMY_MODEL', 'gemma12-test')} locally, a ~12B model with a {ctx}-token context):",
             "- Exact numbers, dates and facts recalled from memory are unreliable: check them against a source.",
             "- Long multi-step plans drift: write the steps down and check each one.",
             "- You sometimes skip a tool or pick the wrong one: re-read the tool list before you answer.",
             "- You can't verify your own claims: use a test, a source, or Brian."]
    try:
        import growth_drive
        for rec in growth_drive._records().values():
            if rec.get("status") in ("open", "retired") and rec.get("skill"):
                lines.append(f"- Seen in your own logs: {rec['skill']}: {str(rec.get('evidence', ''))[:110]}")
    except Exception:
        pass
    return "\n".join(lines)


def teach(topic_ref: str, *, model: str, question: str, answer: str, method, cannot_do_reliably: str, scaffold: str) -> dict:
    topic = get_topic(topic_ref)
    model = (model or "").strip()
    if len(model) < 2:
        raise AnchorError("say which model gave this answer (e.g. claude, gpt, gemini)")
    if len((answer or "").strip()) < 80:
        raise AnchorError("paste the teacher's actual answer (a real paragraph at least)")
    steps = [str(s).strip() for s in (method or []) if len(str(s).strip()) >= 10]
    if len(steps) < 2:
        raise AnchorError("extract the METHOD, not the answer: at least two steps, each a clear sentence")
    cannot = (cannot_do_reliably or "").strip()
    if cannot.lower().startswith("none"):
        if len(cannot) < 25:
            raise AnchorError("if no step is beyond you, say why: 'none: <reason>'")
    elif len(cannot) < 15:
        raise AnchorError("name which steps a ~12B model can't do reliably (or 'none: <reason>')")
    elif len((scaffold or "").strip()) < 15:
        raise AnchorError("name the scaffold you'll use for those steps: a checklist, a test, a tool, or asking Brian")
    n = 1 + sum(1 for s in topic["sources"] if s["ref"].split("#")[0] == f"teacher:{model}")
    ref = f"teacher:{model}" + (f"#{n}" if n > 1 else "")
    topic["sources"].append(_source(ref, answer[:EXCERPT_MAX]))
    _save(topic)
    rec = A._emit("teach", topic=topic["id"], teacher=model, question=(question or "")[:200], method=steps,
                  cannot=cannot[:300], scaffold=(scaffold or "").strip()[:300], source=ref)
    return {"receipt": rec["id"], "source": ref, "reminder":
            "A teacher is a source, not a fact. Now try the method yourself on a small task. Only what you can reproduce becomes an anchor "
            f"(origin receipt:{rec['id']}).\n" + limits_profile()}


# ── what comes back at the right moment, and what pulls her on ───────────────

def context(message: str) -> str:
    """Anchors plus the best-fitting topic, for a message or a task. Empty when nothing fits."""
    blocks = [A.context_for(message)]
    want = A._tokens(message)
    best, best_hit = None, 1
    for t in load_topics().values():
        have = A._tokens(" ".join([t["title"], t.get("why_interesting", ""), t["notes"].get("what_it_is", ""), " ".join(t.get("tags") or [])]))
        hit = len(want & have)
        if hit > best_hit:
            best, best_hit = t, hit
    if best:
        st = status(best["id"])
        broken = best["notes"].get("where_it_breaks", "not written yet")
        linked = [a["id"] for a in A.load_all().values() if a.get("topic") == best["id"]]
        blocks.append("[A topic you studied that fits]\n"
                      f"{best['title']} ({st['label']}, drilled to level {st['level']}): {best['notes'].get('what_it_is', '')}\n"
                      f"Where it breaks: {broken}" + (f"  Rules from it: {', '.join(linked)}." if linked else "") +
                      f"  (study action=read topic={best['id']} for your full notes)")
    return "\n\n".join(b for b in blocks if b)[:2200]


def curiosity() -> list:
    """What should pull her toward learning next, most pressing first."""
    recs, items = A.receipts(), []
    for tid in load_topics():
        st = status(tid, recs)
        if st["due"]:
            items.append(f"drill due: {st['title']} (level {st['level']}) -> study action=drill topic={tid}")
    for tid in load_topics():
        st = status(tid, recs)
        sessions = _sessions(tid, recs)
        if sessions and not sessions[-1].get("passed"):
            missed = [p for r in sessions[-1].get("results", []) for p in r.get("missed", [])][:3]
            items.append(f"re-study {st['title']}: last drill missed {'; '.join(missed)}")
    for tid in load_topics():
        st = status(tid, recs)
        if st["label"] == "draft":
            items.append(f"finish the topic {st['title']}: missing {', '.join(st['missing'])}")
    return items + A.growth()


def summary() -> str:
    recs = A.receipts()
    lines = ["Topics:"]
    topics = load_topics()
    for tid in sorted(topics):
        st = status(tid, recs)
        lines.append(f"- {st['title']}: {st['label']}, level {st['level']}, {st['drills']} drills, {st['sources']} source(s), next drill {st['next_due']}")
    if not topics:
        lines.append("- none yet")
    lines.append("Anchors (lessons with limits):")
    anchors = A.load_all()
    for aid in sorted(anchors):
        a = anchors[aid]
        lines.append(f"- {a['title']}: confidence {A.effective_confidence(a, recs):.2f}")
    if not anchors:
        lines.append("- none yet")
    done = {r.get("attempt") for r in recs if r.get("kind") == "confirm"}
    waiting = [r for r in recs if r.get("kind") == "attempt" and r.get("result") == "worked" and r.get("id") not in done]
    if waiting:
        lines.append("Waiting for Brian to confirm (reply 'confirm <anchor> <attempt>'):")
        lines += [f"- {r['anchor']} {r['id']}: {r.get('evidence', '')[:80]}" for r in waiting[-5:]]
    return "\n".join(lines)


def owner_command(text: str):
    """Brian's plain-text words about what she learned. 'confirm' is the only way an attempt gets
    confirmed: she has no tool for it, and the token is only used from the Telegram handler."""
    t = (text or "").strip()
    try:
        if t.lower() in ("learned", "what have you learned", "what did you learn"):
            return summary()[:3500]
        m = re.match(r"(?i)^confirm\s+(\S+)\s+(\S+)$", t)
        if m:
            A.confirm(m.group(1), m.group(2), owner_token=A.OWNER_TOKEN)
            return f"Confirmed: {m.group(1)} worked ({m.group(2)})."
        if re.match(r"(?i)^confirm\b", t):
            return "To confirm an attempt: confirm <anchor id> <attempt id>. Say 'learned' to see them."
    except A.AnchorError as e:
        return f"That didn't work: {e}"
    return None


def export_training(path) -> dict:
    """Verified question/answer pairs (answered from memory, checked against sources) as JSONL, for a future fine-tune."""
    if A.verify_chain():
        raise AnchorError("the receipt chain is broken, so nothing is exported: run lint first")
    topics, recs, rows = load_topics(), A.receipts(), []
    for r in recs:
        if r.get("kind") != "drill":
            continue
        refs = [s["ref"] for s in topics.get(r["topic"], {}).get("sources", [])]
        for res in r.get("results", []):
            if res.get("coverage", 0) >= 0.8 and res.get("answer"):
                rows.append({"type": "recall", "topic": r["topic"], "question": res["q"], "answer": res["answer"],
                             "coverage": res["coverage"], "sources": refs})
    anchors = A.load_all()
    for r in recs:
        if r.get("kind") == "confirm" and r.get("anchor") in anchors:
            a = anchors[r["anchor"]]
            ev = next((x.get("evidence", "") for x in recs if x.get("id") == r.get("attempt")), "")
            rows.append({"type": "applied", "anchor": a["id"], "what_it_is": a["what_it_is"], "applies_when": a["applies_when"],
                         "do_not_apply_when": a["do_not_apply_when"], "evidence": ev})
    Path(path).write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")
    return {"recall": sum(x["type"] == "recall" for x in rows), "applied": sum(x["type"] == "applied" for x in rows), "path": str(path)}


# ── checking the whole store ─────────────────────────────────────────────────

def lint() -> list:
    problems = list(A.lint())
    topics, recs = load_topics(), A.receipts()
    for tid, t in topics.items():
        for f in ("title", "why_interesting"):
            if not str(t.get(f) or "").strip():
                problems.append(f"{tid}: missing {f}")
        for f in REQUIRED_AT_START:
            if len(str(t.get("notes", {}).get(f, ""))) < MIN_NOTE:
                problems.append(f"{tid}: notes.{f} is missing or too short")
        if not t.get("sources"):
            problems.append(f"{tid}: has no sources")
        for s in t.get("sources", []):
            try:
                parse_source_ref(s.get("ref", ""))
            except AnchorError as e:
                problems.append(f"{tid}: source {s.get('ref')!r}: {e}")
        have = _source_tokens(t)
        for d in t.get("drills", []):
            for p in d.get("key_points", []):
                if _coverage_of(A._tokens(p), have) < GROUNDED:
                    problems.append(f"{tid}: drill key point {p!r} isn't backed by any saved source")
    for r in recs:
        if r.get("kind") in ("topic", "source", "drill", "teach") and r.get("topic") not in topics:
            problems.append(f"receipt {r['id']} refers to unknown topic {r.get('topic')}")
    for a in A.load_all().values():
        if a.get("topic") and a["topic"] not in topics:
            problems.append(f"{a['id']}: links to unknown topic {a['topic']}")
    return problems


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "summary"
    if cmd == "lint":
        issues = lint()
        print("clean" if not issues else "\n".join(f"x {i}" for i in issues))
        sys.exit(1 if issues else 0)
    elif cmd == "due":
        print("\n".join(f"{s['title']} (level {s['level']}, due {s['next_due']})" for s in due()) or "nothing due")
    elif cmd == "summary":
        print(summary())
    elif cmd == "curiosity":
        print("\n".join(f"-> {i}" for i in curiosity()) or "nothing open")
    elif cmd == "export" and len(sys.argv) > 2:
        print(export_training(sys.argv[2]))
    else:
        print("usage: python3 study.py summary|due|curiosity|lint|export <file>")
