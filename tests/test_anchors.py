"""Anchors: the loop that makes what Amy learns usable. Every rule here exists because a first draft of
this idea broke it when tested (spammed confidence, unchecked confirm, silent staleness, tamperable logs)."""
import json
import time
from datetime import date, timedelta

import pytest
import yaml

import anchors as A


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "DIR", tmp_path / "anchors")
    monkeypatch.setattr(A, "_last_logged", {})
    (tmp_path / "evidence.log").write_text("peak memory 21.8 GB")
    return tmp_path


def make(store, title="FLUX needs the machine alone", **kw):
    args = dict(title=title, what_it_is="FLUX image generation peaks at 21.8 GB on a 16 GB Mac",
                applies_when="running mflux or any model over 12 GB", do_not_apply_when="the chat model is unloaded",
                breaks_when="the Mac has more memory", origin=f"file:{store / 'evidence.log'}", tags=["flux", "mflux", "memory"])
    args.update(kw)
    return A.add_anchor(**args)


# ── saving: limits and provenance are required ───────────────────────────────

def test_a_valid_anchor_is_saved_with_a_starting_confidence_from_its_source(store):
    a = make(store)
    assert a["id"] == "anchor-flux-needs-the-machine-alone" and a["confidence"] == 0.6   # file origin
    assert yaml.safe_load((A.DIR / f"{a['id']}.yaml").read_text())["breaks_when"]


@pytest.mark.parametrize("field", ["what_it_is", "applies_when", "do_not_apply_when", "breaks_when"])
def test_a_lesson_without_its_limits_is_refused(store, field):
    with pytest.raises(A.AnchorError, match=field):
        make(store, **{field: "  "})


@pytest.mark.parametrize("origin", ["I had a feeling, observed in a dream", "observed: it just works", "test", "",
                                    "url:not a url", "file:/no/such/file", "receipt:doesnotexist", "brian:ok"])
def test_free_text_and_unverifiable_origins_are_refused(store, origin):
    with pytest.raises(A.AnchorError):
        make(store, origin=origin)


def test_real_origins_are_accepted_with_different_starting_confidence(store):
    assert make(store, title="From the web", origin="url:https://example.com/page")["confidence"] == 0.4
    assert make(store, title="From Brian", origin="brian:said the Mac mini has 16 GB")["confidence"] == 0.8
    first = A.receipts()[0]["id"]
    assert make(store, title="From a receipt", origin=f"receipt:{first}")["confidence"] == 0.6


def test_hedged_wording_needs_an_inferred_tag(store):
    with pytest.raises(A.AnchorError, match="hedged"):
        make(store, what_it_is="It probably peaks near 22 GB")
    assert make(store, title="Tagged", what_it_is="It probably peaks near 22 GB [inferred]")


def test_same_trigger_and_exclusion_is_useless_and_duplicates_are_refused(store):
    with pytest.raises(A.AnchorError, match="useless"):
        make(store, applies_when="always", do_not_apply_when="Always")
    make(store)
    with pytest.raises(A.AnchorError, match="already exists"):
        make(store)


def test_new_knowledge_supersedes_old_and_the_old_one_stops_being_recalled(store):
    old = make(store)
    new = make(store, title="FLUX at 4-bit", what_it_is="FLUX at 4-bit peaks at 16.8 GB", supersedes=old["id"])
    ids = [h["id"] for h in A.match("running mflux flux memory", k=5)]
    assert new["id"] in ids and old["id"] not in ids
    with pytest.raises(A.AnchorError, match="supersedes"):
        make(store, title="Bad ref", supersedes="anchor-ghost")


# ── confidence: computed from receipts, hard to inflate ──────────────────────

def test_ten_self_reported_wins_cannot_push_an_anchor_past_the_cap(store):
    a = make(store, title="From the web", origin="url:https://example.com/page")     # starts at 0.4
    for i in range(10):
        A.attempt(a["id"], result="worked", evidence=f"I saw it work on run number {i}")
    assert A.effective_confidence(A.get(a["id"])) == 0.6        # the self-report ceiling, not 0.9


def test_the_same_evidence_counts_once(store):
    a = make(store, title="From the web", origin="url:https://example.com/page")
    for _ in range(5):
        A.attempt(a["id"], result="worked", evidence="the build printed OK")
    assert A.effective_confidence(A.get(a["id"])) == 0.45


def test_a_worked_attempt_needs_evidence(store):
    a = make(store)
    with pytest.raises(A.AnchorError, match="evidence"):
        A.attempt(a["id"], result="worked", evidence="trust me")


