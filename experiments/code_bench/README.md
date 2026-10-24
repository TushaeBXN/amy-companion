# Code benchmark: Amy's model vs. an abliterated Qwen coder (2026-10-24)

Question: is Amy's own model (`gemma12-test`, Gemma4-12B-QAT-Uncensored-HauhauCS-Balanced-Q4_K_M) good enough at
small coding tasks, or would a separate local coding model help?

Method: 20 single-function Python tasks (10 easy, 10 hard) in `bench.py`. The model gets one prompt, thinking off, up
to 2000 tokens. Its code is run against asserts we wrote; it passes only if every assert holds. Models were run on a
16 GB Mac mini M4 through Ollama, one at a time.

Models compared:

| name here | file | notes |
|---|---|---|
| `gemma12-test` | Gemma4-12B-QAT-Uncensored-HauhauCS-Balanced-Q4_K_M.gguf | Amy's live model |
| `amy-coder` | bartowski `Qwen2.5-Coder-7B-Instruct-abliterated-Q4_K_M.gguf` (4.68 GB) | sha256 `fdf02c16...` matched Hugging Face |
| `amy-coder-q5` | bartowski `Qwen2.5-Coder-7B-Instruct-abliterated-Q5_K_S.gguf` (5.3 GB) | sha256 `f4ae93f2...` matched Hugging Face |

## Result

| set | `gemma12-test` | `amy-coder` (Q4_K_M) | `amy-coder-q5` (Q5_K_S) |
|---|---|---|---|
| easy (10) | 10 / 10 | 9 / 10 | not run |
| hard (10) | 9 / 10 | 3 / 10 | 3 / 10 |

Amy's model was better on these tasks, and the abliterated Qwen coder was not worth adding as a second model. The
coders lost mostly by missing details the prompt spelled out (for example, writing "Insufficient funds" when the
prompt demanded the exact text `insufficient funds`) and by one fix that sorted the caller's list in place, which the
test treats as a bug. The stock (non-abliterated) Qwen coder was **not** tested, so this says nothing about Qwen
coders in general.

## What is in `results/`, and why some numbers there are wrong

The raw logs are kept as they were produced. Two of them contain mistakes of ours, which is why the table above does
not match them directly:

1. `easy_first_run_INVALID_gemma_thinking_on.log`: Gemma scored 5/10 here. That run left thinking **on** and capped
   answers at 700 tokens, so Gemma ran out of room before finishing code. The coder (no thinking) was unaffected.
   The run is not a fair comparison for Gemma.
2. `easy_rerun_failures_thinking_off.log`: the five tasks Gemma failed, rerun with thinking off and 1500 tokens. Gemma
   passed all five, which with its five earlier passes gives 10/10. The coder's `top_words` failure reproduced here.
3. `hard_run_as_logged.log`: Gemma shows 8/10 because the old checker used a text search and blocked its
   `evalexpr` answer, whose docstring said "without using eval()". The code never called `eval`. The logged answer
   was not kept, so we generated that task again and checked it with a syntax-tree check: Gemma's fresh answer
   passes (9/10), while both coders' fresh answers still fail it, as logged. `bench.py` now checks the syntax tree,
   so this mistake can't recur.

## Limits

Twenty small, self-contained Python functions, one attempt each, tests we wrote ourselves. This does not show how a
model does on multi-file projects, other languages, or debugging a large unfamiliar codebase. It does show that
Amy's model can write short correct functions when thinking is off.

Thinking-off is also how her chat and tasks are run (`AMY_THINK=1` turns thinking back on in the web/terminal chat).
