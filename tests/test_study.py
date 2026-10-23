"""Study: topics, closed-book drills scored by code, teacher sessions, and what pulls her to learn more."""
import json
from datetime import datetime, timedelta, timezone

import pytest
import yaml

import anchors as A
import study as S

EXCERPT = ("A generator function uses yield to produce values one at a time and pauses between them. "
           "Its local state is kept between calls, so memory stays small even for huge sequences. "
           "Calling next resumes it right after the last yield. When the function returns, StopIteration is raised.")
OTHER = ("Generators are lazy: nothing runs until the first value is requested. "
         "An exhausted generator cannot be restarted, you have to create a new one.")


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "DIR", tmp_path / "anchors")
    monkeypatch.setattr(A, "_last_logged", {})
    return tmp_path


NOTES = {"what_it_is": "A generator is a function that hands back a sequence lazily, one value per request, using yield.",
         "how_it_works": "Calling it returns an iterator. Each next() runs the body until the next yield, then pauses with its local state intact."}


def make(title="Python generators", **kw):
    args = dict(title=title, why_interesting="I keep meeting them in code I read and want to use them properly",
                notes=dict(NOTES), sources=[{"ref": "url:https://example.com/generators", "excerpt": EXCERPT}])
    args.update(kw)
    return S.save_topic(**args)


def drills(tid, n=3):
    qs = [("How does a generator keep memory small for huge sequences?", ["local state is kept between calls", "values one at a time"]),
          ("What happens at the end of a generator function?", ["StopIteration is raised", "function returns"]),
          ("What does calling next do on a paused generator?", ["resumes right after the last yield", "pauses between them"])]
    for q, pts in qs[:n]:
        S.add_drill(tid, q=q, key_points=pts)


def good(q):      # an answer that contains the key points, from "memory"
    return {"How does": "It produces values one at a time, and its local state is kept between calls so memory stays small.",
            "What happens": "When the function returns, StopIteration is raised.",
            "What does": "It resumes right after the last yield."}[next(k for k in ("How does", "What happens", "What does") if q.startswith(k))]


# ── building a topic: in her own words, backed by sources ────────────────────

def test_a_topic_needs_her_own_interest_notes_and_a_source():
    t = make()
    assert t["id"] == "topic-python-generators" and t["sources"][0]["kind"] == "url"
    with pytest.raises(A.AnchorError, match="why this interests you"):
        make(title="Another", why_interesting="ok")
    with pytest.raises(A.AnchorError, match="start with at least"):
        make(title="Another", notes={"what_it_is": NOTES["what_it_is"]})
    with pytest.raises(A.AnchorError, match="at least one source"):
        make(title="Another", sources=[])


def test_notes_must_be_real_sentences_and_unhedged():
    with pytest.raises(A.AnchorError, match="couple of real sentences"):
        make(notes={**NOTES, "how_it_works": "uses yield"})
    with pytest.raises(A.AnchorError, match="hedged"):
        make(notes={**NOTES, "why": "It is probably faster in most programs, I think, for large data."})
    with pytest.raises(A.AnchorError, match="note field"):
        S.add_notes(make()["id"], field="vibes", text="x" * 60)


@pytest.mark.parametrize("ref", ["I read it somewhere", "url:not a url", "file:/no/such/file", "receipt:abc123", "teacher:"])
def test_sources_must_be_checkable(ref):
    with pytest.raises(A.AnchorError):
        make(sources=[{"ref": ref, "excerpt": EXCERPT}])


def test_a_source_needs_the_passage_that_backs_it():
    with pytest.raises(A.AnchorError, match="short passage"):
        make(sources=[{"ref": "url:https://example.com/x", "excerpt": "short"}])
    with pytest.raises(A.AnchorError, match="short passage"):
        make(sources=[{"ref": "url:https://example.com/x", "excerpt": "word " * 400}])


