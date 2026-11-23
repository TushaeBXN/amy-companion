import os

import pytest

import world as W


def _item(n):
    return {"title": f"Story {n}", "url": f"https://example.com/{n}"}


@pytest.fixture
def observer(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "WORLD_DIR", str(tmp_path))
    monkeypatch.setattr(W, "MANIFEST", str(tmp_path / ".world_manifest.json"))
    monkeypatch.setattr(W, "MAX_AGE", 0)
    feed = {"a": [], "b": []}
    monkeypatch.setattr(W, "AREAS", [
        {"name": "a", "label": "Area A", "fetch": lambda: list(feed["a"])},
        {"name": "b", "label": "Area B", "fetch": lambda: list(feed["b"])},
    ])
    reflected = []
    monkeypatch.setattr(W, "_reflect", lambda manifest, headlines: reflected.append(list(headlines)))
    return W.WorldObserver(), feed, reflected, tmp_path


def test_the_same_headlines_are_not_logged_or_reflected_on_again(observer):
    obs, feed, reflected, d = observer
    feed["a"] = [_item(1), _item(2), _item(3)]
    assert obs.tick()[0] == 1 and len(reflected) == 1
    before = (d / "a.md").read_text()
    stamp = os.stat(d / "a.md").st_mtime_ns

    assert obs.tick()[0] == 0
    assert (d / "a.md").read_text() == before and os.stat(d / "a.md").st_mtime_ns == stamp
    assert len(reflected) == 1


def test_only_the_new_story_is_added_and_the_old_ones_are_not_repeated(observer):
    obs, feed, reflected, d = observer
    feed["a"] = [_item(1), _item(2)]
    obs.tick()
    feed["a"] = [_item(3), _item(1), _item(2)]
    obs.tick()
    text = (d / "a.md").read_text()
    assert text.count("Story 1") == 1 and text.count("Story 2") == 1 and text.count("Story 3") == 1
    assert text.index("Story 3") < text.index("Story 1")
    assert reflected[-1] == ["[Area A] Story 3"]


def test_a_story_already_logged_in_one_area_is_not_logged_in_another(observer):
    obs, feed, reflected, d = observer
    feed["a"] = [_item(1), _item(2)]
    feed["b"] = [_item(2), _item(9)]
    obs.tick()
    assert "Story 2" in (d / "a.md").read_text()
    b = (d / "b.md").read_text()
    assert "Story 9" in b and "Story 2" not in b


def test_an_area_with_only_repeats_gets_no_file(observer):
    obs, feed, reflected, d = observer
    feed["a"] = [_item(1)]
    feed["b"] = [_item(1)]
    obs.tick()
    assert (d / "a.md").exists() and not (d / "b.md").exists()


def test_the_file_keeps_only_the_newest_items(observer, monkeypatch):
    obs, feed, reflected, d = observer
    monkeypatch.setattr(W, "MAX_KEEP", 3)
    for n in range(1, 6):
        feed["a"] = [_item(n)]
        obs.tick()
    text = (d / "a.md").read_text()
    assert [f"Story {n}" in text for n in range(1, 6)] == [False, False, True, True, True]
