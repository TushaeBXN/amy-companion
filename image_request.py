"""Did this message ask for an image? Amy only makes one when it did."""
import re

_ASK = re.compile(r"(?i)\b(images?|pictures?|pics?|photos?|drawings?|illustrations?|artworks?|"
                  r"wallpapers?|logos?|portraits?|renders?|flux|draw|paint|sketch|illustrate)\b")


def asked_for_image(text: str) -> bool:
    return bool(_ASK.search(text or ""))
