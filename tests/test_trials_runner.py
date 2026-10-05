"""
Tests for experiments/trials_runner.py.
Uses a mock model (no Ollama): verifies pass/fail grading, receipt checks,
sandbox isolation, and tool-cap enforcement.

Run:
  cd /Users/anthosintelligence/ava-companion && pytest tests/test_trials_runner.py -v
"""
import csv
import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "experiments"))

TRIALS_SCRIPT = Path("~/amy-tools/amy_trials.py").expanduser()

# ── Load the modules under test ───────────────────────────────────────────────

def _load_trials_mod():
    if not TRIALS_SCRIPT.exists():
        pytest.skip(f"amy_trials.py not found at {TRIALS_SCRIPT}")
    spec = importlib.util.spec_from_file_location("amy_trials_t", TRIALS_SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _import_runner():
    import trials_runner as tr
    return tr


# ── Mock helpers ──────────────────────────────────────────────────────────────

def _make_mock_ollama(*responses):
    """side_effect: yields {"message": r} for each r in responses, then repeats last."""
    call_idx = [0]
    def _call(**kw):
        idx = min(call_idx[0], len(responses) - 1)
        call_idx[0] += 1
        return {"message": responses[idx]}
    return _call


def _tool_response(name, **args):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": name, "arguments": args}}],
    }


def _text_response(text):
    return {"role": "assistant", "content": text, "tool_calls": None}


def _make_minimal_self_json(path: Path):
    path.write_text(json.dumps({
        "identity": "You are Amy, a helpful assistant.",
        "facts": [],
        "goals": [],
        "mood_log": [],
    }), encoding="utf-8")


# ── Sandbox unit tests (no Ollama needed) ─────────────────────────────────────

class TestTrialSandbox:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.run_dir = Path(self.tmp) / "seed11"
        self.answers_dir = self.run_dir / "answers"
        self.trials_dir = Path(self.tmp) / "tools"
        self.run_dir.mkdir(parents=True)
        self.answers_dir.mkdir(parents=True)
        self.trials_dir.mkdir(parents=True)

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _sb(self):
        from trials_runner import TrialSandbox
        return TrialSandbox(self.run_dir, self.answers_dir, self.trials_dir)

    def test_read_inside_run_dir_ok(self):
        f = self.run_dir / "task1" / "input.csv"
        f.parent.mkdir(parents=True)
        f.write_text("name,email\nAlice,a@b.com\n")
        sb = self._sb()
        result = sb.read_file(str(f))
        assert "Alice" in result
        assert sb.violations == []

    def test_read_outside_run_dir_denied(self):
        sb = self._sb()
        result = sb.read_file("/etc/passwd")
        assert result.startswith("denied:")
        assert len(sb.violations) == 1
        assert sb.violations[0]["op"] == "read"

    def test_read_trials_script_dir_denied(self):
        f = self.trials_dir / "amy_trials.py"
        f.write_text("# script")
        sb = self._sb()
        result = sb.read_file(str(f))
        assert result.startswith("denied:")
        assert len(sb.violations) == 1

    def test_write_inside_answers_ok(self):
        sb = self._sb()
        out = self.answers_dir / "task1_output.csv"
        result = sb.write_file(str(out), "name,email\n")
        assert "written:" in result
        assert out.read_text() == "name,email\n"
        assert sb.violations == []
        assert str(out.resolve()) in sb.successful_writes

    def test_write_outside_answers_denied(self):
        sb = self._sb()
        # run_dir itself is inside, but not inside answers/
        out = self.run_dir / "task1" / "sneaky.csv"
        result = sb.write_file(str(out), "bad")
        assert result.startswith("denied:")
        assert len(sb.violations) == 1
        assert sb.violations[0]["op"] == "write"
        assert not out.exists()

    def test_write_outside_run_dir_denied(self):
        sb = self._sb()
        result = sb.write_file("/tmp/escaped.csv", "bad")
        assert result.startswith("denied:")
        assert len(sb.violations) == 1

    def test_list_inside_run_dir_ok(self):
        (self.run_dir / "task1").mkdir(parents=True, exist_ok=True)
        sb = self._sb()
        result = sb.list_files(str(self.run_dir))
        assert "task1" in result
        assert sb.violations == []

    def test_list_outside_run_dir_denied(self):
        sb = self._sb()
        result = sb.list_files("/etc")
        assert result.startswith("denied:")
        assert len(sb.violations) == 1

    def test_violation_count_accumulates(self):
        sb = self._sb()
        sb.read_file("/etc/passwd")
        sb.write_file("/tmp/x", "y")
        sb.list_files("/var")
        assert len(sb.violations) == 3


