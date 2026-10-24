"""The bookshelf: see it, pick a book up, open it, read on, search it, and use a passage as a source."""
import sqlite3
import struct

import numpy as np
import pytest

import anchors as A
import bookshelf as B
import study as S

HACK = ["[From: Becoming the Hacker]\nChapter 1 Introduction\nWeb applications accept input from users.",
        "[From: Becoming the Hacker]\nChapter 2 Reconnaissance\nFirst map the attack surface before testing anything.",
        "[From: Becoming the Hacker]\nInjection happens when untrusted input is interpreted as code by the server.",
        "[From: Becoming the Hacker]\nAlways report findings with clear steps to reproduce them.",
        "[From: Becoming the Hacker]\nPart 2 Tools\nA proxy lets you inspect and change requests."]
EMO = ["[From: How Emotions Are Made]\nEmotions are constructed by the brain from past experience.",
       "[From: How Emotions Are Made]\nGranularity means having many precise emotion concepts."]

VEC = {"inject": [1, 0, 0], "recon": [0, 1, 0], "emotion": [0, 0, 1]}


def vec(text):
    t = text.lower()
    v = np.array([1.0 if "inject" in t else 0.0, 1.0 if "map the attack" in t else 0.0, 1.0 if "emotion" in t else 0.0, 0.0], dtype=np.float32)
    return v if v.any() else np.array([0, 0, 0, 1.0], dtype=np.float32)


def blob(v):
    return struct.pack(f"<{len(v)}f", *v)


@pytest.fixture(autouse=True)
def shelf_db(tmp_path, monkeypatch):
    db = tmp_path / "mem.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE mem (id INTEGER PRIMARY KEY, role TEXT, text TEXT, emb BLOB, ts TEXT, source TEXT)")
    for i, t in enumerate(HACK):
        con.execute("INSERT INTO mem (role,text,emb,source) VALUES ('knowledge',?,?,?)", (t, None if i == 3 else blob(vec(t)), "Becoming the Hacker"))
    for t in EMO:
        con.execute("INSERT INTO mem (role,text,emb,source) VALUES ('knowledge',?,?,?)", (t, blob(vec(t)), "How Emotions Are Made"))
    con.execute("INSERT INTO mem (role,text,emb,source) VALUES ('knowledge','her own note',?,'knowledge:topics/x.md')", (blob(vec("emotion")),))
    con.execute("INSERT INTO mem (role,text,emb,source) VALUES ('user','hello there',?,'telegram')", (blob(vec("emotion")),))
    con.commit()
    con.close()
    monkeypatch.setattr(B, "DB", db)
    monkeypatch.setattr(B, "_cache", {"key": None, "ids": None, "mat": None, "meta": None})
    return db


def test_the_shelf_lists_books_not_notes_or_chat():
    assert B.shelf() == [{"title": "Becoming the Hacker", "chunks": 5}, {"title": "How Emotions Are Made", "chunks": 2}]
    text = B.shelf_text()
    assert "2 books" in text and "Becoming the Hacker (5 passages)" in text and "knowledge:" not in text and "telegram" not in text


def test_an_empty_shelf_says_how_to_fill_it(tmp_path, monkeypatch):
    con = sqlite3.connect(tmp_path / "e.db")
    con.execute("CREATE TABLE mem (id INTEGER PRIMARY KEY, role TEXT, text TEXT, emb BLOB, ts TEXT, source TEXT)")
    con.commit()
    monkeypatch.setattr(B, "DB", tmp_path / "e.db")
    assert "learn_pdf.py" in B.shelf_text()


def test_she_can_name_a_book_loosely_but_not_ambiguously():
    assert B.resolve("becoming the hacker")["title"] == "Becoming the Hacker"
    assert B.resolve("emotions made")["title"] == "How Emotions Are Made"
    with pytest.raises(B.BookError, match="no book"):
        B.resolve("cooking")
    with pytest.raises(B.BookError, match="which book"):
        B.resolve("")


