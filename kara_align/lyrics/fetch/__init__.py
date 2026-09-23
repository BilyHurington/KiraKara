"""Lyrics lookup from music links (NetEase Cloud Music, QQ Music).

Lookups only happen when the user triggers them; failures raise
:class:`FetchError` and never affect offline processing.
"""

from __future__ import annotations

from typing import Optional, Union

from . import netease, qq
from .links import extract_urls, parse_target, resolve_link
from .safe_http import SafeClient, is_allowed_host
from .types import CollectionListing, FetchedSong, FetchError, LinkTarget, SongRef

_ADAPTERS = {"netease": netease, "qq": qq}


def fetch_song(platform: str, song_id: str, client: Optional[SafeClient] = None) -> FetchedSong:
    if platform not in _ADAPTERS:
        raise FetchError(f"Unsupported platform {platform}")
    return _ADAPTERS[platform].get_song(song_id, client)


def fetch_lyrics_from_link(text: str, client: Optional[SafeClient] = None) -> Union[FetchedSong, CollectionListing]:
    """A song link yields lyrics; album / playlist links yield a listing to pick from."""
    own = client is None
    client = client or SafeClient()
    try:
        target = resolve_link(text, client)
        adapter = _ADAPTERS[target.platform]
        if target.kind == "song":
            return adapter.get_song(target.id, client)
        return adapter.list_collection(target.kind, target.id, client)
    finally:
        if own:
            client.close()


__all__ = [
    "CollectionListing", "FetchError", "FetchedSong", "LinkTarget", "SafeClient", "SongRef",
    "extract_urls", "fetch_lyrics_from_link", "fetch_song", "is_allowed_host", "parse_target", "resolve_link",
]
