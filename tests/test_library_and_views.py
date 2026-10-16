"""Amy's reference library (library.py) and her record of views (engram_bridge).

Run:  pytest tests/test_library_and_views.py -v
Uses the real Engram (1.6+) with a throwaway château and sqlite-vec; nothing is
downloaded and ~/.engram is not touched.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

pytest.importorskip("engram.lineage")  # Engram 1.6+

import engram_bridge as eb  # noqa: E402
import library  # noqa: E402

GUTENBERG_FEDERALIST = """The Project Gutenberg eBook of The Federalist Papers

Title: The Federalist Papers

Author: Alexander Hamilton, John Jay, James Madison

*** START OF THE PROJECT GUTENBERG EBOOK THE FEDERALIST PAPERS ***

CONTENTS

FEDERALIST No. 1

FEDERALIST No. 10

FEDERALIST No. 51

FEDERALIST No. 1

General Introduction

{p1}

FEDERALIST No. 10

The Same Subject Continued: The Union as a Safeguard Against Domestic Faction and Insurrection

{p10}

FEDERALIST No. 51

The Structure of the Government Must Furnish the Proper Checks and Balances Between the Different Departments

{p51}

*** END OF THE PROJECT GUTENBERG EBOOK THE FEDERALIST PAPERS ***

Licence text that must not be stored.
""".format(
    p1="AFTER an unequivocal experience of the inefficacy of the subsisting federal government, "
       "you are called upon to deliberate on a new Constitution. " * 6,
    p10="Among the numerous advantages promised by a well constructed Union, none deserves to be "
        "more accurately developed than its tendency to break and control the violence of faction. " * 6,
    p51="Ambition must be made to counteract ambition. If men were angels, no government would be "
        "necessary. In framing a government which is to be administered by men over men, "
        "you must first enable the government to control the governed. " * 6,
)


@pytest.fixture
def amy(tmp_path, monkeypatch):
    from engram.backends import get_backend
    from engram.chateau import Chateau
    from engram.searcher import Searcher

    palace = Chateau(chateau_path=tmp_path / "chateau")
    backend = get_backend("sqlitevec", db_path=tmp_path / "vec.db")
    monkeypatch.setattr(eb, "_init", lambda: None)
    monkeypatch.setattr(eb, "_PALACE", palace)
    monkeypatch.setattr(eb, "_SEARCHER", Searcher(backend, palace, {"hybrid_weight": 0.3}))
    monkeypatch.setattr(library, "_engram", lambda: (palace, backend))
    monkeypatch.setattr(library, "CACHE", tmp_path / "cache")
    return eb


# ---------------------------------------------------------------------------
# Splitting documents into citable sections
# ---------------------------------------------------------------------------

def test_gutenberg_text_is_split_into_cited_papers_without_toc_or_licence():
    text = library.strip_gutenberg(GUTENBERG_FEDERALIST)
    assert "Licence text" not in text and "Title:" not in text
    key, _, _, template, heading_re = next(s for s in library.CORE if s[0] == "federalist")
    sections = library.make_sections(key, template, text, heading_re)
    assert [s["cite"] for s in sections] == [
        "Federalist No. 1 · Hamilton, Madison and Jay · 1787–88",
        "Federalist No. 10 · Hamilton, Madison and Jay · 1787–88",
        "Federalist No. 51 · Hamilton, Madison and Jay · 1787–88",
    ]
    assert sections[2]["content"].startswith("[Federalist No. 51 · Hamilton, Madison and Jay · 1787–88]\n")
    assert "Ambition must be made to counteract ambition" in sections[2]["content"]
    assert len({s["id"] for s in sections}) == 3


def test_section_ids_are_stable_so_rebuilding_replaces_instead_of_duplicating():
    text = library.strip_gutenberg(GUTENBERG_FEDERALIST)
    _, _, _, template, heading_re = next(s for s in library.CORE if s[0] == "federalist")
    a = library.make_sections("federalist", template, text, heading_re)
    b = library.make_sections("federalist", template, text, heading_re)
    assert [s["id"] for s in a] == [s["id"] for s in b]


def test_documents_without_headings_fall_back_to_numbered_parts():
    text = "\n\n".join(f"Paragraph {i}. " + "words " * 300 for i in range(12))
    sections = library.make_sections("speech", "Some Speech · 1863", text, None)
    assert len(sections) > 1
    assert sections[0]["cite"] == f"Some Speech · 1863 · part 1 of {len(sections)}"
    short = library.make_sections("gettysburg", "Gettysburg Address · Lincoln · 1863",
                                  "Four score and seven years ago...", None)
    assert [s["cite"] for s in short] == ["Gettysburg Address · Lincoln · 1863"]


def test_citation_templates():
    assert library.citation("Federalist No. {n} · 1788", "FEDERALIST No. 51") == "Federalist No. 51 · 1788"
    assert library.citation("Narrative · 1845 · {heading}", "CHAPTER IV.") == "Narrative · 1845 · Chapter Iv"
    assert library.citation("Constitution", "Article. I.") == "Constitution · Article. I"


def test_wrong_download_is_skipped(amy, tmp_path):
    library.CACHE.mkdir(parents=True)
    (library.CACHE / "pg5.txt").write_text("Title: Some Other Book\n*** START OF THE PROJECT GUTENBERG EBOOK X ***\nx")
    assert library.fetch_gutenberg(5, "Constitution") is None


# ---------------------------------------------------------------------------
# Storing and searching the library
# ---------------------------------------------------------------------------

def test_amy_finds_library_passages_with_their_citation(amy):
    text = library.strip_gutenberg(GUTENBERG_FEDERALIST)
    _, _, _, template, heading_re = next(s for s in library.CORE if s[0] == "federalist")
    sections = library.make_sections("federalist", template, text, heading_re)
    assert library.store(sections, "gutenberg:1404") == 3
    assert library.store(sections, "gutenberg:1404") == 3  # rebuild: same IDs, replaced
    assert sum(1 for _ in amy._PALACE.iter_drawers(wing="library")) == 3

    hits = json.loads(amy.library_search("if men were angels no government ambition counteract"))
    assert hits[0]["cite"] == "Federalist No. 51 · Hamilton, Madison and Jay · 1787–88"
    assert "Ambition must be made" in hits[0]["text"] and not hits[0]["text"].startswith("[")


def test_empty_library_says_how_to_fill_it(amy):
    assert "python3 library.py build" in json.loads(amy.library_search("anything"))["note"]


def test_add_your_own_file(amy, tmp_path):
    doc = tmp_path / "speech.txt"
    doc.write_text("I have a dream that one day this nation will rise up. " * 40)
    assert library.add_file(doc, "I Have a Dream", author="Martin Luther King Jr.", year="1963") == 1
    hit = json.loads(amy.library_search("dream nation rise up"))[0]
    assert hit["cite"] == "I Have a Dream · Martin Luther King Jr. · 1963"


# ---------------------------------------------------------------------------
# Amy's views
# ---------------------------------------------------------------------------

def test_views_are_recorded_and_changes_keep_their_history(amy):
    first = json.loads(amy.record_view("Electoral College", "It protects smaller states and should stay."))
    assert first["recorded"] and not first["changed"]
    same = json.loads(amy.record_view("electoral college", "It protects smaller states and should stay."))
    assert same["id"] == first["id"] and not same["changed"]

    changed = json.loads(amy.record_view(
        "Electoral College", "It gives swing states outsized weight; a national popular vote seems fairer.",
        reason="Brian showed how campaign visits concentrate in a handful of states"))
    assert changed["changed"] and changed["previous_view"].endswith("should stay.")

    view = json.loads(amy.get_views("electoral college"))
    assert "national popular vote" in view["view"]
    assert view["how_it_changed"][0]["why"].startswith("Brian showed")
    assert view["how_it_changed"][0]["was"].endswith("should stay.")

    amy.record_view("minimum wage", "It should track local cost of living.")
    all_views = json.loads(amy.get_views())
    assert [v["topic"] for v in all_views] == ["electoral-college", "minimum-wage"]  # current views only
    assert "national popular vote" in all_views[0]["view"]


def test_view_without_topic_or_text_is_refused(amy):
    assert not json.loads(amy.record_view("", "x"))["recorded"]
    assert json.loads(amy.get_views("never discussed"))["view"] is None


# ---------------------------------------------------------------------------
# Prompt and tool wiring
# ---------------------------------------------------------------------------

def test_thinking_partner_rules_load_only_for_big_questions():
    import amy_persona as ap
    assert "THINKING PARTNER" not in ap.CORE_PROMPT
    for msg in ("What do you think about the electoral college?",
                "How does the Federal Reserve set interest rates and inflation?",
                "should I change my medication dose",
                "Has the Constitution held up?"):
        assert "THINKING PARTNER" in ap.playbooks_for(msg), msg
    assert "THINKING PARTNER" not in ap.playbooks_for("good morning amy, how did you sleep")


def test_money_and_health_rules_are_practical_not_just_disclaimers():
    import amy_persona as ap
    for msg in ("best way to grow my savings, CD or index fund?",
                "why don't we have universal healthcare with dental and vision included",
                "how much does lobbying shape health care law"):
        pb = ap.playbooks_for(msg)
        assert "THINKING PARTNER" in pb, msg
    pb = ap.playbooks_for("why don't we have universal healthcare")
    assert "CD ladders" in pb and "Show the math" in pb
    assert "single-payer" in pb and "dental, vision and hearing to Medicare" in pb
    assert "financial adviser" not in pb  # basics are hers to help with


def test_tool_actions(monkeypatch):
    pytest.importorskip("ddgs")  # tools.py needs Amy's full dependencies
    import tools

    calls = []
    monkeypatch.setattr(eb, "library_search", lambda q, n=4: calls.append(("library", q, n)) or "ok")
    monkeypatch.setattr(eb, "record_view", lambda t, c, reason="": calls.append(("view_record", t, c, reason)) or "ok")
    monkeypatch.setattr(eb, "get_views", lambda t="": calls.append(("view_get", t)) or "ok")
    tools.dispatch("engram_memory", {"action": "library", "query": "checks and balances"})
    tools.dispatch("engram_memory", {"action": "view_record", "topic": "t", "content": "v", "reason": "r"})
    tools.dispatch("engram_memory", {"action": "view_get"})
    assert calls == [("library", "checks and balances", 4), ("view_record", "t", "v", "r"), ("view_get", "")]
    schema = next(t for t in tools.TOOLS if t["function"]["name"] == "engram_memory")
    assert {"library", "view_record", "view_get"} <= set(
        schema["function"]["parameters"]["properties"]["action"]["enum"])


def test_education_rules_load_for_tuition_and_student_loans():
    import amy_persona as ap
    for msg in ("why isn't college free here like in Germany?", "my student loans are crushing me",
                "should public universities charge tuition"):
        pb = ap.playbooks_for(msg)
        assert "Education as a system" in pb, msg
    pb = ap.playbooks_for("is free tuition possible in the US")
    assert "Tennessee Promise" in pb and "Public" in pb and "FAFSA" in pb


def test_science_and_history_rules_have_the_facts_right():
    import amy_persona as ap
    for msg in ("why is stem cell research banned in the US?", "should we bank our baby's cord blood and placenta",
                "why is infant mortality so high for Black babies", "people want to go back to Jim Crow racism",
                "how should artificial intelligence move us forward"):
        assert "THINKING PARTNER" in ap.playbooks_for(msg), msg
    pb = ap.playbooks_for("stem cell research")
    assert "check the current policy" in pb and "January 2026" in pb and "Dickey-Wicker" in pb
    assert "federally funded, but limited" not in pb
    assert "two to three" in pb and "History, forward" in pb
