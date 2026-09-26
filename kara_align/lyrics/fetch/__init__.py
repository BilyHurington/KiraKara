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


_NAMES = {"netease": "网易云音乐", "qq": "QQ 音乐"}


def _unexpected(platform: str) -> FetchError:
    return FetchError(f"{_NAMES.get(platform, platform)} 返回的数据格式无法识别（接口可能已变化），请改为粘贴歌词")


def fetch_song(platform: str, song_id: str, client: Optional[SafeClient] = None) -> FetchedSong:
    if platform not in _ADAPTERS:
        raise FetchError(f"不支持的平台 {platform}")
    if not isinstance(song_id, str) or not song_id.strip():
        raise FetchError("歌曲 ID 为空")
    try:
        return _ADAPTERS[platform].get_song(song_id.strip(), client)
    except FetchError:
        raise
    except (AttributeError, TypeError, KeyError, IndexError, ValueError) as e:
        # a platform answer of an unexpected shape is the platform's problem, not a crash here
        raise _unexpected(platform) from e


def fetch_lyrics_from_link(text: str, client: Optional[SafeClient] = None) -> Union[FetchedSong, CollectionListing]:
    """A song link yields lyrics; album / playlist links yield a listing to pick from."""
    own = client is None
    client = client or SafeClient()
    target = None
    try:
        target = resolve_link(text, client)
        adapter = _ADAPTERS[target.platform]
        if target.kind == "song":
            return adapter.get_song(target.id, client)
        return adapter.list_collection(target.kind, target.id, client)
    except FetchError:
        raise
    except (AttributeError, TypeError, KeyError, IndexError, ValueError) as e:
        raise _unexpected(target.platform if target else "") from e
    finally:
        if own:
            client.close()


__all__ = [
    "CollectionListing", "FetchError", "FetchedSong", "LinkTarget", "SafeClient", "SongRef",
    "extract_urls", "fetch_lyrics_from_link", "fetch_song", "is_allowed_host", "parse_target", "resolve_link",
]
