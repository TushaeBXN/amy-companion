"""Keeping a chat turn honest and unstuck (the 2026-10-24 failures: tool loops, invented progress, 'want me to keep going?')."""
import sqlite3

import pytest

import bookshelf as B
import turn_guard as G


def call(name, result, **args):
    return {"name": name, "args": args, "result_snippet": result}


FAILED_OPEN = call("books", "books: no book on your shelf matches 'x.pdf'", action="open", title="x.pdf")
GOOD_OPEN = call("books", "[Becoming the Hacker: passages 1-3 of 603.]", action="open", title="hacker")
LOOP = [call("wake_up", "[L0:identity] You are Amy"), call("wake_up", "[Already called wake_up with these exact args")]


def test_failures_are_recognised_from_the_tool_text():
    for r in ["books: no book", "study: give 2 to 5 key points", "[tool_error] x", "[Already called x", "Not done: ...",
              "file not found: /x", "browse: refused", "consult: no key"]:
        assert G.failed(r), r
    for r in ["[Becoming the Hacker: passages 1-3", "Goal created: g1", "42", ""]:
        assert not G.failed(r) or r == "", r


def test_only_working_non_context_calls_count_as_work():
    assert G.real_work([FAILED_OPEN] + LOOP) == []
    assert G.real_work([FAILED_OPEN, GOOD_OPEN]) == [GOOD_OPEN]
    assert G.real_work([call("run_code", "3\n", code="print(3)")])


def test_the_summary_is_built_from_what_the_tools_returned():
    s = G.work_summary([FAILED_OPEN, GOOD_OPEN])
    assert "FAILED: books: no book" in s and "- books(action=open, title=hacker) -> [Becoming" in s
    assert G.work_summary([]) == "You called no tools."


def test_the_wrapup_demands_honesty_and_does_not_invite_a_yes():
    p = G.wrapup_prompt([FAILED_OPEN] + LOOP)
    assert "You have not done the task yet, so say so plainly" in p and "FAILED" in p
    assert "Do not describe anything you did not do" in p and "Do not ask whether to continue" in p
    assert "You have not done the task yet" not in G.wrapup_prompt([GOOD_OPEN])


def test_claiming_work_no_tool_did_gets_a_note_but_true_claims_do_not():
    claim = "I've opened the Hugging Face report and mapped out the failures."
    note = G.unbacked_claim_note(claim, [FAILED_OPEN] + LOOP)
    assert "no tool I called this turn actually did that" in note and "FAILED: books:" in note
    assert G.unbacked_claim_note(claim, [GOOD_OPEN]) == ""
    assert G.unbacked_claim_note("Sure, tell me more about it.", []) == ""
    assert G.unbacked_claim_note("I scanned the shelf and found it.", []) != ""


@pytest.mark.parametrize("text,expect", [("yes", True), ("keep going", True), ("Yes and dont stop", True), ("OK", True),
                                         ("go ahead!", True), ("figure out how you would do it on this machine", False)])
def test_continue_words(text, expect):
    assert G.is_continue_word(text) is expect


@pytest.mark.parametrize("reply,expect", [("The data alignment phase is next, want me to keep going?", True),
                                          ("Should I move on to the data alignment now?", True),
                                          ("Would you like me to continue with step two?", True),
                                          ("Done. The file is at work/x.md.", False)])
def test_asking_to_continue_is_detected(reply, expect):
    assert G.asks_to_continue(reply) is expect


def test_the_job_is_his_last_real_request_not_the_word_yes():
    hist = [{"role": "user", "content": "Read the hugging-face-incident-report PDF and make a step by step study guide, then do what they did"},
            {"role": "assistant", "content": "I started... want me to keep going?"},
            {"role": "user", "content": "yes"}]
    assert G.objective_for("keep going", hist).startswith("Read the hugging-face-incident-report")
    assert G.objective_for("Please set up a proper workbench and then build a small tool that checks my PDFs for text", []).startswith("Please set up")
    assert G.objective_for("yes", [{"role": "user", "content": "hi"}]) == ""