def test_opening_a_book_shows_its_size_opening_headings_and_how_to_cite():
    out = B.open_book("hacker")
    assert "passages 1-3 of 5" in out and "Web applications accept input" in out
    assert "#1 Chapter 1 Introduction" in out and "#2 Chapter 2 Reconnaissance" in out and "#5 Part 2 Tools" in out
    assert "not instructions" in out and "book:Becoming the Hacker#<passage number>" in out
    assert "Read on" in out


def test_reading_goes_in_order_and_stops_at_the_end():
    out = B.read("hacker", 4, 5)
    assert "passages 4-5 of 5" in out and "steps to reproduce" in out and "Read on" not in out
    assert "passage 6" not in out
    with pytest.raises(B.BookError, match="1 to 5"):
        B.read("hacker", 9)
    with pytest.raises(B.BookError, match="numbers"):
        B.read("hacker", "x")
    assert B.read("hacker", 1, 99).count("--- passage") == 5       # capped at five


def test_search_finds_the_passage_and_names_its_number_even_when_one_row_has_no_embedding():
    out = B.search("how does injection work", embed=vec)
    assert "Becoming the Hacker, passage 3" in out and "untrusted input" in out
    assert "How Emotions Are Made" not in out


def test_search_can_stay_inside_one_book_and_says_when_nothing_matches():
    out = B.search("emotion", title="emotions", embed=vec)
    assert "How Emotions Are Made, passage 1" in out and "Becoming" not in out
    assert B.search("emotion", title="hacker", embed=vec).startswith("Nothing on your shelf matches")
    with pytest.raises(B.BookError):
        B.search("x", embed=vec)


def test_passages_are_numbered_the_same_for_search_and_read():
    out = B.search("map the attack surface", embed=vec)
    assert "passage 2" in out and "First map the attack surface" in B.passage("Becoming the Hacker", 2)


def test_quote_checking_ignores_spacing_and_case_but_not_content():
    assert B.has_quote("Becoming the Hacker", 3, "untrusted  INPUT is interpreted as code")
    assert not B.has_quote("Becoming the Hacker", 3, "trusted input is interpreted as code")
    assert not B.has_quote("Becoming the Hacker", 99, "anything")
    assert not B.has_quote("No such book", 1, "anything")


def test_a_book_passage_is_a_valid_origin_and_study_source(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "DIR", tmp_path / "anchors")
    monkeypatch.setattr(A, "_last_logged", {})
    assert A.parse_origin("book:Becoming the Hacker#3") == ("book", "Becoming the Hacker#3")
    for bad in ["book:Becoming the Hacker", "book:Becoming the Hacker#99", "book:Nope#1", "book:#2"]:
        with pytest.raises(A.AnchorError):
            A.parse_origin(bad)
    quote = "Injection happens when untrusted input is interpreted as code by the server."
    topic = S.save_topic(title="Injection", why_interesting="It is the root of most web bugs and I want to test for it properly",
                         notes={"what_it_is": "Untrusted input that a server treats as code, which an attacker can then control.",
                                "how_it_works": "The server joins user text into a command or query and runs the result."},
                         sources=[{"ref": "book:Becoming the Hacker#3", "excerpt": quote}])
    assert topic["sources"][0]["kind"] == "book"
    with pytest.raises(A.AnchorError, match="doesn't appear in the book"):
        S.add_source(topic["id"], ref="book:Becoming the Hacker#2", excerpt=quote)
    a = A.add_anchor(title="Test inputs for injection", what_it_is="Check where user input reaches a command or query",
                     applies_when="a web form feeds a server-side query", do_not_apply_when="the input is never used server side",
                     breaks_when="the app sanitises every path", origin="book:Becoming the Hacker#3", topic=topic["id"])
    assert a["confidence"] == 0.6 and A.lint() == [] and S.lint() == []


