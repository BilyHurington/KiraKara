"""Music link parsing: URLs, share texts, short links and explicit ids."""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qs, urlsplit

from .safe_http import SafeClient
from .types import FetchError, LinkTarget

_URL = re.compile(r"https?://[^\s，。！？、）)\"'<>【】「」]+", re.I)
_EXPLICIT = re.compile(
    r"^\s*(netease|ncm|163|wyy|qq|qqmusic)\s*[:：]\s*(?:(song|album|playlist)\s*[:：]\s*)?([A-Za-z0-9]+)\s*$",
    re.I,
)
# verified short-link hosts; everything they redirect to is re-validated
SHORT_HOSTS = frozenset({"163cn.tv", "c6.y.qq.com"})


def extract_urls(text: str) -> list[str]:
    return [m.group(0).rstrip(".,;") for m in _URL.finditer(text)]


def _netease(url: str) -> Optional[LinkTarget]:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host.endswith("music.163.com"):
        return None
    path, query = parts.path, parts.query
    # SPA form: https://music.163.com/#/song?id=123
    if parts.fragment.startswith("/"):
        frag = urlsplit(parts.fragment)
        path, query = frag.path, frag.query or query
    qs = parse_qs(query)
    m = re.search(r"/(song|album|playlist)(?:/(\d+))?/?$", path)
    if not m:
        m = re.search(r"/(song|album|playlist)(?:/(\d+))?", path)
    if not m:
        return None
    kind = m.group(1)
    sid = m.group(2) or (qs.get("id") or [None])[0]
    if not sid or not sid.isdigit():
        return None
    return LinkTarget("netease", kind, sid, url)  # type: ignore[arg-type]


def _qq(url: str) -> Optional[LinkTarget]:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host.endswith("y.qq.com"):
        return None
    path, qs = parts.path, parse_qs(parts.query)
    m = re.search(r"/songDetail/([A-Za-z0-9]+)", path) or re.search(r"/song/([A-Za-z0-9]+)\.html", path)
    if m:
        return LinkTarget("qq", "song", m.group(1), url)
    m = re.search(r"/albumDetail/([A-Za-z0-9]+)", path) or re.search(r"/album/([A-Za-z0-9]+)\.html", path)
    if m:
        return LinkTarget("qq", "album", m.group(1), url)
    m = re.search(r"/playlist/(\d+)", path)
    if m:
        return LinkTarget("qq", "playlist", m.group(1), url)
    for key in ("songmid", "songid", "mid"):
        if qs.get(key) and "album" not in path and "taoge" not in path:
            return LinkTarget("qq", "song", qs[key][0], url)
    if "album" in path and (qs.get("albummid") or qs.get("albumMid") or qs.get("id")):
        return LinkTarget("qq", "album", (qs.get("albummid") or qs.get("albumMid") or qs.get("id"))[0], url)
    if ("taoge" in path or "playlist" in path) and qs.get("id"):
        return LinkTarget("qq", "playlist", qs["id"][0], url)
    return None


def parse_url(url: str) -> Optional[LinkTarget]:
    """Offline recognition of a full platform URL (no network)."""
    return _netease(url) or _qq(url)


def parse_target(text: str) -> Optional[LinkTarget]:
    """Recognize explicit ids and full URLs without any network access."""
    m = _EXPLICIT.match(text)
    if m:
        plat = "qq" if m.group(1).lower().startswith("qq") else "netease"
        kind = (m.group(2) or "song").lower()
        sid = m.group(3)
        if plat == "netease" and not sid.isdigit():
            raise FetchError("NetEase ids are numeric")
        return LinkTarget(plat, kind, sid)  # type: ignore[arg-type]
    for url in extract_urls(text):
        target = parse_url(url)
        if target:
            return target
    return None


def resolve_link(text: str, client: Optional[SafeClient] = None) -> LinkTarget:
    """Resolve pasted text (URL, share text, short link, ``netease:ID``) to a target."""
    text = text.strip()
    if not text:
        raise FetchError("Empty link")
    if text.isdigit():
        raise FetchError("A bare song id is ambiguous; write it as netease:ID or qq:ID")
    target = parse_target(text)
    if target:
        return target
    urls = extract_urls(text)
    shorts = [u for u in urls if (urlsplit(u).hostname or "").lower() in SHORT_HOSTS]
    if not shorts:
        if urls:
            raise FetchError("Unsupported link; only NetEase Cloud Music and QQ Music song/album/playlist links work")
        raise FetchError("No music link found in the text")
    own = client is None
    client = client or SafeClient()
    try:
        final = client.follow_redirects(shorts[0], stop=lambda u: parse_url(u) is not None)
    finally:
        if own:
            client.close()
    target = parse_url(final)
    if not target:
        raise FetchError("Short link did not lead to a supported song, album or playlist page")
    target.source_url = shorts[0]
    return target
