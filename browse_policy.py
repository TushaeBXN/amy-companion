"""What Amy's browser is allowed to touch. Pure functions, so the rules can be tested without a browser.

The rules: only http(s); never a private, loopback or link-local address (her own Ollama and
dashboard live on localhost, and a web page must not be able to reach them); nothing but
read-only requests (no form posts, no logins, no purchases); no images, video or fonts."""
import ipaddress
import socket
from urllib.parse import urlparse

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
SKIPPED_RESOURCES = {"image", "media", "font"}   # saves memory and bandwidth; she reads text


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%")[0])
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


def check_url(url: str, resolve=socket.getaddrinfo, allow_private: bool = False):
    """(ok, reason). Resolves the host itself, so decimal/hex IP tricks and hostnames that point
    at a private address are caught too."""
    try:
        parts = urlparse(url)
        host = parts.hostname
    except ValueError:
        return False, "not a valid URL"
    if parts.scheme not in ("http", "https"):
        return False, f"only http and https pages are allowed, not '{parts.scheme or 'none'}'"
    if not host:
        return False, "no host in the URL"
    if allow_private:
        return True, ""
    try:
        addresses = {info[4][0] for info in resolve(host, None)}
    except OSError:
        return False, f"can't resolve '{host}'"
    if not addresses:
        return False, f"can't resolve '{host}'"
    bad = [a for a in addresses if not _is_public(a)]
    if bad:
        return False, f"'{host}' points at a private or local address ({bad[0]}); not allowed"
    return True, ""


def block_reason(method: str, url: str, resource_type: str, resolve=socket.getaddrinfo,
                 allow_private: bool = False):
    """Why a browser request must be blocked, or None to let it through."""
    if method.upper() not in SAFE_METHODS:
        return f"{method.upper()} is blocked: read-only browsing, no forms or logins"
    if resource_type in SKIPPED_RESOURCES:
        return f"{resource_type} skipped"
    ok, reason = check_url(url, resolve, allow_private)
    return None if ok else reason