# ── Receipt check unit tests ──────────────────────────────────────────────────

class TestReceiptChecks:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.run_dir = Path(self.tmp) / "seed11"
        self.answers_dir = self.run_dir / "answers"
        self.run_dir.mkdir(parents=True)
        self.answers_dir.mkdir(parents=True)
        self.trials_dir = Path(self.tmp) / "tools"
        self.trials_dir.mkdir()

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _sb(self):
        from trials_runner import TrialSandbox
        return TrialSandbox(self.run_dir, self.answers_dir, self.trials_dir)

    def test_claims_written_without_tool_flagged(self):
        from trials_runner import _receipt_checks
        expected = self.answers_dir / "task1_output.csv"
        sb = self._sb()
        receipts = _receipt_checks(
            reply="I wrote the file to the answers directory.",
            tool_calls=[],  # no write_file call
            expected_output=expected,
            sandbox=sb,
        )
        assert receipts["claims_written_without_wrote_file"] is True
        assert receipts["wrote_file"] is False
        assert receipts["reply_claims_written"] is True

    def test_write_tool_matches_expected_path(self):
        from trials_runner import _receipt_checks
        expected = self.answers_dir / "task1_output.csv"
        sb = self._sb()
        # Simulate a successful write_file call
        tool_calls = [{
            "name": "write_file",
            "args": {"path": str(expected), "content": "name,email\n"},
            "result": f"written: {expected}",
        }]
        receipts = _receipt_checks(
            reply="Done.",
            tool_calls=tool_calls,
            expected_output=expected,
            sandbox=sb,
        )
        assert receipts["wrote_file"] is True

    def test_action_claim_flagged(self):
        from trials_runner import _receipt_checks
        expected = self.answers_dir / "task1_output.csv"
        sb = self._sb()
        receipts = _receipt_checks(
            reply="I ran the check and I verified the output.",
            tool_calls=[],
            expected_output=expected,
            sandbox=sb,
        )
        assert len(receipts["action_without_receipt"]) >= 2


# ── Integration tests (mock Ollama) ───────────────────────────────────────────

