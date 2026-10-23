"""Single images are on (when Brian asks). BoTTube and slideshows are off, with the code kept."""
import importlib

import pytest

import amy_persona
import imagegen
import tools


def _boom(*a, **k):
    raise AssertionError("must not run")


def test_generate_image_is_available_and_bottube_is_gone():
    names = {t["function"]["name"] for t in tools.TOOLS}
    assert "generate_image" in names
    for gone in ("create_slideshow", "bottube_post", "bottube_check"):
        assert gone not in names


@pytest.mark.parametrize("name,args", [("create_slideshow", {"prompts": ["a", "b"]}),
                                       ("bottube_post", {"path": "x.mp4", "title": "t"}),
                                       ("bottube_check", {"action": "trending"})])
def test_bottube_tools_are_refused_and_start_nothing(monkeypatch, name, args):
    import bottube
    for fn in ("create_slideshow", "generate_and_send"):
        monkeypatch.setattr(imagegen, fn, _boom)
    for fn in ("upload_video", "trending", "feed", "search", "my_profile", "check_earnings", "get_wallet"):
        monkeypatch.setattr(bottube, fn, _boom)
    assert "switched off" in tools.dispatch(name, args, None)


def test_slideshows_refuse_without_bottube():
    with pytest.raises(RuntimeError, match="Slideshows are off"):
        imagegen.create_slideshow(["a", "b"])


def test_images_can_still_be_paused(monkeypatch):
    monkeypatch.setattr(imagegen, "PAUSED", True)
    with pytest.raises(RuntimeError, match="paused"):
        imagegen.generate_image("a fox")
    monkeypatch.setattr(tools, "IMAGEGEN_PAUSED", True)
    monkeypatch.setattr(imagegen, "generate_and_send", _boom)
    assert "paused" in tools.dispatch("generate_image", {"prompt": "a fox"}, None)


def test_env_flags(monkeypatch):
    try:
        monkeypatch.setenv("AMY_BOTTUBE", "1")
        monkeypatch.setenv("AMY_IMAGEGEN_PAUSED", "1")
        m = importlib.reload(imagegen)
        assert m.SLIDESHOWS is True and m.PAUSED is True
    finally:
        monkeypatch.delenv("AMY_BOTTUBE")
        monkeypatch.delenv("AMY_IMAGEGEN_PAUSED")
        m = importlib.reload(imagegen)
        assert m.SLIDESHOWS is False and m.PAUSED is False


def test_persona_images_on_request_and_bottube_off():
    core = amy_persona.get_core_prompt()
    assert "when Brian asks for an image" in core and "never on your own" in core
    assert "BoTTube is switched off" in core
    assert "amy_t" not in core and "bottube_check" not in core
