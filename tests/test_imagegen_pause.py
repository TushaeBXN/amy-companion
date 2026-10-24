"""Single images are on (when Brian asks). BoTTube/slideshows were removed entirely (2026-10-24)."""
import pytest

import imagegen
import tools


def test_images_can_still_be_paused(monkeypatch):
    monkeypatch.setattr(imagegen, "PAUSED", True)
    with pytest.raises(RuntimeError, match="paused"):
        imagegen.generate_image("a fox")
    monkeypatch.setattr(tools, "IMAGEGEN_PAUSED", True)
    monkeypatch.setattr(imagegen, "generate_and_send", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    assert "paused" in tools.dispatch("generate_image", {"prompt": "a fox"}, None)


def test_persona_images_on_request():
    import amy_persona
    core = amy_persona.get_core_prompt()
    assert "when Brian asks for an image" in core and "never on your own" in core