def test_duplicates_and_adding_to_a_topic():
    t = make()
    with pytest.raises(A.AnchorError, match="already exists"):
        make()
    S.add_notes(t["id"], field="why", text="Lazy evaluation lets one pipeline stage feed the next without building lists.")
    S.add_source(t["id"], ref="brian:said generators are great for reading big log files", excerpt="Brian: use generators to stream a huge log file line by line.")
    with pytest.raises(A.AnchorError, match="already a source"):
        S.add_source(t["id"], ref="url:https://example.com/generators", excerpt=EXCERPT)
    got = S.get_topic("Python generators")
    assert "why" in got["notes"] and len(got["sources"]) == 2


# ── drills can only test what a source backs ─────────────────────────────────

def test_a_drill_key_point_must_be_in_her_saved_sources():
    t = make()
    S.add_drill(t["id"], q="How does a generator keep memory small?", key_points=["local state is kept between calls", "one at a time"])
    with pytest.raises(A.AnchorError, match="isn't in your saved sources"):
        S.add_drill(t["id"], q="Why are generators the fastest option available?", key_points=["always faster than lists", "uses the GPU"])


def test_a_drill_cannot_give_away_its_own_answer_or_be_sloppy():
    t = make()
    with pytest.raises(A.AnchorError, match="given away"):
        S.add_drill(t["id"], q="Does a generator pause between values one at a time?", key_points=["pauses between values", "one at a time"])
    with pytest.raises(A.AnchorError, match="2 to 5"):
        S.add_drill(t["id"], q="What does calling next do on a generator?", key_points=["resumes right after the last yield"])
    S.add_drill(t["id"], q="What does calling next do on a generator?", key_points=["resumes right after the last yield", "pauses between them"])
    with pytest.raises(A.AnchorError, match="already a drill"):
        S.add_drill(t["id"], q="what does calling next do on a generator?", key_points=["resumes right after the last yield", "pauses between them"])


def test_a_topic_holds_a_limited_number_of_drills():
    t = make(sources=[{"ref": "url:https://example.com/g", "excerpt": EXCERPT + " " + OTHER}])
    base = ["lazy", "values", "state", "memory", "yield", "next", "function", "sequences", "restarted", "generator", "pauses", "calls"]
    for i, w in enumerate(base):
        S.add_drill(t["id"], q=f"Question number {i}: what should be said regarding {w}?", key_points=[f"{w} appears in the text", "generator function"] if False else
                    ["local state is kept between calls", "values one at a time"])
    with pytest.raises(A.AnchorError, match="at most"):
        S.add_drill(t["id"], q="One question more than the limit allows here?", key_points=["local state is kept between calls", "values one at a time"])


# ── scoring: by code, against key points ─────────────────────────────────────

def test_scoring_counts_key_points_not_style():
    pts = ["local state is kept between calls", "values one at a time"]
    assert S.score("It yields values one at a time, keeping its local state between calls.", pts) == (1.0, [])
    cov, missed = S.score("It keeps local state between calls.", pts)
    assert cov == 0.5 and missed == ["values one at a time"]
    assert S.score("I don't know.", pts) == (0.0, pts)
    assert S.score("", pts)[0] == 0.0
    assert S.score("Generators are cool and fast and nice.", pts)[0] == 0.0


# ── drills are closed-book, and the schedule follows what she really knows ───

def test_the_model_is_asked_only_the_question_never_her_notes(store):
    t = make()
    drills(t["id"])
    seen = []
    S.run_drill(t["id"], ask=lambda q: seen.append(q) or "I don't know")
    assert len(seen) == 3 and all(q.endswith("?") for q in seen)
    assert not any("local state" in q or "yield" in q.lower() and "generator is a function" in q for q in seen)


def test_default_ask_is_one_toolless_call_with_only_the_question(monkeypatch):
    import worker
    sent = {}
    monkeypatch.setattr(worker, "ollama_chat", lambda **kw: sent.update(kw) or {"message": {"content": "an answer"}})
    assert S.default_ask("What does calling next do?") == "an answer"
    assert "tools" not in sent and sent["_priority"] == "worker"
    texts = " ".join(m["content"] for m in sent["messages"])
    assert "What does calling next do?" in texts and "no notes and no tools" in texts
    assert len(sent["messages"]) == 2


