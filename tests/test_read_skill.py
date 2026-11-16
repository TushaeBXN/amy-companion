import os
import tools


def _read(name):
    return tools.dispatch("read_skill", {"skill_name": name})


def test_every_vendored_skill_is_readable():
    root = os.path.join(os.path.dirname(tools.__file__), "amy_skills")
    names = [d for d in os.listdir(root) if os.path.isfile(os.path.join(root, d, "SKILL.md"))]
    assert len(names) == 55
    for n in names:
        assert _read(n).startswith("---"), n


def test_reference_file_and_traversal():
    assert "bb" in _read("bb-cli/references/plugins.md").lower()
    out = _read("../../etc/passwd.md")
    assert "not found" in out


def test_missing_lists_available():
    out = _read("nope")
    assert "not found" in out and "handoff" in out
