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
    money = ap.playbooks_for("should I open a CD or buy an index fund")
    assert "CD ladders" in money and "Show the math" in money and "financial adviser" not in money
    health = ap.playbooks_for("why don't we have universal healthcare")
    assert "single-payer" in health and "dental, vision and hearing to Medicare" in health
    assert "CD ladders" not in health  # only the sections a message needs


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
        assert "EDUCATION AS A SYSTEM" in pb, msg
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
    assert "two to three" in ap.playbooks_for("infant mortality") and "HISTORY FORWARD" in ap.playbooks_for("Jim Crow")


def test_federal_changes_are_checked_not_assumed():
    import amy_persona as ap
    for msg in ("so many programs got defunded under Trump", "people lost their jobs at federal agencies",
                "is that grant still funded?"):
        pb = ap.playbooks_for(msg)
        assert "changes fast" in pb and "check current sources" in pb, msg


def test_appointee_qualifications_judged_from_the_record():
    import amy_persona as ap
    pb = ap.playbooks_for("is the new secretary of health even qualified for that agency?")
    assert "Who runs an agency matters" in pb and "same standard to every administration" in pb


def test_voting_rights_and_doj_rules():
    import amy_persona as ap
    for msg in ("is the DOJ going after voting rights?", "what did the Supreme Court do to the Voting Rights Act",
                "they're purging voter rolls before the election"):
        pb = ap.playbooks_for(msg)
        assert "VOTING RIGHTS AND THE JUSTICE DEPARTMENT" in pb, msg
    pb = ap.playbooks_for("voting rights")
    assert "Shelby County" in pb and "Louisiana v. Callais" in pb and "same test to any" in pb
    assert "Voting rights" not in ap.playbooks_for("I'm devoted to my morning coffee")


def test_trade_tariffs_and_conflicts_of_interest():
    import amy_persona as ap
    for msg in ("these tariffs are driving prices up", "the trade war with China",
                "he's enriching his family with crypto while in office"):
        assert "TRADE, TARIFFS AND PRICES" in ap.playbooks_for(msg), msg
    pb = ap.playbooks_for("tariffs")
    assert "passed on to American buyers" in pb and "emoluments" in pb and "Same standard for every president" in pb


def test_each_big_question_loads_only_its_own_section():
    import amy_persona as ap
    heads = ["MONEY PARTNER", "HEALTHCARE AS A SYSTEM", "EDUCATION AS A SYSTEM", "SCIENCE AND MEDICINE",
             "FEDERAL CHANGES", "VOTING RIGHTS AND THE JUSTICE DEPARTMENT", "TRADE, TARIFFS AND PRICES",
             "PRESS AND FREE SPEECH", "PROJECT 2025", "FOREIGN POLICY AND US TERRITORIES", "MOTHERS AND BABIES", "HISTORY FORWARD"]
    pb = ap.playbooks_for("why is college tuition so high?")
    assert "THINKING PARTNER" in pb and "EDUCATION AS A SYSTEM" in pb
    assert [h for h in heads if h in pb] == ["EDUCATION AS A SYSTEM"]
    assert len(pb) < 4000  # well under the old all-in-one playbook


def test_press_freedom_and_education_department():
    import amy_persona as ap
    for msg in ("they kicked reporters out of the White House", "he says he's for free speech but he's not",
                "the AP got banned"):
        assert "PRESS AND FREE SPEECH" in ap.playbooks_for(msg), msg
    pb = ap.playbooks_for("free speech")
    assert "viewpoint discrimination" in pb and "same line" in pb
    edu = ap.playbooks_for("the cuts to the Department of Education and going after Harvard")
    assert "McMahon v. New York" in edu and "ruled illegal" in edu


def test_sitting_president_owns_the_results():
    import amy_persona as ap
    pb = ap.playbooks_for("he keeps blaming Biden and Obama for everything")
    assert "The buck stops with whoever is in office" in pb and "Apply this to every president" in pb


def test_project_2025_checked_against_its_text():
    import amy_persona as ap
    for msg in ("a lot of this comes from Project 2025", "the Heritage Foundation plan"):
        assert "PROJECT 2025:" in ap.playbooks_for(msg), msg
    pb = ap.playbooks_for("project 2025")
    assert "Mandate for Leadership" in pb and "quote the chapter and page" in pb
    assert "claims people attribute to it that aren't in it" in pb


def test_pdfs_are_cited_by_page_range():
    pages = ["Chapter 11. Department of Education. " + "x " * 1200, "More on education. " + "y " * 1200,
             "", "Chapter 12. Department of Energy. " + "z " * 3500]
    sections = library.page_sections("mandate", "Mandate for Leadership (Project 2025) · Heritage Foundation · 2023",
                                     pages)
    assert [s["cite"].rsplit(" · ", 1)[1] for s in sections] == ["pp. 1–3", "p. 4"]
    assert sections[0]["content"].startswith("[Mandate for Leadership (Project 2025) · Heritage Foundation · 2023 · pp. 1–3]")
    assert len({s["id"] for s in sections}) == 2


def test_foreign_policy_and_territories():
    import amy_persona as ap
    for msg in ("cutting USAID is hurting people", "taking Maduro and the oil", "hitting boats near Venezuela",
                "fixing things with Canada", "how they treat Puerto Rico", "Cuba policy"):
        assert "FOREIGN POLICY AND US TERRITORIES" in ap.playbooks_for(msg), msg
    pb = ap.playbooks_for("Venezuela")
    assert "projection, not a count" in pb and "January 3, 2026" in pb and "same questions for any president" in pb


CONSTITUTION = """Title: The United States Constitution

*** START OF THE PROJECT GUTENBERG EBOOK THE UNITED STATES CONSTITUTION ***

Article 1

Section 1.  All legislative Powers herein granted shall be vested in a Congress. {a1}

Section 7.  All Bills for raising Revenue shall originate in the House; but the Senate may propose or concur with
Amendments as on other Bills.

ARTICLE 2

Section 1.  The executive Power shall be vested in a President of the United States of America. {a2}

ARTICLE THREE

Section 1.  The judicial Power of the United States, shall be vested in one supreme Court. {a3}

Amendment 14

Section 1.  All persons born or naturalized in the United States are citizens. {a14}

*** END OF THE PROJECT GUTENBERG EBOOK THE UNITED STATES CONSTITUTION ***
""".format(a1="x " * 300, a2="y " * 300, a3="z " * 300, a14="w " * 300)


def test_constitution_is_split_by_article_and_amendment(amy):
    key, _, _, template, heading_re = next(s for s in library.CORE if s[0] == "constitution")
    sections = library.make_sections(key, template, library.strip_gutenberg(CONSTITUTION), heading_re)
    assert [s["cite"] for s in sections] == [
        "U.S. Constitution · Article 1", "U.S. Constitution · Article 2",
        "U.S. Constitution · Article Three", "U.S. Constitution · Amendment 14"]
    assert "Amendments as on other Bills" in sections[0]["content"]  # not mistaken for a heading


def test_rebuilding_removes_a_documents_old_sections(amy):
    old = library.make_sections("constitution", "U.S. Constitution", "word " * 3000, None)
    library.store(old, "gutenberg:5")
    key, _, _, template, heading_re = next(s for s in library.CORE if s[0] == "constitution")
    new = library.make_sections(key, template, library.strip_gutenberg(CONSTITUTION), heading_re)
    library.store(new, "gutenberg:5")
    stored = sorted(d.content.split("]", 1)[0] for d in amy._PALACE.iter_drawers(wing="library"))
    assert stored == sorted("[" + s["cite"] for s in new)