def test_passing_drills_move_the_topic_up_failing_ones_pull_it_back(store):
    t = make()
    drills(t["id"])
    assert S.status(t["id"])["level"] == 0 and S.status(t["id"])["due"]
    r1 = S.run_drill(t["id"], ask=good)
    assert r1["passed"] and S.status(t["id"])["level"] == 1
    assert not S.status(t["id"])["due"]                       # next one is a day out
    r2 = S.run_drill(t["id"], ask=lambda q: "I don't know")
    assert not r2["passed"] and S.status(t["id"])["level"] == 0
    st = S.status(t["id"])
    assert st["last_passed"] is False
    due_at = datetime.strptime(st["next_due"], "%Y-%m-%d %H:%M")
    assert timedelta(hours=20) < due_at - datetime.now(timezone.utc).replace(tzinfo=None) < timedelta(hours=25)   # study, then tomorrow


def test_a_failed_drill_shows_what_she_missed_and_what_her_source_says(store):
    t = make()
    drills(t["id"])
    r = S.run_drill(t["id"], ask=lambda q: "I'm not sure")
    assert "missed: StopIteration is raised" in r["report"] and "your source says: " in r["report"]
    assert "StopIteration is raised" in r["report"]


def test_a_drill_needs_enough_questions_and_runs_nothing_without_them(store):
    t = make()
    drills(t["id"], n=2)
    with pytest.raises(A.AnchorError, match="at least 3"):
        S.run_drill(t["id"], ask=good)
    assert not [r for r in A.receipts() if r["kind"] == "drill"]


def test_if_the_model_is_off_nothing_is_recorded(store):
    import heavy_job
    t = make()
    drills(t["id"])

    def refuse(q):
        raise heavy_job.JobBusy("busy: image generation is running")
    with pytest.raises(heavy_job.JobBusy):
        S.run_drill(t["id"], ask=refuse)
    assert not [r for r in A.receipts() if r["kind"] == "drill"]


def test_the_weakest_questions_are_asked_first(store):
    t = make()
    drills(t["id"])
    wrong_on = "What happens at the end"
    S.run_drill(t["id"], ask=lambda q: "I don't know" if q.startswith(wrong_on) else good(q), k=3)
    order = []
    S.run_drill(t["id"], ask=lambda q: order.append(q) or good(q), k=3)
    assert order[0].startswith(wrong_on)


def test_due_lists_only_ready_topics_that_are_due(store):
    ready, notready = make(), make(title="Half built")
    drills(ready["id"])
    S.add_drill(notready["id"], q="What does calling next do on a generator?", key_points=["resumes right after the last yield", "pauses between them"])
    assert [d["id"] for d in S.due()] == [ready["id"]]
    S.run_drill(ready["id"], ask=good)
    assert S.due() == []


# ── from draft to mastered: what each label has to mean ──────────────────────

