"""Image generation (FLUX) is paused: hidden from her tools, refused if called, guarded in imagegen."""
import importlib

import pytest

import imagegen
import tools


def test_both_image_tools_are_hidden():
    names = {t["function"]["name"] for t in tools.TOOLS}
    assert "generate_image" not in names and "create_slideshow" not in names
    assert "bottube_check" in names           # looking around BoTTube is still allowed


@pytest.mark.parametrize("name,args", [("generate_image", {"prompt": "a fox"}),
                                       ("create_slideshow", {"prompts": ["a", "b"]})])
def test_calling_them_anyway_is_refused_and_starts_nothing(monkeypatch, name, args):
    def boom(*a, **k):
        raise AssertionError("FLUX must not start while paused")
    monkeypatch.setattr(imagegen, "generate_image", boom)
    monkeypatch.setattr(imagegen, "create_slideshow", boom)
    monkeypatch.setattr(imagegen, "generate_and_send", boom)
    reply = tools.dispatch(name, args, None)
    assert "paused" in reply and "don't pretend" in reply


def test_imagegen_itself_refuses_when_paused():
    with pytest.raises(RuntimeError, match="paused"):
        imagegen.generate_image("a fox")
    with pytest.raises(RuntimeError, match="paused"):
        imagegen.create_slideshow(["a", "b"])


def test_env_flag_resumes_it(monkeypatch):
    monkeypatch.setenv("AMY_IMAGEGEN_PAUSED", "0")
    try:
        assert importlib.reload(imagegen).PAUSED is False
    finally:
        monkeypatch.delenv("AMY_IMAGEGEN_PAUSED")
        assert importlib.reload(imagegen).PAUSED is True


def test_persona_tells_her_the_truth_about_images():
    import amy_persona
    assert "Image generation is paused" in amy_persona.get_core_prompt()
