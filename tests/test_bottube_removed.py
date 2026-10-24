"""BoTTube and Kellybot were removed completely (2026-10-24), not just paused. Nothing should name them anywhere
Amy actually sees or runs: tools, the allowed-tool list, her persona, the spontaneous-thought prompts, imagegen."""
import os
import re
from pathlib import Path

import amy_persona
import imagegen
import thoughts
import tools
import worker

ROOT = Path(__file__).resolve().parent.parent
_HIT = re.compile(r"(?i)bottube|kellybot")


def test_no_bottube_tools_or_env_flag():
    names = {t["function"]["name"] for t in tools.TOOLS}
    for gone in ("create_slideshow", "bottube_post", "bottube_check"):
        assert gone not in names
    assert not hasattr(tools, "BOTTUBE_ENABLED") and not hasattr(tools, "_BOTTUBE_TOOLS")
    assert tools.dispatch("bottube_post", {"path": "x", "title": "t"}, None) is None   # not a registered tool any more


def test_slideshows_are_gone_from_imagegen():
    assert not hasattr(imagegen, "create_slideshow") and not hasattr(imagegen, "SLIDESHOWS")


def test_not_in_her_allowed_tools_or_persona_or_thoughts():
    assert not _HIT.search(" ".join(worker.ALLOWED_TOOLS))
    assert not _HIT.search(amy_persona.SYSTEM_PROMPT)
    assert not any(_HIT.search(t) for t in thoughts._THOUGHT_PROMPTS if isinstance(t, str))


def test_not_left_behind_in_the_source_tree():
    """Nobody can bring it back by accident: no file is still named bottube, and no source file mentions it."""
    assert not (ROOT / "bottube.py").exists()
    hits = []
    for p in ROOT.rglob("*.py"):
        # tests/ is allowed to document that the feature was removed (e.g. "BoTTube is gone" in a
        # docstring, or a fictitious sample case string); the feature itself must be gone from real code.
        if "venv" in p.parts or ".git" in p.parts or "tests" in p.parts:
            continue
        if _HIT.search(p.read_text(errors="ignore")):
            hits.append(str(p.relative_to(ROOT)))
    assert hits == []