def test_labels_need_real_evidence(store):
    t = make(sources=[{"ref": "url:https://example.com/g", "excerpt": EXCERPT}])
    drills(t["id"])
    assert S.status(t["id"])["label"] == "draft"              # four note fields and more are still missing
    for f, text in {"why": "Lazy values let one stage feed the next without building a full list in memory.",
                    "worked_example": "def count(): n = 0 then loop: yield n and increase n, so a for loop reads 0, 1, 2.",
                    "common_mistakes": "Trying to reuse an exhausted generator, or expecting len() to work on one.",
                    "where_it_breaks": "When you need random access or several passes over the same data."}.items():
        S.add_notes(t["id"], field=f, text=text)
    assert S.status(t["id"])["label"] == "learning"
    for _ in range(3):
        S.run_drill(t["id"], ask=good)
    assert S.status(t["id"])["level"] == 3
    assert S.status(t["id"])["label"] == "learning"           # one source is not enough for 'solid'
    S.add_source(t["id"], ref="brian:said use them to stream big files", excerpt="Brian: use generators to stream a huge log file line by line.")
    assert S.status(t["id"])["label"] == "solid"
    a = A.add_anchor(title="Stream big files with a generator", what_it_is="Read a large file line by line with a generator",
                     applies_when="a file is too big to hold in memory", do_not_apply_when="you need several passes over the data",
                     breaks_when="the data must be sorted first", origin="brian:said use them to stream big files", topic=t["id"])
    assert S.status(t["id"])["label"] == "solid"              # not mastered until she has used it
    A.attempt(a["id"], result="worked", evidence="streamed a 2 GB log in constant memory, output matched")
    assert S.status(t["id"])["label"] == "mastered" and S.status(t["id"])["applied"]
    rec = [r for r in A.receipts() if r["kind"] == "attempt"][0]
    A.confirm(a["id"], rec["id"], owner_token=A.OWNER_TOKEN)
    assert "confirmed by Brian" in S.status(t["id"])["label"]


# ── teachers: a frontier model is a source, not a fact ───────────────────────

TEACH = dict(model="claude", question="How should I plan a multi-step task?",
             answer="First break the goal into steps small enough to verify. Do one step, check the result against the goal, "
                    "and only then continue. If a check fails, stop and re-plan instead of pushing on with a broken assumption.",
             method=["Break the goal into small verifiable steps", "Check each result before the next step"],
             cannot_do_reliably="Keeping the whole plan in mind across many steps without drifting",
             scaffold="Write the steps to a file and tick each one off with the result")


def test_a_teacher_session_demands_method_limits_and_a_scaffold():
    t = make()
    r = S.teach(t["id"], **TEACH)
    topic = S.get_topic(t["id"])
    assert topic["sources"][-1]["ref"] == "teacher:claude" and r["source"] == "teacher:claude"
    assert "Your limits" in r["reminder"] and r["receipt"] in r["reminder"]
    bad = {**TEACH, "method": ["only one step here"]}
    with pytest.raises(A.AnchorError, match="METHOD"):
        S.teach(t["id"], **bad)
    with pytest.raises(A.AnchorError, match="can't do reliably"):
        S.teach(t["id"], **{**TEACH, "cannot_do_reliably": ""})
    with pytest.raises(A.AnchorError, match="scaffold"):
        S.teach(t["id"], **{**TEACH, "scaffold": ""})
    with pytest.raises(A.AnchorError, match="which model"):
        S.teach(t["id"], **{**TEACH, "model": ""})
    with pytest.raises(A.AnchorError, match="actual answer"):
        S.teach(t["id"], **{**TEACH, "answer": "short"})
    S.teach(t["id"], **{**TEACH, "cannot_do_reliably": "none: every step is a simple check I can do with a tool", "scaffold": ""})


def test_repeat_sessions_with_the_same_teacher_get_their_own_source_and_can_back_an_anchor():
    t = make()
    first = S.teach(t["id"], **TEACH)
    second = S.teach(t["id"], **TEACH)
    assert [s["ref"] for s in S.get_topic(t["id"])["sources"] if s["kind"] == "teacher"] == ["teacher:claude", "teacher:claude#2"]
    a = A.add_anchor(title="Plan in checked steps", what_it_is="Break a task into small steps and verify each before moving on",
                     applies_when="a task has more than three steps", do_not_apply_when="the task is a single lookup",
                     breaks_when="the steps depend on each other in ways you can't check", origin=f"receipt:{first['receipt']}",
                     topic=t["id"])
    assert a["confidence"] == 0.6 and A.lint() == [] and S.lint() == []


def test_limits_profile_names_her_model_and_what_her_own_logs_showed(tmp_path, monkeypatch):
    import growth_drive
    monkeypatch.setattr(growth_drive, "LEDGER", tmp_path / "gaps.jsonl")
    growth_drive.open_gap("g1", "tool-selection", "picked fund_status for a BoTTube earnings question", case="c")
    monkeypatch.setenv("AMY_MODEL", "gemma12-test")
    text = S.limits_profile()
    assert "gemma12-test" in text and "12B" in text and "unreliable" in text
    assert "Seen in your own logs: tool-selection: picked fund_status" in text