class FakeGoals:
    def __init__(self):
        self.rows = []

    def list_goals(self, status=None):
        return [g for g in self.rows if g["status"] == status]

    def create_goal(self, objective, reason="", plan=None, goal_id=""):
        g = {"goal_id": f"g{len(self.rows) + 1}", "objective": objective, "status": "pending", "plan": plan or [], "current_step": 0}
        self.rows.append(g)
        return g

    def get_active_goal(self):
        return self.rows[0] if self.rows else None


def test_unfinished_work_becomes_one_goal_and_is_not_duplicated():
    fg = FakeGoals()
    obj = "Read the hugging-face-incident-report PDF and make a step by step study guide"
    g1, new1 = G.queue_goal(obj, "chat", fg)
    g2, new2 = G.queue_goal(obj + "!", "chat", fg)
    assert new1 and not new2 and g1["goal_id"] == g2["goal_id"] and len(fg.rows) == 1
    assert G.queue_goal("", "chat", fg) == (None, False)


def test_goal_context_gives_yes_something_to_point_at():
    fg = FakeGoals()
    assert G.goal_context(fg) == ""
    g = fg.create_goal("Make a study guide from the incident report")
    g["plan"], g["current_step"] = ["Open the book", "Write the steps", "Try step one"], 1
    ctx = G.goal_context(fg)
    assert "step 2 of 3: Write the steps" in ctx and "don't start over" in ctx


# ── the book lookup that failed on a file name ───────────────────────────────

@pytest.fixture
def shelf(tmp_path, monkeypatch):
    db = tmp_path / "m.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE mem (id INTEGER PRIMARY KEY, role TEXT, text TEXT, emb BLOB, ts TEXT, source TEXT)")
    for src in ["hugging-face-incident-report-aug-2026", "Becoming the Hacker", "Bug Bounty Bootcamp", "Bug Bounty Playbook"]:
        con.execute("INSERT INTO mem (role,text,source) VALUES ('knowledge',?,?)", (f"[From: {src}]\nbody text", src))
    con.commit()
    con.close()
    monkeypatch.setattr(B, "DB", db)


def test_a_file_name_finds_the_book(shelf):
    assert B.resolve("hugging-face-incident-report-aug-2026.pdf")["title"] == "hugging-face-incident-report-aug-2026"
    assert B.resolve("Hugging Face incident report")["title"] == "hugging-face-incident-report-aug-2026"
    assert B.resolve("becoming the hacker.PDF")["title"] == "Becoming the Hacker"


def test_a_miss_names_the_closest_books_and_ambiguity_lists_them(shelf):
    with pytest.raises(B.BookError, match="Closest: .*Bug Bounty"):
        B.resolve("bug bounty handbook")
    with pytest.raises(B.BookError, match="more than one book"):
        B.resolve("bug bounty")
    with pytest.raises(B.BookError, match="no book"):
        B.resolve("cooking")


# ── the chat loop itself, with a scripted model that does what she did ───────

def test_not_a_tool_answer_names_real_tools():
    import tools
    msg = G.not_a_tool("wake_up", G.tool_names(tools))
    assert "wake_up is not a tool, so nothing was run" in msg and "books" in msg and "run_code" in msg
    assert "wake_up" not in [t["function"]["name"] for t in tools.TOOLS]
    enum = next(t for t in tools.TOOLS if t["function"]["name"] == "engram_memory")["function"]["parameters"]["properties"]["action"]["enum"]
    assert "wake_up" not in enum and "diary_read" in enum


def test_the_wrapup_names_the_goal_when_one_was_queued():
    p = G.wrapup_prompt([GOOD_OPEN], "study_guide_ab12")
    assert "handed to your background worker as goal study_guide_ab12" in p and "Do not ask whether to continue" in p
    assert "handed to your background worker" not in G.wrapup_prompt([GOOD_OPEN])


def _scripted(script, seen):
    def fake(**kw):
        seen.append(kw)
        msg = script.pop(0) if script else {"role": "assistant", "content": "ok"}
        return {"message": msg}
    return fake


