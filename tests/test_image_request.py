"""She makes an image only when the message asks for one."""
import pytest

from image_request import asked_for_image


@pytest.mark.parametrize("text", [
    "make me a picture of a fox", "can you draw a cat?", "generate an image of a rainy window",
    "I want a logo for my project", "render a portrait of a lighthouse", "Send me a PHOTO of the sea",
    "use flux to paint a sunset", "sketch something",
])
def test_requests_for_images_pass(text):
    assert asked_for_image(text)


@pytest.mark.parametrize("text", [
    "how are you today?", "what's the weather", "remind me about the dentist", "summarize this article",
    "", None,
])
def test_everything_else_does_not(text):
    assert not asked_for_image(text)