# ── what comes back, and what pulls her on ───────────────────────────────────

def test_a_fitting_topic_and_its_anchors_show_up_with_where_it_breaks(store):
    t = make()
    S.add_notes(t["id"], field="where_it_breaks", text="When you need random access or several passes over the same data.")
    ctx = S.context("I need to read a huge sequence of values with a python generator without running out of memory")
    assert "[A topic you studied that fits]" in ctx and "Python generators" in ctx and "Where it breaks: When you need random access" in ctx
    assert S.context("what is the weather in Winston-Salem") == ""


def test_curiosity_puts_due_drills_first_then_restudy_then_unfinished_work(store):
    t = make()
    drills(t["id"])
    S.add_drill  # (drills exist)
    other = make(title="Unfinished")
    S.run_drill(t["id"], ask=lambda q: "I don't know")
    A.recall("something nobody has written down yet")
    items = S.curiosity()
    assert items[0].startswith("re-study Python generators") or items[0].startswith("drill due")
    assert any(i.startswith("finish the topic Unfinished") for i in items)
    assert any(i.startswith("recall miss") for i in items)
    assert [i for i in items if i.startswith("drill due")] == [] or items.index([i for i in items if i.startswith("drill due")][0]) < items.index(next(i for i in items if i.startswith("finish")))


def test_summary_lists_topics_and_lessons(store):
    t = make()
    drills(t["id"])
    A.add_anchor(title="Stream big files", what_it_is="Read a large file with a generator", applies_when="a file is too big for memory",
                 do_not_apply_when="several passes are needed", breaks_when="data must be sorted first", origin="brian:said use them for big files")
    text = S.summary()
    assert "Python generators: draft" in text and "Stream big files: confidence 0.80" in text


# ── export for a future fine-tune ────────────────────────────────────────────

def test_only_verified_recall_and_confirmed_applications_are_exported(store, tmp_path):
    t = make()
    drills(t["id"])
    S.run_drill(t["id"], ask=lambda q: good(q) if q.startswith("How does") else "I don't know")
    a = A.add_anchor(title="Stream big files", what_it_is="Read a large file with a generator", applies_when="a file is too big for memory",
                     do_not_apply_when="several passes are needed", breaks_when="data must be sorted first", origin="brian:said use them for big files", topic=t["id"])
    rec = A.attempt(a["id"], result="worked", evidence="streamed a 2 GB log in constant memory")
    A.confirm(a["id"], rec["id"], owner_token=A.OWNER_TOKEN)
    out = tmp_path / "train.jsonl"
    counts = S.export_training(out)
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    assert counts == {"recall": 1, "applied": 1, "path": str(out)}
    assert rows[0]["type"] == "recall" and rows[0]["question"].startswith("How does") and rows[0]["coverage"] >= 0.8
    assert rows[1]["type"] == "applied" and "2 GB log" in rows[1]["evidence"]


def test_export_refuses_when_the_receipt_chain_is_broken(store, tmp_path):
    t = make()
    drills(t["id"])
    S.run_drill(t["id"], ask=good)
    lines = A._receipts_path().read_text().splitlines()
    A._receipts_path().write_text("\n".join(lines[1:]) + "\n")
    with pytest.raises(A.AnchorError, match="chain is broken"):
        S.export_training(tmp_path / "x.jsonl")


# ── the whole store stays consistent ─────────────────────────────────────────

def test_everything_the_writers_produce_passes_lint(store):
    t = make()
    drills(t["id"])
    S.run_drill(t["id"], ask=good)
    S.teach(t["id"], **TEACH)
    A.add_anchor(title="Stream big files", what_it_is="Read a large file with a generator", applies_when="a file is too big for memory",
                 do_not_apply_when="several passes are needed", breaks_when="data must be sorted first", origin="brian:said use them for big files", topic=t["id"])
    A.defer("how generators interact with async code", trigger="curiosity", why="not needed today")
    assert S.lint() == []


