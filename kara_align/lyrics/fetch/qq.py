"""QQ Music adapter (lyrics + metadata only)."""

from __future__ import annotations

import base64
import binascii
import html
import json
import re
from typing import Any, Optional

from ..lrc import has_valid_times, parse_lrc
from .safe_http import SafeClient
from .types import CollectionListing, FetchedSong, FetchError, SongRef

HEADERS = {"Referer": "https://y.qq.com/"}
MUSICU = "https://u.y.qq.com/cgi-bin/musicu.fcg"
LYRIC = "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"
PLAYLIST = "https://c.y.qq.com/qzone/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg"


def _musicu(client: SafeClient, module: str, method: str, param: dict) -> dict:
    payload = {"comm": {"ct": 24, "cv": 0}, "req": {"module": module, "method": method, "param": param}}
    data = client.post(MUSICU, content=json.dumps(payload), headers={**HEADERS, "Content-Type": "application/json"}).json()
    req = (data or {}).get("req") or {}
    if req.get("code", 0) != 0:
        raise FetchError(f"QQ 音乐：{method} 失败（code {req.get('code')}）")
    return req.get("data") or {}


def _ref(info: dict) -> SongRef:
    singers = [s.get("name", "") for s in info.get("singer") or []]
    album = (info.get("album") or {}).get("name") or info.get("albumname")
    interval = info.get("interval")
    return SongRef("qq", info.get("mid") or info.get("songmid") or str(info.get("id") or info.get("songid")),
                   info.get("title") or info.get("name") or info.get("songname", ""), singers, album,
                   int(interval) * 1000 if interval else None)


def _decode_lyric(value: Any) -> str:
    if not value:
        return ""
    text = str(value)
    if "[" not in text and re.fullmatch(r"[A-Za-z0-9+/=\s]+", text):
        try:
            text = base64.b64decode(text).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            pass
    return html.unescape(text).strip()


def get_song(song_id: str, client: Optional[SafeClient] = None) -> FetchedSong:
    """``song_id`` may be a songmid or a numeric songid."""
    own = client is None
    client = client or SafeClient()
    try:
        param: dict[str, Any] = {"song_type": 0}
        if song_id.isdigit():
            param.update(song_mid="", song_id=int(song_id))
        else:
            param.update(song_mid=song_id, song_id=0)
        data = _musicu(client, "music.pf_song_detail_svr", "get_song_detail_yqq", param)
        info = data.get("track_info") or {}
        if not info:
            raise FetchError(f"QQ 音乐：找不到歌曲 {song_id}")
        ref = _ref(info)
        lyr = client.get(LYRIC, params={"songmid": ref.song_id, "format": "json", "nobase64": 1, "g_tk": 5381},
                         headers=HEADERS).json()
    finally:
        if own:
            client.close()
    if not isinstance(lyr, dict) or lyr.get("retcode", lyr.get("code", 0)) not in (0, None):
        raise FetchError("QQ 音乐：歌词获取失败")
    mid = (info.get("album") or {}).get("mid") or info.get("albummid")
    song = FetchedSong("qq", ref.song_id, ref.title, ref.artists, ref.album, ref.duration_ms,
                       url=f"https://y.qq.com/n/ryqq/songDetail/{ref.song_id}",
                       cover_url=f"https://y.gtimg.cn/music/photo_new/T002R800x800M000{mid}.jpg"
                       if isinstance(mid, str) and re.fullmatch(r"[A-Za-z0-9]+", mid) else None)
    for key, name in (("lyric", "original"), ("trans", "translation")):
        text = _decode_lyric(lyr.get(key))
        if text:
            song.tracks[name] = text
            song.has_timestamps[name] = has_valid_times(parse_lrc(text))
    return song


def list_collection(kind: str, cid: str, client: Optional[SafeClient] = None) -> CollectionListing:
    own = client is None
    client = client or SafeClient()
    try:
        if kind == "album":
            data = _musicu(client, "music.musichallAlbum.AlbumSongList", "GetAlbumSongList",
                           {"albumMid": cid, "begin": 0, "num": 500, "order": 2})
            songs = [_ref(item.get("songInfo") or {}) for item in data.get("songList") or []]
            title = songs[0].album if songs else None
        elif kind == "playlist":
            data = client.get(PLAYLIST, params={"type": 1, "json": 1, "utf8": 1, "onlysong": 0,
                                                "disstid": cid, "format": "json"}, headers=HEADERS).json()
            cd = ((data or {}).get("cdlist") or [{}])[0]
            title = cd.get("dissname")
            songs = [_ref(s) for s in cd.get("songlist") or []]
        else:
            raise FetchError(f"不支持的合集类型 {kind}")
    finally:
        if own:
            client.close()
    return CollectionListing("qq", kind, cid, title, songs)  # type: ignore[arg-type]
