"""Result types shared by the platform adapters."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal, Optional

from ...models import SourceSnapshot

Platform = Literal["netease", "qq"]
TrackName = Literal["original", "translation", "romanization"]


class FetchError(Exception):
    """User readable failure of a lyrics lookup (never fatal for offline work)."""


@dataclass
class LinkTarget:
    platform: Platform
    kind: Literal["song", "album", "playlist"]
    id: str
    source_url: Optional[str] = None


@dataclass
class SongRef:
    platform: Platform
    song_id: str
    title: str
    artists: list[str] = field(default_factory=list)
    album: Optional[str] = None
    duration_ms: Optional[int] = None


@dataclass
class CollectionListing:
    platform: Platform
    kind: Literal["album", "playlist"]
    id: str
    title: Optional[str]
    songs: list[SongRef] = field(default_factory=list)


@dataclass
class FetchedSong:
    """Lyrics + metadata only; audio is never downloaded."""

    platform: Platform
    song_id: str
    title: Optional[str] = None
    artists: list[str] = field(default_factory=list)
    album: Optional[str] = None
    duration_ms: Optional[int] = None
    tracks: dict[str, str] = field(default_factory=dict)  # TrackName -> raw text
    has_timestamps: dict[str, bool] = field(default_factory=dict)
    url: Optional[str] = None
    notes: list[str] = field(default_factory=list)
    cover_url: Optional[str] = None  # the album cover on the platform (for a background picture)

    def to_snapshot(self, track: TrackName = "original") -> SourceSnapshot:
        """Source snapshot of one track, keeping platform provenance."""
        text = self.tracks[track]
        kind = "lrc" if self.has_timestamps.get(track) else "lyrics"
        if track != "original":
            kind = track
        return SourceSnapshot(
            origin=self.platform,
            kind=kind,
            url=self.url,
            platform_song_id=self.song_id,
            text=text,
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            fetched_meta={
                "title": self.title,
                "artists": self.artists,
                "album": self.album,
                "duration_ms": self.duration_ms,
                "track": track,
                "has_timestamps": self.has_timestamps.get(track, False),
                "cover_url": self.cover_url,
            },
        )
