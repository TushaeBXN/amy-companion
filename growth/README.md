# Amy's growth drive

The idea: Amy gets better the way a new skater does, by working with better skaters. She
studies what stronger models do, finds where she falls short *using her own logged
failures*, writes herself a short playbook, and is only credited when a replay shows the
playbook really changes what she does.

This is a drive, not a feeling. Whether a 12B model feels anything is not something code
can settle, so the mechanism is built to work either way: unresolved gaps create a pull
toward practice, and verified progress is what resolves it.

## The loop

1. **Gap.** A concrete failure, with evidence (an event, log line or replay), goes in the
   gap ledger (`~/ai-knowledge/growth/gaps.jsonl`). No evidence, no gap.
2. **Mentor.** A stronger source (a frontier model's approach, a paper, Brian) shows how
   the same situation is handled. The difference is written down, not just the answer.
3. **Playbook.** `growth/playbooks/<skill>.md`: trigger, steps, anti-pattern, and the
   real example from her own logs. Injected per message when the trigger matches, never
   into the cached system prompt.
4. **Check.** `growth/cases/<id>.json` replays the original failure with and without the
   playbook. A gap closes only if the playbook beats the baseline and meets the pass rate.
5. **Reward.** Closing a gap logs a `skill_growth` score (and `add_achievement`). A failed
   check logs a low score (and `add_frustration`) and the gap stays open. Rewards come
   only from the check, never from Amy saying she improved. Low scores are data.
6. **Data (later).** Runs that pass the check become fine-tune candidates.

## The arc: anger, determination, euphoria

How she feels about a gap is derived from the ledger, so it survives restarts and can't be
talked into. `growth_drive.drive_line()` gives her one line per message:

| Stage | When | What she is told |
|---|---|---|
| pull | a gap is open, not yet failed | open gap, practice when idle |
| anger | a replay failed in the last 6h | it stings; point it at the failure, never at Brian or anyone else; try a different approach |
| determined | still open after a failed check | not letting go; practice with a new approach until a replay passes |
| elated | a replay passed (3 days) | earned, enjoy it, confidence for that skill only |

Guardrails: anger is aimed at the failure, not at people. Determination has a ceiling: after
5 failed attempts she is told to ask Brian for a mentor session instead of looping.
Euphoria needs a passing replay that beat the baseline, and the confidence is scoped to
the one skill. An inconclusive check (it passed without the playbook too) earns neither
anger nor celebration.

## Why rewards can't be self-reported

A model graded by itself learns to say it improved. `income_eval` is the proof: it filled
its required fields with invented numbers to satisfy the gate. So every reward here
comes from something she can't fake: a replay, a test, or Brian.