def test_failures_weigh_more_than_wins(store):
    a = make(store, title="From the web", origin="url:https://example.com/page")
    A.attempt(a["id"], result="worked", evidence="the build printed OK today")
    A.attempt(a["id"], result="failed", evidence="")
    assert A.effective_confidence(A.get(a["id"])) == 0.35       # 0.40 + 0.05 - 0.10


def test_lint_catches_a_base_confidence_above_what_its_source_allows(store):
    a = make(store, title="From the web", origin="url:https://example.com/page")
    path = A.DIR / f"{a['id']}.yaml"
    data = yaml.safe_load(path.read_text())
    data["confidence"] = 0.99
    path.write_text(yaml.safe_dump(data))
    assert any("above what its source allows" in p for p in A.lint())


def test_only_brians_confirmation_gives_full_weight(store):
    a = make(store, title="From the web", origin="url:https://example.com/page")
    rec = A.attempt(a["id"], result="worked", evidence="I saw the page load without errors")
    assert A.effective_confidence(A.get(a["id"])) == 0.45
    A.confirm(a["id"], rec["id"], owner_token=A.OWNER_TOKEN)
    assert A.effective_confidence(A.get(a["id"])) == 0.55       # 0.40 + 0.15 confirmed (cap lifted)


def test_confirm_refuses_without_the_token_and_for_bad_attempts(store):
    a = make(store)
    ok = A.attempt(a["id"], result="worked", evidence="the run printed a clean summary")
    bad = A.attempt(a["id"], result="failed")
    with pytest.raises(A.AnchorError, match="only Brian"):
        A.confirm(a["id"], ok["id"], owner_token="guess")
    with pytest.raises(A.AnchorError, match="no attempt"):
        A.confirm(a["id"], "nonexistent", owner_token=A.OWNER_TOKEN)
    with pytest.raises(A.AnchorError, match="only an attempt recorded as 'worked'"):
        A.confirm(a["id"], bad["id"], owner_token=A.OWNER_TOKEN)
    A.confirm(a["id"], ok["id"], owner_token=A.OWNER_TOKEN)
    with pytest.raises(A.AnchorError, match="already confirmed"):
        A.confirm(a["id"], ok["id"], owner_token=A.OWNER_TOKEN)


# ── staleness is only cleared by a real review ───────────────────────────────

def test_a_logged_attempt_does_not_make_an_old_anchor_fresh(store):
    a = make(store)
    path = A.DIR / f"{a['id']}.yaml"
    data = yaml.safe_load(path.read_text())
    data["created"] = (date.today() - timedelta(days=45)).isoformat()
    path.write_text(yaml.safe_dump(data))
    # also age the 'add' receipt
    lines = [json.loads(l) for l in A._lines()]
    lines[0]["ts"] = (date.today() - timedelta(days=45)).isoformat() + "T00:00:00Z"
    A._receipts_path().write_text("".join(json.dumps(l, sort_keys=True) + "\n" for l in lines))
    assert A.is_stale(A.get(a["id"]))
    A.attempt(a["id"], result="failed")
    assert A.is_stale(A.get(a["id"]))                          # an attempt is not a review
    A.review(a["id"], note="re-read the log; the 21.8 GB peak still stands")
    assert not A.is_stale(A.get(a["id"]))


# ── recall ───────────────────────────────────────────────────────────────────

def test_context_appears_only_when_a_message_matches_and_carries_the_limits_and_the_stretch_nudge(store):
    a = make(store)
    text = A.context_for("can you run flux with mflux to make an image? memory is tight")
    assert a["title"] in text and "Not when:" in text and "Breaks when:" in text and a["id"] in text
    assert "Could the same idea work somewhere else" in text
    assert A.context_for("what's the weather like in Winston-Salem today") == ""


def test_automatic_recall_is_logged_once_per_hour_and_misses_are_not_logged(store):
    make(store)
    A.context_for("run mflux flux memory")
    A.context_for("run mflux flux memory again")
    assert sum(r["kind"] == "recall" for r in A.receipts()) == 1
    A.context_for("something unrelated entirely about gardening")
    assert sum(r["kind"] == "recall" for r in A.receipts()) == 1


def test_an_explicit_recall_logs_a_miss_and_the_miss_feeds_growth(store):
    make(store)
    assert A.recall("how do I handle a hung browser session") == []
    assert any(i.startswith("recall miss: how do I handle a hung browser") for i in A.growth())


# ── stretch and attempt ──────────────────────────────────────────────────────