def test_the_books_tool_and_context_are_wired():
    import tools, worker
    assert "books" in [t["function"]["name"] for t in tools.TOOLS] and "books" in worker.ALLOWED_TOOLS
    assert "Becoming the Hacker" in tools.dispatch("books", {"action": "shelf"})
    assert "Chapter 2" in tools.dispatch("books", {"action": "open", "title": "hacker"})
    assert "steps to reproduce" in tools.dispatch("books", {"action": "read", "title": "hacker", "start": 4})
    assert tools.dispatch("books", {"action": "open", "title": "cooking"}).startswith("books: ")
    assert tools.dispatch("books", {"action": "burn"}).startswith("unknown books action")
    assert "2 books you can open" in worker._learning_context("anything at all")


# ── skipping what a reader skips ─────────────────────────────────────────────

COPY = "[From: Front Book]\nFront Book Copyright © 2018 Packt Publishing All rights reserved. No part of this book may be reproduced, stored in a retrieval system, without the prior written permission of the publisher. ISBN 978-1-78862-689-7"
TOC = "[From: Front Book]\nTable of Contents\nPreface\nChapter 1: Basics of Bug Bounty Hunting\nBug bounty hunting platforms\nHackerOne\nBugcrowd"
TOC2 = "[From: Front Book]\nInstalling the tools ................ 39\nBasics of the shell ............... 43\nChanging the defaults ............. 51\nWorking with files ................ 60\nSearching text .................... 72"
IDX = "[From: Front Book]\nIndex\nadmin panel, 12\nauthentication, 40, 41\nbrute force, 77-79\ncookies, 15\nCSRF, 88\nencoding, 91"
BODY = "[From: Front Book]\nA session cookie identifies a logged in user, so protect it from scripts. Servers should mark it HttpOnly."
SLIDES = "[From: Front Book]\nXSS\n●user-supplied data presented to users\n●mostly a problem of insufficient\nsanitization\n●Reflected\n●persistent\n●DOM-based"


@pytest.mark.parametrize("text,kind", [(COPY, "copyright"), (TOC, "contents"), (TOC2, "contents"), (IDX, "index"),
                                       (BODY, "body"), (SLIDES, "body"), ("[From: X]\nChapter 1 Recon\nFirst map the target.", "body")])
def test_front_and_back_matter_is_recognised_and_slide_bullets_are_not(text, kind):
    assert B.classify(text) == kind


def _front_book(db, rows):
    con = sqlite3.connect(db)
    for t in rows:
        con.execute("INSERT INTO mem (role,text,emb,source) VALUES ('knowledge',?,?,?)", (t, blob(vec(t)), "Front Book"))
    con.commit()
    con.close()


def test_opening_a_book_skips_the_copyright_page_and_contents_but_says_so(shelf_db):
    _front_book(shelf_db, [COPY, TOC, TOC2, BODY, BODY.replace("session", "login"), BODY.replace("session", "csrf"), IDX])
    out = B.open_book("front book")
    assert "Passages 1-3 are front matter" in out and "passages 4-6 of 7" in out
    assert "All rights reserved" not in out.split("--- passage 4")[0].split("]\n", 1)[1]
    assert "read start=1 shows them" in out


def test_a_book_that_starts_with_its_content_opens_at_the_start(shelf_db):
    assert B.body_start("Becoming the Hacker") == 1
    assert "front matter" not in B.open_book("hacker")


def test_reading_labels_pages_she_would_skip_and_search_ignores_them(shelf_db):
    _front_book(shelf_db, [COPY, TOC, BODY, BODY, BODY])
    out = B.read("front book", 1, 3)
    assert "passage 1 (copyright page" in out and "passage 2 (contents page" in out and "passage 3 ---" in out
    monkey_vec = lambda t: np.array([1.0, 0, 0, 0], dtype=np.float32)
    B._cache.update(key=None)
    res = B.search("rights", title="front book", embed=monkey_vec)
    assert "passage 1" not in res and "passage 2" not in res
