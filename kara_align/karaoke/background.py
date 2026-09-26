"""Background pictures for karaoke videos made from audio.

A project whose original is an audio file (or one that should not show its own
video) can have a background: a still picture, or a video that loops for the
whole song.  Burning then plays the audio over that picture, which gives the
same video as first making "audio + picture" into a video and then adding the
subtitles.

The frame keeps the background's aspect ratio with its longer side at
``LONG_SIDE`` px (a 4000×3000 photo gives 1920×1440, a phone video 1080×1920);
the background is scaled to cover it.  Subtitles are laid out for that frame.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

from ..audio.io import AudioError, sniff_audio_format

LONG_SIDE = 1920
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".flv", ".ts", ".mts", ".m2ts", ".wmv",
                    ".mpg", ".mpeg", ".3gp", ".gif"}
MAX_BACKGROUND_BYTES = 4 * 1024 ** 3

Kind = Literal["image", "video"]


class BackgroundError(AudioError):
    pass


def _image_magic(head: bytes) -> bool:
    return (head.startswith(b"\x89PNG\r\n\x1a\n") or head.startswith(b"\xff\xd8\xff") or head.startswith(b"BM")
            or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"))


def validate_background(filename: str, head: bytes, size: int, max_bytes: int = MAX_BACKGROUND_BYTES) -> Kind:
    """Check an uploaded background before reading it; returns its kind."""
    ext = os.path.splitext(os.path.basename(filename or ""))[1].lower()
    if ext not in IMAGE_EXTENSIONS and ext not in VIDEO_EXTENSIONS:
        raise BackgroundError(f"背景只能是图片（PNG / JPG / WebP / BMP）或视频（MP4 / MOV / MKV / GIF …），不支持 {ext or '（无扩展名）'}")
    if size <= 0:
        raise BackgroundError("背景文件为空")
    if size > max_bytes:
        raise BackgroundError(f"背景文件过大（{size} 字节 > {max_bytes}）")
    if ext in IMAGE_EXTENSIONS:
        if not _image_magic(head):
            raise BackgroundError("背景文件的内容不像图片")
        return "image"
    if ext != ".gif" and sniff_audio_format(head[:64]) is None:
        raise BackgroundError("背景文件的内容不像视频")
    if ext == ".gif" and not head.startswith(b"GIF8"):
        raise BackgroundError("背景文件的内容不像 GIF")
    return "video"


def probe_background(path: Path, kind: Kind) -> dict:
    """{width, height, duration_ms} of a background (width / height as displayed)."""
    from ..audio.video import probe_media

    info = probe_media(path)
    w, h = info.get("width"), info.get("height")
    if not info.get("has_video") or not w or not h:
        raise BackgroundError("无法读取背景的画面尺寸" + ("（图片可能已损坏）" if kind == "image" else "（视频里没有画面）"))
    dur = info.get("duration_ms") or None
    if kind == "video" and (not dur or dur < 100):
        raise BackgroundError("背景视频太短或读不出时长")
    return {"width": int(w), "height": int(h), "duration_ms": dur if kind == "video" else None}


def frame_for(width: int, height: int) -> tuple[int, int]:
    """The output frame for a background: its aspect ratio, longer side ``LONG_SIDE``, both sides even."""
    s = LONG_SIDE / max(1, max(width, height))
    return max(2, int(round(width * s)) // 2 * 2), max(2, int(round(height * s)) // 2 * 2)


def cover_filter(w: int, h: int) -> str:
    """Scale to cover the frame and crop the overflow (centred)."""
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"


def input_args(path: Path, kind: Kind, *, at_s: Optional[float] = None, duration_ms: Optional[int] = None) -> list[str]:
    """ffmpeg input options for a background: a picture repeated at 30 fps, or a video looped.
    ``at_s``: one frame at that song time (preview), taken from the loop position."""
    if kind == "image":
        return ["-loop", "1", "-framerate", "30", "-i", str(path)]
    if at_s is not None:
        pos = at_s % (duration_ms / 1000.0) if duration_ms else 0.0
        return ["-ss", f"{pos:.3f}", "-i", str(path)]
    return ["-stream_loop", "-1", "-i", str(path)]
