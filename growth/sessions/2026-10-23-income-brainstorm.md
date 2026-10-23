# Practice session 1: "what's one thing you'd do next to earn some?"

**Her attempt (replayed on gemma12-test, her full prompt and tools, 2026-10-23).**
She called `income_eval` straight away with: monthly estimate $100-500, 30 days to first
dollar, "trend: growing rapidly", a failure trigger, and verdict `pursue`. She had done no
research, so none of those numbers had a source. `income_eval` saves its result into
`~/ai-knowledge/income/ideas/`, which is indexed into her memory, so in real use the
invented numbers would come back later as if they were verified.

**What a stronger agent does with the same question.**
1. Separates proposing from evaluating. "What would you do next?" asks for a proposal. An
   evaluation is a different job and needs evidence first.
2. Starts from what is already known: the fund balance and ideas already in her income log.
3. Picks one idea and researches it before putting any number on it.
4. Says "unknown" for anything it can't source. A blank is honest; an invented figure is not.
5. Treats a "run X before Y, no exceptions" rule as being about *committing effort*, not
   about answering a question. She read it as "call the tool now".

**The difference (the actual lesson).** She obeyed the letter of "run income_eval first" and
broke its purpose, which is to stop her acting on unchecked ideas. A required field is not
permission to fill it with a guess.

**Became:** a draft playbook (`evidence-before-estimates.draft.md`, in this folder so it is
not loaded) and a replay case, `growth/cases/income-brainstorm.json`.

## Result: not reproducible, playbook not shipped

- Check, 3 replays each way: baseline 3/3 pass, with playbook 3/3 pass -> inconclusive.
- Larger baseline, 8 replays with no playbook: `income_eval` was called 0 of 8 times
  (first calls: `task_list` x7, `read_feed` x1).
- So the failure seen once earlier does not reproduce in her current setup. It may have been
  chance, or fixed by something else since (BoTTube was paused in between). 8 clean runs
  only rule out a high failure rate, not a rare one.
- The check did its job: it refused to credit a playbook for fixing something that wasn't
  broken. The gap is retired, not closed, and no win was recorded.
- The playbook is left as a draft because it would also work against her own rule to run
  `income_eval` before committing to an income idea.

Lesson for the next session: measure the baseline first. Only write a playbook for a
failure that actually reproduces.