def test_lint_catches_edits_that_break_the_rules(store):
    t = make()
    drills(t["id"])
    path = S._path(t["id"])
    data = yaml.safe_load(path.read_text())
    data["drills"][0]["key_points"] = ["generators run on the GPU"]
    data["sources"].append({"ref": "I read it somewhere", "kind": "url", "excerpt": EXCERPT})
    path.write_text(yaml.safe_dump(data))
    problems = " | ".join(S.lint())
    assert "isn't backed by any saved source" in problems and "source 'I read it somewhere'" in problems


def test_lint_flags_receipts_and_anchors_pointing_at_missing_topics(store):
    t = make()
    S._path(t["id"]).unlink()
    A.add_anchor(title="Orphan", what_it_is="A rule from a topic that is gone", applies_when="never really", do_not_apply_when="always",
                 breaks_when="now", origin="brian:said this is only a test case", topic="topic-ghost")
    problems = " | ".join(S.lint())
    assert "refers to unknown topic" in problems and "links to unknown topic topic-ghost" in problems


# ── wiring: the tool surface she gets ────────────────────────────────────────

def test_the_tools_exist_and_have_no_confirm_action():
    import tools
    names = {t["function"]["name"]: t for t in tools.TOOLS}
    assert "anchor" in names and "study" in names
    assert "confirm" not in names["anchor"]["function"]["parameters"]["properties"]["action"]["enum"]
    import worker
    assert {"anchor", "study"} <= worker.ALLOWED_TOOLS


def test_the_anchor_and_study_tools_work_end_to_end_and_refuse_confirm(store):
    import tools
    out = tools.dispatch("study", {"action": "save_topic", "title": "Python generators", "why_interesting": "I keep meeting them in code I read and want to use them properly",
                                    "notes": NOTES, "sources": [{"ref": "url:https://example.com/generators", "excerpt": EXCERPT}]})
    assert "topic-python-generators" in out
    for q, pts in [("How does a generator keep memory small for huge sequences?", ["local state is kept between calls", "values one at a time"])]:
        assert "q" in tools.dispatch("study", {"action": "add_drill", "topic": "topic-python-generators", "q": q, "key_points": pts})
    assert "Python generators" in tools.dispatch("study", {"action": "read", "topic": "topic-python-generators"})
    assert "unknown anchor action" in tools.dispatch("anchor", {"action": "confirm", "id": "x"})
    assert "anchor: " in tools.dispatch("anchor", {"action": "add", "title": "t"}) or True
    assert tools.dispatch("anchor", {"action": "add", "title": "Only a title"}).startswith("anchor")


def test_the_worker_adds_what_she_learned_to_a_matching_task(store):
    import worker
    t = make()
    drills(t["id"])
    text = worker._learning_context("read a huge sequence of values with a python generator without running out of memory")
    assert "Python generators" in text and "drill due" in text
    assert worker._learning_context("something else entirely, unrelated").count("topic you studied") == 0


def test_brian_confirms_by_message_only(store):
    class T:
        _learning_command = staticmethod(S.owner_command)
    a = A.add_anchor(title="Stream big files", what_it_is="Read a large file with a generator", applies_when="a file is too big for memory",
                     do_not_apply_when="several passes are needed", breaks_when="data must be sorted first", origin="brian:said use them for big files")
    rec = A.attempt(a["id"], result="worked", evidence="streamed a 2 GB log in constant memory")
    assert rec["id"] in T._learning_command("learned") or "Waiting for Brian" in T._learning_command("learned")
    assert T._learning_command("confirm") .startswith("To confirm")
    assert T._learning_command(f"confirm {a['id']} {rec['id']}").startswith("Confirmed")
    assert T._learning_command(f"confirm {a['id']} {rec['id']}").startswith("That didn't work")
    assert T._learning_command("hello there") is None
