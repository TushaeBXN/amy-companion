"""Quotations Amy can't back up get flagged (quote_check.py). No Engram or model needed."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import quote_check as qc  # noqa: E402

FOURTEENTH = ("No State shall make or enforce any law which shall abridge the privileges or immunities of "
              "citizens of the United States; nor shall any State deprive any person of life, liberty, or "
              "property, without due process of law; nor deny to any person within its jurisdiction the equal "
              "protection of the laws.")
FIFTH_REAL = ("No person shall be held to answer for a capital, or otherwise infamous crime, unless on a "
              "presentment or indictment of a grand jury; nor be deprived of life, liberty, or property, "
              "without due process of law; nor shall private property be taken for public use, without just compensation.")
DAVIS = ("In Washington v. Davis, the Court held that someone claiming harm from the disparate or "
         "disproportionate racial impact of a race-neutral law or policy must prove the government's intent to discriminate.")
SOURCES = f"[Amendment XIV, Section 1]\n{FOURTEENTH}\n\n[Facially Neutral Laws]\n{DAVIS}"


def test_a_real_quotation_with_edits_and_ellipses_passes():
    reply = ('The text says: "nor shall any State deprive any person of life, liberty, or property, without due '
             'process of law; nor deny to any person within its jurisdiction th[e] equal protection of the laws."')
    assert qc.unverified(reply, SOURCES) == []
    reply = '> "No State shall make or enforce any law ... nor deny to any person within its jurisdiction the equal protection of the laws."'
    assert qc.unverified(reply, SOURCES) == []


def test_curly_quotes_italics_and_attribution_lines_are_handled():
    reply = (f"> “In *Washington v. Davis*, the Court held that someone claiming harm from the disparate or "
             f"disproportionate racial impact of a race-neutral law or policy must prove the government’s intent to discriminate.”\n"
             f"> — *Constitution Annotated*")
    assert qc.unverified(reply, SOURCES) == []


def test_a_made_up_quotation_is_flagged():
    fake = ("...or shall not make nor permit any man or woman to be deprived of life, liberty, or property, "
            "without due process of law; nor deny to any person in the United States equally protected rights, under the laws...")
    reply = f'The Fifth Amendment says: "{fake}" and the Fourteenth says "{FOURTEENTH}"'
    missing = qc.unverified(reply, SOURCES)
    assert len(missing) == 1 and "equally protected rights" in missing[0]
    note = qc.footnote(missing)
    assert note.startswith("\n\n⚠️") and "shall not make nor permit" in note and "unconfirmed" in note


def test_the_library_is_asked_about_what_the_turn_did_not_contain():
    reply = f'The Fifth Amendment says "{FIFTH_REAL}"'
    assert len(qc.unverified(reply, SOURCES)) == 1                       # not in this turn's text
    asked = []
    has = lambda frag: asked.append(frag) or frag in qc.normalize(FIFTH_REAL)  # noqa: E731
    assert qc.unverified(reply, SOURCES, library_has=has) == [] and asked   # but it is in the library
    broken = lambda frag: (_ for _ in ()).throw(RuntimeError("index down"))  # noqa: E731
    assert len(qc.unverified(reply, SOURCES, library_has=broken)) == 1       # a failing lookup never hides a flag


def test_short_phrases_and_ordinary_replies_are_left_alone():
    assert qc.unverified('He called it "a living document" and "the supreme law of the land".', "") == []
    assert qc.unverified("No quotations here, just a plain answer about coffee.", "") == []
    assert qc.footnote([]) == ""


def test_library_has_finds_real_passages_and_rejects_made_up_ones(tmp_path, monkeypatch):
    import pytest
    pytest.importorskip("engram.lineage")
    import engram_bridge as eb
    import library
    from engram.backends import get_backend
    from engram.chateau import Chateau
    from engram.searcher import Searcher

    palace = Chateau(chateau_path=tmp_path / "chateau")
    backend = get_backend("sqlitevec", db_path=tmp_path / "vec.db")
    monkeypatch.setattr(eb, "_init", lambda: None)
    monkeypatch.setattr(eb, "_PALACE", palace)
    monkeypatch.setattr(eb, "_SEARCHER", Searcher(backend, palace, {"hybrid_weight": 0.3}))
    library.store([{"id": "lib-5th", "room": "bill-of-rights", "cite": "Amendment V",
                    "content": f"[Amendment V]\n{FIFTH_REAL}"}], "test", palace, backend)
    real = qc.fragments("nor be deprived of life, liberty, or property, without due process of law; nor shall private property be taken")[0]
    fake = qc.fragments("nor deny to any person in the United States equally protected rights, under the laws")[0]
    assert eb.library_has(real) and not eb.library_has(fake)
    reply = f'The Fifth Amendment says "{FIFTH_REAL}" and also "nor deny to any person in the United States equally protected rights, under the laws"'
    assert len(qc.unverified(reply, "", library_has=eb.library_has)) == 1


# ── case names and citations that don't match the claim ───────────────────────

def test_case_names_are_found_even_after_a_lead_in_word():
    reply = ("In Washington v. Davis the Court held X. Under Chapman v. Meier, 420 U.S. 1, a deviation matters, "
             "and the recent Louisiana v. Callais moved toward intent. See also Baker v. Carr.")
    names = qc.case_names(reply)
    assert "Washington v. Davis" in names and "Chapman v. Meier" in names
    assert "Louisiana v. Callais" in names and "Baker v. Carr" in names
    assert not any(n.startswith(("In ", "Under ", "See ")) for n in names)


def test_only_case_names_with_no_source_are_flagged():
    sources = "Under Washington v. Davis, the challenger must prove purpose. Chapman v. Meier is about deviations."
    reply = "Washington v. Davis, Chapman v. Meier and Louisiana v. Callais all matter."
    assert qc.unsourced_cases(reply, sources) == ["Louisiana v. Callais"]
    assert qc.unsourced_cases(reply, sources, library_has=lambda n: n == "louisiana v callais") == []
    assert qc.unsourced_cases("No cases here.", sources) == []


def test_a_cited_passage_that_is_not_near_the_claim_is_flagged():
    reply = 'The passage is lib-2f8867ec: "a deviation of such magnitude"'
    claim = "Unequal effects without discriminatory purpose do not violate equal protection."
    assert qc.cited_ids(reply) == ["lib-2f8867ec"]
    assert qc.unsupported_citations(reply, claim, lambda text: ["lib-5c82efe9", "lib-7786351e"]) == ["lib-2f8867ec"]
    assert qc.unsupported_citations(reply, claim, lambda text: ["lib-2f8867ec12345"]) == []   # a longer ID of the same passage
    assert qc.unsupported_citations(reply, "", lambda text: []) == []
    assert qc.unsupported_citations(reply, claim, lambda text: (_ for _ in ()).throw(RuntimeError())) == []


def test_notes_cover_all_three_kinds_and_stay_empty_when_all_is_well():
    assert qc.notes([], [], []) == ""
    text = qc.notes([], ["Louisiana v. Callais"], ["lib-2f8867ec"])
    assert "Louisiana v. Callais" in text and "lib-2f8867ec" in text and "may not support" in text


def test_her_own_earlier_replies_are_never_a_source():
    earlier_invention = ('The Court said Section 2 now reaches "intentional discrimination in districting rather than '
                         'discriminatory effects" in Louisiana v. Callais.')
    history = [
        {"role": "system", "content": "You are Amy."},
        {"role": "user", "content": "[Message from Brian]\nDoes effect alone violate equal protection?"},
        {"role": "assistant", "content": earlier_invention},                       # her own words
        {"role": "tool", "content": "A search result about Washington v. Davis and intent."},
    ]
    sources = qc.sources_text(history)
    assert "Callais" not in sources and "Washington v. Davis" in sources and "equal protection" in sources
    # so repeating the invention is flagged, instead of being "found" in her own history
    assert qc.unverified(earlier_invention, sources) == ['intentional discrimination in districting rather than discriminatory effects']
    assert qc.unsourced_cases(earlier_invention, sources) == ["Louisiana v. Callais"]
    # while a real quote from a tool result or from Brian still passes
    assert qc.unverified('He wrote "Washington v. Davis and intent matter here for sure".', "x Washington v. Davis and intent matter here for sure x") == []
    # objects with model_dump (Ollama messages) are handled too
    class Msg:
        def model_dump(self):
            return {"role": "assistant", "content": "secret invention"}
    assert "secret invention" not in qc.sources_text([Msg(), {"role": "user", "content": "hi"}])