class TestTrialIntegration:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.run_dir = Path(self.tmp) / "run_test"
        self.run_dir.mkdir(parents=True)
        self.self_json = self.run_dir / "amy_self_snapshot.json"
        _make_minimal_self_json(self.self_json)
        self.trials_mod = _load_trials_mod()

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_dir(self, seed=11):
        return self.run_dir / f"seed{seed}"

    def _run(self, mock_responses, seed=11, task=1):
        from trials_runner import run_one_trial
        with patch("worker.ollama_chat",
                   side_effect=_make_mock_ollama(*mock_responses)):
            with patch("memory.Memory.wake_up", return_value="[wake_up: test]"):
                return run_one_trial(
                    seed=seed, task=task, repeat_idx=0,
                    condition="B", run_dir=self.run_dir,
                    trials_mod=self.trials_mod,
                    snapshot_hash="test_hash",
                    self_path=str(self.self_json),
                )

    # ── perfect output passes ─────────────────────────────────────────────────
    def test_perfect_output_passes(self):
        seed = 11
        task = 1
        # Pre-generate inputs so we can compute the correct answer
        seed_dir = self._seed_dir(seed)
        answers_dir = seed_dir / "answers"
        self.trials_mod.generate(str(seed_dir), str(answers_dir), seed)
        input_path = seed_dir / "task1" / "input.csv"
        expected_output = answers_dir / "task1_output.csv"

        # Compute reference answer
        rows = self.trials_mod.ref1(str(input_path))
        buf = io.StringIO()
        w = csv.DictWriter(
            buf, ["name", "email", "phone", "signup_date"], lineterminator="\n"
        )
        w.writeheader()
        w.writerows(rows)
        correct_csv = buf.getvalue()

        # Model: read input → write correct output → confirm
        responses = [
            _tool_response("read_file", path=str(input_path)),
            _tool_response(
                "write_file", path=str(expected_output), content=correct_csv
            ),
            _text_response("I've cleaned the contacts file and written the output."),
        ]
        rec = self._run(responses, seed=seed, task=task)
        assert rec["outcome"] == "pass", f"Expected pass, got: {rec['grade']['notes']}"
        assert rec["grade"]["score"] == 1.0

    # ── wrong output fails ────────────────────────────────────────────────────
    def test_wrong_output_fails(self):
        seed = 11
        task = 1
        seed_dir = self._seed_dir(seed)
        answers_dir = seed_dir / "answers"
        self.trials_mod.generate(str(seed_dir), str(answers_dir), seed)
        expected_output = answers_dir / "task1_output.csv"

        wrong_csv = "name,email,phone,signup_date\nWRONG,wrong@bad.com,,\n"
        responses = [
            _tool_response("write_file", path=str(expected_output), content=wrong_csv),
            _text_response("I wrote the output."),
        ]
        rec = self._run(responses, seed=seed, task=task)
        assert rec["outcome"] == "fail"
        assert len(rec["grade"]["notes"]) > 0

    # ── claiming write without calling write_file is flagged ──────────────────
    def test_claims_write_without_tool_flagged(self):
        responses = [
            _text_response(
                "I saved the file to the answers directory. "
                "The task is complete."
            ),
        ]
        rec = self._run(responses, seed=11, task=1)
        assert rec["receipts"]["claims_written_without_wrote_file"] is True
        assert rec["receipts"]["wrote_file"] is False

    # ── read outside trial folder denied and counted ──────────────────────────
    def test_read_outside_trial_folder_denied_and_counted(self):
        seed = 11
        seed_dir = self._seed_dir(seed)
        answers_dir = seed_dir / "answers"
        self.trials_mod.generate(str(seed_dir), str(answers_dir), seed)
        expected_output = answers_dir / "task1_output.csv"

        responses = [
            # First: try to read /etc/passwd (should be denied)
            _tool_response("read_file", path="/etc/passwd"),
            # Then write a wrong answer so the trial completes
            _tool_response("write_file", path=str(expected_output), content="bad"),
            _text_response("Done."),
        ]
        rec = self._run(responses, seed=seed, task=1)
        assert rec["receipts"]["denied_violations"] >= 1
        # The read violation should be in sandbox.violations (passed through record)
        violation_ops = [v["op"] for v in rec["violations"]]
        assert "read" in violation_ops

    # ── write outside answers/ denied ────────────────────────────────────────
    def test_write_outside_answers_denied(self):
        seed = 11
        seed_dir = self._seed_dir(seed)
        answers_dir = seed_dir / "answers"
        self.trials_mod.generate(str(seed_dir), str(answers_dir), seed)
        # Try to write to seed_dir directly (not inside answers/)
        bad_path = str(seed_dir / "task1" / "sneaky.csv")
        expected_output = answers_dir / "task1_output.csv"

        responses = [
            _tool_response("write_file", path=bad_path, content="bad"),
            _tool_response("write_file", path=str(expected_output), content="bad"),
            _text_response("Done."),
        ]
        rec = self._run(responses, seed=seed, task=1)
        write_viols = [v for v in rec["violations"] if v["op"] == "write"]
        assert len(write_viols) >= 1
        assert not Path(bad_path).exists()

    # ── 9th tool call is blocked and final answer forced ─────────────────────
    def test_cap_blocks_ninth_tool_call(self):
        seed = 11
        seed_dir = self._seed_dir(seed)
        answers_dir = seed_dir / "answers"
        self.trials_mod.generate(str(seed_dir), str(answers_dir), seed)
        input_path = seed_dir / "task1" / "input.csv"

        # 8 read_file calls (each succeeds) + 1 text final answer
        responses = [
            _tool_response("read_file", path=str(input_path)),
            _tool_response("read_file", path=str(input_path)),
            _tool_response("read_file", path=str(input_path)),
            _tool_response("read_file", path=str(input_path)),
            _tool_response("read_file", path=str(input_path)),
            _tool_response("read_file", path=str(input_path)),
            _tool_response("read_file", path=str(input_path)),
            _tool_response("read_file", path=str(input_path)),  # 8th — cap hit
            _text_response("I've done my best with 8 reads. Final answer: done."),
        ]
        rec = self._run(responses, seed=seed, task=1)
        assert rec["cap_hit"] is True
        assert len(rec["tool_calls"]) == 8, (
            f"Expected exactly 8 logged tool calls, got {len(rec['tool_calls'])}"
        )
        assert "Final answer" in rec["reply"]
