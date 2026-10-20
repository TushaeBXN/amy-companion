"""Amy's code review (self_check.read_codebase) must not crash on empty docstrings."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import self_check as sc  # noqa: E402


def test_first_line_handles_empty_blank_and_normal_text():
    assert sc._first_line("", 80) == "" and sc._first_line(None, 80) == ""
    assert sc._first_line("\n\n   \n", 80) == ""
    assert sc._first_line("\n  Summary line.\nMore.", 80) == "Summary line."
    assert sc._first_line("x" * 200, 10) == "x" * 10


def test_read_codebase_runs_on_a_project_with_empty_docstrings(tmp_path, monkeypatch):
    (tmp_path / "empty_doc.py").write_text('""" """\n\ndef public():\n    ""\n    return 1\n\nclass Thing:\n    """\n\n    """\n')
    (tmp_path / "plain.py").write_text('"""A module."""\n\ndef ok():\n    """Does a thing."""\n')
    for name in ("HERE", "ROOT", "BASE", "PROJECT_DIR"):
        if hasattr(sc, name):
            monkeypatch.setattr(sc, name, str(tmp_path))
    out = sc.read_codebase()
    assert isinstance(out, str) and out


def test_read_codebase_on_the_real_project_no_longer_raises():
    out = sc.read_codebase()
    assert "telegram_amy.py" in out
