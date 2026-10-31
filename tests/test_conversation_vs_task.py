"""Her instructions must say plainly that ordinary conversation gets zero tool calls — the real
2026-10-30 bug: a simple reflective question ('what are you hoping to say when they finally write
back') triggered 16+ unrelated tool calls (email, task list, codebase map, journal), burned her whole
step budget, and only then got correctly flagged as stalled. The fix is upstream: tell her plainly that
most messages need no tool at all, not just patch what happens after she's already spiraled."""
import worker
import amy_persona


def test_scope_rules_states_zero_tool_calls_as_the_default_for_conversation():
    r = worker.scope_rules(16)
    assert "ZERO tool calls" in r and "Most messages are just conversation" in r
    assert "like a friend talking to you" in r
    # the real-job framing must still exist for genuine multi-step work
    assert "When a message IS a real job" in r and "16 tool steps" in r
    assert "only on an actual job; don't manufacture tool calls" in r


def test_scope_rules_still_reports_the_right_step_count():
    assert "7 tool steps" in worker.scope_rules(7)
    assert "step N of 7" in worker.scope_rules(7)


def test_persona_distinguishes_a_request_to_do_something_from_conversation():
    p = amy_persona.SYSTEM_PROMPT
    assert "is conversation, and the right answer to" in p
    assert "not everything he says to you is a request to go do something" in p
    # the existing "what Brian says is real" / goal-making guidance must still be present
    assert "What Brian tells you is real" in p and "make it a goal" in p
