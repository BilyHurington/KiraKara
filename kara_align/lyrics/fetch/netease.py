"""NetEase Cloud Music adapter (lyrics + metadata only)."""

from __future__ import annotations

import json
from typing import Any, Optional

from ..lrc import format_lrc_tag, has_valid_times, parse_lrc
from .safe_http import SafeClient
from .types import CollectionListing, FetchedSong, FetchError, SongRef

BASE = "https://music.163.com"
HEADERS = {"Referer": "https://music.163.com/"}


def _check(data: Any, what: str) -> dict:
    if not isinstance(data, dict):
        raise FetchError(f"网易云：{what} 返回了无法识别的数据")
    code = data.get("code", 200)
    if code != 200:
        raise FetchError(f"网易云：{what} 失败（code {code}：{data.get('message') or data.get('msg') or ''}）")
    return data


def _song_ref(s: dict) -> SongRef:
    artists = [a.get("name", "") for a in (s.get("ar") or s.get("artists") or [])]
    album = (s.get("al") or s.get("album") or {}).get("name")
    return SongRef("netease", str(s.get("id")), s.get("name", ""), artists, album, s.get("dt") or s.get("duration"))


def normalize_netease_lrc(text: str) -> str:
    """Convert NetEase JSON credit lines (``{"t":0,"c":[{"tx":..}]}``) to LRC lines.

    The time ``t`` comes from the platform; nothing is invented.
    """
    out = []
    for line in text.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                obj = json.loads(s)
                body = "".join(c.get("tx", "") for c in obj.get("c", []))
                out.append(f"{format_lrc_tag(int(obj['t']), 'ms')}{body}" if "t" in obj else body)
                continue
            except (ValueError, KeyError, TypeError, AttributeError):
                pass
        out.append(line)
    return "\n".join(out)


def _detail(client: SafeClient, ids: list[str]) -> list[dict]:
    data = _check(client.get(f"{BASE}/api/song/detail/", params={"ids": json.dumps([int(i) for i in ids])},
                             headers=HEADERS).json(), "song detail")
    return data.get("songs") or []


def get_song(song_id: str, client: Optional[SafeClient] = None) -> FetchedSong:
    if not str(song_id).isdigit():
        raise FetchError("网易云歌曲 ID 必须是数字")
    own = client is None
    client = client or SafeClient()
    try:
        songs = _detail(client, [song_id])
        if not songs:
            raise FetchError(f"网易云：找不到歌曲 {song_id}")
        ref = _song_ref(songs[0])
        lyr = _check(client.get(f"{BASE}/api/song/lyric",
                                params={"id": song_id, "lv": -1, "tv": -1, "rv": -1},
                                headers=HEADERS).json(), "lyric")
    finally:
        if own:
            client.close()
    song = FetchedSong("netease", str(song_id), ref.title, ref.artists, ref.album, ref.duration_ms,
                       url=f"{BASE}/song?id={song_id}")
    for key, name in (("lrc", "original"), ("tlyric", "translation"), ("romalrc", "romanization")):
        text = ((lyr.get(key) or {}).get("lyric") or "").strip()
        if text:
            text = normalize_netease_lrc(text)
            song.tracks[name] = text
            song.has_timestamps[name] = has_valid_times(parse_lrc(text))
    if lyr.get("nolyric"):
        song.notes.append("平台标记为纯音乐 / 无歌词。")
    if lyr.get("uncollected"):
        song.notes.append("平台尚未收录这首歌的歌词。")
    return song


def list_collection(kind: str, cid: str, client: Optional[SafeClient] = None) -> CollectionListing:
    own = client is None
    client = client or SafeClient()
    try:
        if kind == "album":
            data = _check(client.get(f"{BASE}/api/v1/album/{cid}", headers=HEADERS).json(), "album")
            title = (data.get("album") or {}).get("name")
            songs = [_song_ref(s) for s in data.get("songs") or []]
        elif kind == "playlist":
            data = _check(client.get(f"{BASE}/api/v6/playlist/detail", params={"id": cid, "n": 1000},
                                     headers=HEADERS).json(), "playlist")
            pl = data.get("playlist") or {}
            title = pl.get("name")
            songs = [_song_ref(s) for s in pl.get("tracks") or []]
            known = {s.song_id for s in songs}
            missing = [str(t["id"]) for t in pl.get("trackIds") or [] if str(t.get("id")) not in known][:1000]
            for i in range(0, len(missing), 200):
                songs += [_song_ref(s) for s in _detail(client, missing[i:i + 200])]
        else:
            raise FetchError(f"不支持的合集类型 {kind}")
    finally:
        if own:
            client.close()
    return CollectionListing("netease", kind, cid, title, songs)  # type: ignore[arg-type]