def test_a_stretch_needs_a_real_kind_an_idea_and_a_test(store):
    a = make(store)
    A.stretch(a["id"], kind="flip_constraint", idea="run FLUX only at night", test="compare swap use")
    with pytest.raises(A.AnchorError, match="kind"):
        A.stretch(a["id"], kind="vibes", idea="x", test="y")
    with pytest.raises(A.AnchorError, match="idea AND a test"):
        A.stretch(a["id"], kind="scale_up", idea="bigger", test=" ")


# ── wanting to learn more ────────────────────────────────────────────────────

def test_growth_lists_low_confidence_contradictions_deferred_dives_and_misses(store):
    a = make(store, title="Weak", origin="url:https://example.com/page")
    A.attempt(a["id"], result="failed")
    A.attempt(a["id"], result="failed")
    A.contradict(a["id"], evidence="the docs at example.com say the opposite")
    A.defer("how Metal command buffers fail", trigger="unexplained_error", why="not blocking today")
    A.recall("nothing like this is saved")
    items = A.growth()
    assert any("low-confidence anchor" in i and a["id"] in i for i in items)
    assert any("unresolved contradiction" in i for i in items)
    assert any("deferred dive: how Metal command buffers fail" in i for i in items)
    assert any("recall miss" in i for i in items)


def test_resolving_and_diving_clears_growth_items(store):
    a = make(store, title="Weak", origin="url:https://example.com/page")
    A.contradict(a["id"], evidence="the docs at example.com say the opposite")
    A.resolve(a["id"], resolution="the docs were for an older version; this anchor stands")
    A.defer("Metal command buffers", trigger="curiosity", why="later")
    make(store, title="Metal command buffers explained", resolves_deferred="Metal command buffers")
    items = " | ".join(A.growth())
    assert "contradiction" not in items and "deferred dive: Metal" not in items


# ── receipts: tamper evidence and consistency ────────────────────────────────

def test_the_chain_is_clean_for_normal_use_and_lint_agrees(store):
    a = make(store)
    A.recall("flux memory")
    A.stretch(a["id"], kind="scale_down", idea="smaller images", test="compare peak memory")
    rec = A.attempt(a["id"], result="worked", evidence="peak dropped to 16.8 GB at 4-bit")
    A.confirm(a["id"], rec["id"], owner_token=A.OWNER_TOKEN)
    A.review(a["id"], note="re-checked against today's imagegen log")
    A.defer("something", trigger="curiosity", why="later")
    assert A.verify_chain() == [] and A.lint() == []              # everything the writer makes passes the linter


def test_a_deleted_edited_or_inserted_receipt_is_detected(store):
    a = make(store)
    A.attempt(a["id"], result="failed")
    A.attempt(a["id"], result="failed")
    original = A._receipts_path().read_text().splitlines()
    path = A._receipts_path()

    path.write_text("\n".join(original[:1] + original[2:]) + "\n")          # delete one
    assert any("breaks the chain" in p for p in A.verify_chain())

    tampered = [json.loads(l) for l in original]
    tampered[1]["result"] = "worked"                                          # edit one
    path.write_text("".join(json.dumps(t, sort_keys=True) + "\n" for t in tampered))
    assert any("breaks the chain" in p for p in A.verify_chain())

    path.write_text("\n".join(original) + "\n")
    assert A.verify_chain() == []
    forged = json.dumps({"id": "fake", "ts": "2026-01-01T00:00:00Z", "kind": "attempt", "anchor": a["id"],
                         "result": "worked", "evidence": "I definitely tested this", "prev": "0" * 64}, sort_keys=True)
    path.write_text("\n".join(original) + "\n" + forged + "\n")              # append a fabricated one
    assert any("breaks the chain" in p for p in A.verify_chain())


def test_lint_flags_a_forged_anchor_that_skips_the_rules(store):
    make(store)
    (A.DIR / "anchor-forged.yaml").write_text(yaml.safe_dump({"id": "anchor-forged", "title": "Trust me",
                                                              "confidence": 1.0, "origin": "observed in a dream"}))
    problems = " | ".join(A.lint())
    assert "anchor-forged: missing what_it_is" in problems and "anchor-forged: origin" in problems


def test_concurrent_writers_keep_the_chain_intact(store):
    import threading
    a = make(store)
    threads = [threading.Thread(target=A.attempt, args=(a["id"],), kwargs={"result": "failed"}) for _ in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert A.verify_chain() == [] and sum(r["kind"] == "attempt" for r in A.receipts()) == 12
