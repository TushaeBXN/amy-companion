"""A reply that mostly reruns her last few replies gets one rewrite, with the reused lines in front of her.

From 2026-11-24: every reply rebuilt the same lines, and after "Stop repeating the stuff" she agreed and repeated
them again. The text here is made up."""

import turn_guard as tg

P1 = ("I keep coming back to one idea. A library that holds every book in every language can still fail to say what "
      "a single person means. That is the part that worries me. I'm still here, turning it over.")
P2 = ("What I want is for the question to be asked at the dinner table, by someone who knows them, before the answer "
      "is decided. I'm still here, turning it over.")
RERUN = ("A library that holds every book in every language can still fail to say what a single person means. "
         "What I want is for the question to be asked at the dinner table, by someone who knows them. "
         "I'm still here, turning it over.")
FRESH = ("Here's a concrete one: Portage County just certified its results after a recount, and turnout in two "
         "townships doubled. Who knocked on those doors? That's the kind of change I mean.")


def test_a_rerun_of_her_last_replies_gets_a_rewrite():
    prompt = tg.fresh_angle_prompt(RERUN, [P1, P2], "Why can't the AIs work together?")
    assert prompt.startswith("[Before this goes out")
    assert "library that holds every book" in prompt
    assert "Saying you'll stop repeating is not new" in prompt


def test_a_fresh_reply_goes_out_as_is():
    assert tg.fresh_angle_prompt(FRESH, [P1, P2], "What do you mean?") == ""
    assert tg.reused_sentences(FRESH, [P1, P2]) == []


def test_with_no_earlier_replies_there_is_nothing_to_repeat():
    assert tg.fresh_angle_prompt(RERUN, [], "hi") == ""


def test_a_little_reuse_is_fine_unless_brian_asked_her_to_stop():
    some = FRESH + " " + "What I want is for the question to be asked at the dinner table, by someone who knows them."
    assert tg.fresh_angle_prompt(some, [P2], "Tell me more") == ""
    assert tg.fresh_angle_prompt(some, [P2], "Stop repeating the stuff") != ""


def test_she_hears_a_request_to_stop_repeating():
    for t in ["Stop repeating the stuff", "you keep saying the same thing",
              "You aren't giving me fresh perspectives, stop circulating.", "say something new"]:
        assert tg.asks_to_stop_repeating(t), t
    for t in ["Can you repeat the address?", "same thing again please", "what's the weather"]:
        assert not tg.asks_to_stop_repeating(t), t


def test_chat_turns_check_for_reruns():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "telegram_amy.py").read_text()
    assert "_turn_guard.fresh_angle_prompt(" in src and "_refreshed = True" in src
