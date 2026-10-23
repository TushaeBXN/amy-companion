"""Offline checks: every self_edit outcome is recorded, and wrong scores can be retracted."""
import json
from datetime import datetime

import self_check
import self_metrics


def _point_metrics_at(tmp_path, monkeypatch):
    monkeypatch.setattr(self_metrics, "METRICS_DIR", tmp_path)
    monkeypatch.setattr(self_metrics, "SCORES_FILE", tmp_path / "scores.json")
    monkeypatch.setattr(self_metrics, "SOFT_SPOTS_FILE", tmp_path / "soft_spots.md")


def _log_lines(tmp_path):
    return [json.loads(l) for l in (tmp_path / "self_edits.jsonl").read_text().splitlines()]


def test_missing_file_edit_is_recorded(tmp_path, monkeypatch):
    _point_metrics_at(tmp_path, monkeypatch)
    result = self_check.self_edit("no_such_file_xyz.py", "a", "b", "testing the log")
    assert result["status"] == "error"
    (entry,) = _log_lines(tmp_path)
    assert entry["file"] == "no_such_file_xyz.py"
    assert entry["status"] == "error"
    assert entry["reason"] == "testing the log"


def test_text_not_found_is_recorded(tmp_path, monkeypatch):
    _point_metrics_at(tmp_path, monkeypatch)
    result = self_check.self_edit("amy_core_rules.md", "THIS TEXT IS NOT IN THE FILE 9f3a", "x", "nothing should change")
    assert result["status"] == "error"
    assert _log_lines(tmp_path)[-1]["status"] == "error"


def test_protected_file_refusal_is_recorded(tmp_path, monkeypatch):
    _point_metrics_at(tmp_path, monkeypatch)
    result = self_check.self_edit("self_check.py", "a", "b", "try to edit the guard")
    assert result["status"] == "approval_required"
    assert _log_lines(tmp_path)[-1]["status"] == "approval_required"


def test_logging_failure_never_breaks_an_edit(tmp_path, monkeypatch):
    _point_metrics_at(tmp_path, monkeypatch)
    monkeypatch.setattr(self_metrics, "METRICS_DIR", tmp_path / "file_not_dir" / "x")
    (tmp_path / "file_not_dir").write_text("blocks mkdir")
    assert self_check.self_edit("no_such_file_xyz.py", "a", "b", "r")["status"] == "error"


def test_retracted_score_is_ignored_by_soft_spots(tmp_path, monkeypatch):
    _point_metrics_at(tmp_path, monkeypatch)
    now = datetime.now().isoformat()
    (tmp_path / "scores.json").write_text(json.dumps({"entries": [
        {"date": now, "model": "Amy", "dimension": "income_progress", "score": 2, "note": "wrong premise"},
        {"date": now, "model": "Amy", "dimension": "task_completion", "score": 5, "note": "ok"},
    ]}))
    assert "income_progress" in self_metrics.get_soft_spots()
    assert json.loads(self_metrics.retract_score(now[:10], "income_progress", "false premise"))["retracted"] == 1
    out = self_metrics.get_soft_spots()
    assert "income_progress" not in out and "task_completion" in out
    # kept on file, not deleted
    saved = json.loads((tmp_path / "scores.json").read_text())["entries"]
    assert saved[0]["superseded"] == "false premise"
