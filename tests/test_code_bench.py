"""The benchmark's checker: it runs only safe code, and it judges by real calls, not by words in a docstring."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments" / "code_bench"))
import bench as B

ADD = "assert add(1, 2) == 3"


def test_correct_code_passes_and_wrong_code_fails_with_the_reason():
    assert B.check("def add(a, b):\n    return a + b", ADD) == "pass"
    assert B.check("def add(a, b):\n    return a - b", ADD).startswith("fail: AssertionError")
    assert B.check("def add(a, b)\n    return a + b", ADD).startswith("fail: SyntaxError")


def test_a_word_in_a_docstring_or_comment_is_not_a_call():
    code = 'def add(a, b):\n    """Adds without using eval() or open()."""\n    # never import os here\n    return a + b'
    assert B.check(code, ADD) == "pass"


def test_real_dangerous_calls_and_imports_are_blocked_before_running():
    for code in ["import os\ndef add(a, b): return a + b", "from subprocess import run\ndef add(a, b): return a + b",
                 "def add(a, b):\n    return eval('a + b')", "def add(a, b):\n    open('x', 'w')\n    return a + b"]:
        assert B.check(code, ADD).startswith("blocked")


def test_an_infinite_loop_times_out(monkeypatch):
    assert B.check("def add(a, b):\n    while True: pass", ADD) in ("timeout",)


def test_code_is_taken_from_a_fenced_block_and_every_task_is_well_formed():
    assert B.code_of("here you go\n```python\nx = 1\n```\nthanks") == "x = 1"
    for t in B.EASY + B.HARD:
        assert len(t) == 3 and all(isinstance(p, str) and p for p in t)
    assert len(B.EASY) == 10 and len(B.HARD) == 10