def _tool_call(name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def test_a_spiral_of_wake_up_calls_ends_early_and_the_job_goes_to_the_worker(tmp_path, monkeypatch):
    try:
        import telegram_amy as T
    except FileNotFoundError:
        pytest.skip("needs amy_self.json (present in the live folder)")
    import tools, worker
    seen, ran = [], []
    script = [_tool_call("books", action="search", query="Hugging Face PDF")] + [_tool_call("wake_up")] * 9 \
        + [{"role": "assistant", "content": "I searched my shelf and found the report. The rest is queued for my worker."}]
    monkeypatch.setattr(worker, "ollama_chat", _scripted(script, seen))
    monkeypatch.setattr(tools, "dispatch", lambda n, a, m=None: ran.append(n) or "[Matches from your books. passage 1]")
    monkeypatch.setattr(T, "_history_for", lambda k: [])
    monkeypatch.setattr(T, "_save_history", lambda *a, **k: None)
    queued = []
    monkeypatch.setattr(G, "queue_goal", lambda obj, why, ag=None: (queued.append(obj) or {"goal_id": "g_test"}, True))
    monkeypatch.setattr(T.mem, "add", lambda *a, **k: None)
    monkeypatch.setattr(T._turn_log, "log_turn", lambda **k: None)
    reply = T._chat("Read the hugging face incident report PDF and reproduce what they did on this machine, then tell me what worked", None)
    assert ran == ["books"]                                   # wake_up never ran as a tool
    assert len(seen) <= 6                                    # 1 real call + 3 wasted + wrap-up, not 10 hops
    assert queued and "hugging face incident report" in queued[0].lower()
    last = seen[-1]["messages"][-1]["content"]
    assert "goal g_test" in last and "not a tool" not in last.split("What your tools")[0]
    assert reply


TASK = "Read the hugging face incident report PDF and reproduce what they did on this machine, then tell me what worked"


@pytest.mark.parametrize("reply,user,expect", [
    ("I've searched my shelf. My first instinct is to actually open that book and read through it.", TASK, True),
    ("Let me open the report and start on step one.", TASK, True),
    ("I'm ready to dive into the specific", "keep going", True),
    ("Which book do you mean? I'll open it as soon as I know.", TASK, False),          # a question back is not a stall
    ("Done. The notes are in work/guide.md.", TASK, False),
    ("I'll keep that in mind.", "thanks, that helps", False),                          # chit-chat is left alone
    ("", TASK, False),
])
def test_announcing_instead_of_acting_is_detected_only_on_real_tasks(reply, user, expect):
    assert G.announced_not_done(reply, user) is expect


def test_the_nudge_restates_the_request_and_names_real_tools():
    n = G.nudge_text("keep going", [{"role": "user", "content": TASK}])
    assert "reproduce what they did" in n and "books action=open" in n and "real tool call, not text" in n


def test_the_chat_loop_pushes_her_from_announcing_to_doing(monkeypatch):
    try:
        import telegram_amy as T
    except FileNotFoundError:
        pytest.skip("needs amy_self.json (present in the live folder)")
    import tools, worker
    seen, ran = [], []
    script = [{"role": "assistant", "content": "I found it. My first instinct is to open that book and read through it."},
              _tool_call("books", action="open", title="hugging-face-incident-report-aug-2026"),
              {"role": "assistant", "content": "I opened it: the report lists the failure sequence. Step one is done."}]
    monkeypatch.setattr(worker, "ollama_chat", _scripted(script, seen))
    monkeypatch.setattr(tools, "dispatch", lambda n, a, m=None: ran.append(n) or "[hugging-face-incident-report: passages 1-3 of 12]")
    monkeypatch.setattr(T, "_history_for", lambda k: [])
    monkeypatch.setattr(T, "_save_history", lambda *a, **k: None)
    monkeypatch.setattr(T.mem, "add", lambda *a, **k: None)
    monkeypatch.setattr(T._turn_log, "log_turn", lambda **k: None)
    reply = T._chat(TASK, None)
    assert ran == ["books"] and "Step one is done" in reply
    assert any("didn't do it" in m.get("content", "") for m in seen[1]["messages"] if m["role"] == "user")
