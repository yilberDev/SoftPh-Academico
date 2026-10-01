from urllib.parse import urlsplit


def _origin_of(url: str) -> str | None:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def origin_allowed(origin: str | None, referer: str | None, allowed_origin: str) -> bool:
    """Origin (or Referer when Origin is missing) must be the single public origin.

    Never derived from the Host header, which the proxy may rewrite.
    """
    allowed = allowed_origin.rstrip("/")
    if origin:
        return origin == allowed
    if referer:
        return _origin_of(referer) == allowed
    return False
