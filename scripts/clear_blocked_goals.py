"""Mark every blocked goal as abandoned, so Amy starts with a clean goal list.

Nothing is deleted: the goals stay in amy_memory.db with status 'abandoned'.
Run it on the Mac, from the project folder:
    venv/bin/python scripts/clear_blocked_goals.py            # do it
    venv/bin/python scripts/clear_blocked_goals.py --dry-run  # only list them
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import agent_state  # noqa: E402

dry = "--dry-run" in sys.argv
blocked = agent_state.list_goals("blocked")
for g in blocked:
    print(f"{'would abandon' if dry else 'abandoned'}: {g['goal_id']} | {g['objective'][:70]!r}")
    if not dry:
        agent_state.abandon_goal(g["goal_id"])
left = len(agent_state.list_goals("blocked"))
print(f"{len(blocked)} blocked goal(s) {'found' if dry else 'cleared'}; {left} still blocked.")
