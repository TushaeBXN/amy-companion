"""A reply that loops on the same paragraphs is cut down to one copy of each before it is sent.

The shape is from 2026-11-24: four paragraphs from her previous reply, repeated until the 1,200-token cap cut
the reply off mid-sentence. The text here is made up."""

import turn_guard as tg

A = "I'm still thinking about this one, out loud."
B = "A city is not a spreadsheet. It's a bus driver who knows every stop, and a kid who waves at the train."
C = "So when I say I want people here, I mean I want them asked, at the kitchen table, in their own words."
NEW = "The part I'd add today: the people who carry the cost should be in the room when it's decided."


def test_a_reply_that_loops_keeps_one_copy_of_each_paragraph():
    looping = "\n\n".join([NEW, A, B, C, A, B, C, A, B, "So when I say I"])
    out = tg.drop_repeats(looping)
    assert out == "\n\n".join([NEW, A, B, C])


def test_paragraphs_copied_from_her_previous_reply_are_dropped():
    previous = "\n\n".join([A, B, C])
    reply = "\n\n".join([NEW, B, C])
    assert tg.drop_repeats(reply, previous) == NEW


def test_a_reply_that_is_only_a_repeat_is_left_alone_rather_than_emptied():
    previous = "\n\n".join([A, B])
    assert tg.drop_repeats(B, previous) == B


def test_ordinary_replies_are_unchanged():
    reply = "\n\n".join([NEW, A, B])
    assert tg.drop_repeats(reply, previous=C) == reply
    short = "Yes.\n\nYes.\n\nOkay."          # short lines may repeat
    assert tg.drop_repeats(short) == short
    code = "```\nx = 1\n```\n\n```\nx = 1\n```"
    assert tg.drop_repeats(code) == code


def test_whitespace_and_case_do_not_hide_a_repeat():
    reply = B + "\n\n" + "  " + B.upper().replace(" ", "  ") + "  "
    assert tg.drop_repeats(reply) == B


def test_chat_replies_go_through_the_guard():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "telegram_amy.py").read_text()
    assert "_turn_guard.drop_repeats(_reply_text, _prev)" in src


def test_recall_skips_worktree_copies(monkeypatch):
    import engram_bridge as eb

    class W:
        def __init__(self, name):
            self.name = name

    class P:
        def list_wings(self):
            return [W("ava-companion"), W("wt-docs"), W("wt-thoughts"), W("amy")]

    monkeypatch.setattr(eb, "_PALACE", P())
    skip = eb._memory_skip_wings()
    assert "wt-docs" in skip and "wt-thoughts" in skip
    assert "ava-companion" not in skip and "amy" not in skip
    assert set(eb.MEMORY_CONTEXT_SKIP_WINGS) <= set(skip)
