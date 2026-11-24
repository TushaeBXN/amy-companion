"""Her unprompted thoughts start from something she read and rotate through
the areas, instead of circling back to the most vivid line in her persona."""

import random

import pytest

import world


@pytest.fixture
def reading(tmp_path, monkeypatch):
    monkeypatch.setattr(world, "WORLD_DIR", str(tmp_path))
    monkeypatch.setattr(world, "THOUGHTS", str(tmp_path / ".thought_rotation.json"))
    for area in world.AREAS:
        world._write_area(area["name"], area["label"], [
            {"title": f"{area['name']} story {n}", "url": f"https://example.org/{area['name']}/{n}"}
            for n in range(3)
        ])
    return tmp_path


def test_she_reads_more_than_tech():
    names = {a["name"] for a in world.AREAS}
    assert {"science", "health", "environment", "space", "elections"} <= names
    assert {"tech", "ai", "news", "startups", "geopolitics"} <= names


def test_a_run_of_thoughts_covers_many_areas_and_never_reuses_a_story(reading):
    rng = random.Random(7)
    seeds = [world.thought_seed(rng) for _ in range(10)]
    assert all(seeds)
    assert len({s["area"] for s in seeds}) >= 8           # rotation, not one subject
    assert len({s["title"] for s in seeds}) == 10         # no story used twice
    for a, b in zip(seeds, seeds[1:]):
        assert a["area"] != b["area"]                     # never the same area back to back


def test_when_every_story_is_used_there_is_no_seed(reading):
    rng = random.Random(1)
    total = 3 * len(world.AREAS)
    for _ in range(total):
        assert world.thought_seed(rng)
    assert world.thought_seed(rng) is None


def test_no_reading_yet_gives_no_seed(tmp_path, monkeypatch):
    monkeypatch.setattr(world, "WORLD_DIR", str(tmp_path))
    monkeypatch.setattr(world, "THOUGHTS", str(tmp_path / ".thought_rotation.json"))
    assert world.thought_seed() is None
    assert world.thought_context(None) == ""


def test_the_prompt_shows_the_story_and_her_last_thoughts(reading):
    for n in range(7):
        world.note_thought(f"thought number {n}")
    seed = world.thought_seed(random.Random(3))
    text = world.thought_context(seed)
    assert seed["title"] in text and seed["area"] in text
    assert "this one should be about something else" in text
    assert "thought number 6" in text and "thought number 1" not in text   # only the last 5
    assert "own model, weights" in text


def test_her_thought_prompts_use_the_rotation():
    import inspect
    import worker
    assert "_thought_rotation()" in inspect.getsource(worker._spontaneous_thought)
    assert "_note_thought(text)" in inspect.getsource(worker._spontaneous_thought)
    src = inspect.getsource(worker._run_task)
    assert "THOUGHT_TASKS" in src and "_thought_rotation()" in src and "_note_thought(final)" in src
    assert "evening_thought" in worker.THOUGHT_TASKS
