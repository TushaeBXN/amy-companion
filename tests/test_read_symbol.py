"""read_symbol: pull one function/class's exact source without loading the whole file it's in."""
import self_check as sc
import tools


SRC_A = '''"""Module A."""
import os


def helper(x):
    """Does a thing."""
    return x + 1


class Thing:
    """A thing."""

    def method_one(self):
        return 1

    def _private(self):
        return 2


@staticmethod
def decorated():
    return 3
'''

SRC_B = '''def helper(x):
    return x * 2
'''

BROKEN = "def oops(:\n    pass\n"


def test_finds_a_top_level_function_with_exact_lines(tmp_path, monkeypatch):
    f = tmp_path / "a.py"
    f.write_text(SRC_A)
    monkeypatch.setattr(sc, "HERE", str(tmp_path))
    out = sc.read_symbol("helper", file="a.py")
    assert out.startswith("a.py:5-7") and "return x + 1" in out and "import os" not in out and "class Thing" not in out


def test_finds_a_class_and_a_class_method(tmp_path, monkeypatch):
    f = tmp_path / "a.py"
    f.write_text(SRC_A)
    monkeypatch.setattr(sc, "HERE", str(tmp_path))
    whole = sc.read_symbol("Thing", file="a.py")
    assert "class Thing" in whole and "method_one" in whole and "_private" in whole
    one = sc.read_symbol("Thing.method_one", file="a.py")
    assert "def method_one" in one and "class Thing" not in one and "_private" not in one


def test_a_decorated_function_includes_its_decorator(tmp_path, monkeypatch):
    f = tmp_path / "a.py"
    f.write_text(SRC_A)
    monkeypatch.setattr(sc, "HERE", str(tmp_path))
    out = sc.read_symbol("decorated", file="a.py")
    assert "@staticmethod" in out and "return 3" in out


def test_searches_every_file_when_no_filename_given_and_disambiguates(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text(SRC_A)
    (tmp_path / "b.py").write_text(SRC_B)
    monkeypatch.setattr(sc, "HERE", str(tmp_path))
    out = sc.read_symbol("helper")
    assert "more than one file" in out and "a.py:5" in out and "b.py:1" in out
    assert "return x + 1" not in out  # ambiguous: no source shown, just the choice


def test_unique_name_is_returned_directly_without_a_filename(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text(SRC_A)
    (tmp_path / "b.py").write_text(SRC_B)
    monkeypatch.setattr(sc, "HERE", str(tmp_path))
    out = sc.read_symbol("Thing")
    assert out.startswith("a.py:") and "method_one" in out


def test_not_found_says_so_plainly(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text(SRC_A)
    monkeypatch.setattr(sc, "HERE", str(tmp_path))
    out = sc.read_symbol("nothing_here")
    assert "isn't defined" in out and "read_codebase" in out
    out2 = sc.read_symbol("helper", file="nope.py")
    assert "isn't defined" in out2 and "nope.py" in out2


def test_a_file_with_a_syntax_error_is_skipped_not_fatal(tmp_path, monkeypatch):
    (tmp_path / "broken.py").write_text(BROKEN)
    (tmp_path / "a.py").write_text(SRC_A)
    monkeypatch.setattr(sc, "HERE", str(tmp_path))
    out = sc.read_symbol("helper", file="a.py")
    assert "return x + 1" in out  # didn't blow up on the broken file


def test_an_empty_name_asks_for_one():
    assert "give a function or class name" in sc.read_symbol("")


def test_the_tool_is_wired_and_dispatches(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text(SRC_A)
    monkeypatch.setattr(sc, "HERE", str(tmp_path))
    schema = next(t for t in tools.TOOLS if t["function"]["name"] == "self_improve")
    assert "read_symbol" in schema["function"]["parameters"]["properties"]["action"]["enum"]
    out = tools.dispatch("self_improve", {"action": "read_symbol", "name": "helper", "filename": "a.py"}, None)
    assert "return x + 1" in out
