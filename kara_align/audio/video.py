"""Video input and reduced-vocal video output (ffmpeg / ffprobe).

A video uploaded as the "original" is kept as-is; its first audio stream is
extracted losslessly (FLAC, native sample rate) and used for everything else.
The extracted audio starts at the audio stream's first sample, exactly like
``load_audio`` decodes it, so alignment times stay on the same timeline.

When muxing a new soundtrack back, the picture is copied untouched and the
new audio is placed at the same offset the original audio stream had
relative to the file start, so lip-sync is preserved.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

from .io import AudioError, ffmpeg_path, ffprobe_path

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".flv", ".ts", ".mts", ".m2ts",
                    ".wmv", ".mpg", ".mpeg", ".3gp"}

# container -> audio codec used for the new soundtrack (video is always copied)
_AUDIO_CODEC = {".mp4": ("aac", ["-b:a", "256k"]), ".m4v": ("aac", ["-b:a", "256k"]),
                ".mov": ("aac", ["-b:a", "256k"]), ".mkv": ("flac", []), ".webm": ("libopus", ["-b:a", "192k"])}


class VideoError(AudioError):
    pass


def probe_media(path: str | Path) -> dict:
    """Streams and timing of a media file (ffprobe)."""
    probe = ffprobe_path()
    if not probe:
        raise VideoError("需要 ffprobe（随 ffmpeg 安装）来读取视频")
    out = subprocess.run([probe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise VideoError(f"无法读取媒体文件：{out.stderr.strip()[:300]}")
    data = json.loads(out.stdout or "{}")
    fmt = data.get("format", {})
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)

    def f(x, default=0.0):
        try:
            return float(x)
        except (TypeError, ValueError):
            return default

    info = {
        "format_name": fmt.get("format_name"),
        "duration_ms": int(round(f(fmt.get("duration")) * 1000)),
        "start_s": f(fmt.get("start_time")),
        "has_video": video is not None,
        "has_audio": audio is not None,
    }
    if video is not None:
        num, _, den = str(video.get("avg_frame_rate") or "0/1").partition("/")
        fps = f(num) / f(den, 1.0) if f(den, 1.0) else 0.0
        info.update(video_codec=video.get("codec_name"), pix_fmt=video.get("pix_fmt"),
                    width=video.get("width"), height=video.get("height"),
                    fps=round(fps, 3), video_start_s=f(video.get("start_time")))
    if audio is not None:
        info.update(audio_codec=audio.get("codec_name"), audio_sample_rate=int(f(audio.get("sample_rate"))),
                    audio_channels=audio.get("channels"), audio_start_s=f(audio.get("start_time")))
    return info


def is_video(path: str | Path) -> bool:
    try:
        return probe_media(path)["has_video"]
    except AudioError:
        return False


def extract_audio(video_path: str | Path, out_path: str | Path) -> Path:
    """First audio stream → lossless FLAC at its native sample rate."""
    info = probe_media(video_path)
    if not info["has_audio"]:
        raise VideoError("视频中没有音轨，无法对齐")
    out = Path(out_path).with_suffix(".flac")
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg_path(), "-v", "error", "-nostdin", "-y", "-i", str(video_path), "-map", "0:a:0",
           "-vn", "-sn", "-dn", "-c:a", "flac", "-sample_fmt", "s32", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not out.exists():
        raise VideoError(f"提取视频音轨失败：{r.stderr.strip()[:300]}")
    return out


def audio_offset_s(info: dict) -> float:
    """Where the (first) audio stream starts relative to the file start."""
    return max(0.0, float(info.get("audio_start_s", 0.0)) - float(info.get("start_s", 0.0)))


def mux_audio(video_path: str | Path, audio_path: str | Path, out_base: str | Path, offset_s: float = 0.0,
              container: Optional[str] = None) -> Path:
    """Copy the picture of ``video_path`` and use ``audio_path`` as its only soundtrack.

    ``offset_s`` is the original audio stream's start relative to the file
    start (see :func:`audio_offset_s`). The container follows the source when
    it can hold the copied video; otherwise MKV is used.
    """
    src_ext = (container or Path(video_path).suffix).lower()
    candidates = [src_ext if src_ext in _AUDIO_CODEC else ".mp4", ".mkv"]
    errors = []
    Path(out_base).parent.mkdir(parents=True, exist_ok=True)
    for ext in dict.fromkeys(candidates):
        out = Path(out_base).with_suffix(ext)
        acodec, aopts = _AUDIO_CODEC[ext]
        cmd = [ffmpeg_path(), "-v", "error", "-nostdin", "-y", "-i", str(video_path)]
        if offset_s > 0:
            cmd += ["-itsoffset", f"{offset_s:.6f}"]
        cmd += ["-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", acodec, *aopts,
                "-map_metadata", "0"]
        if ext in (".mp4", ".m4v", ".mov"):
            cmd += ["-movflags", "+faststart"]
        cmd.append(str(out))
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0 and out.exists() and out.stat().st_size > 0:
            return out
        errors.append(f"{ext}: {r.stderr.strip()[-200:]}")
        out.unlink(missing_ok=True)
    raise VideoError("合成视频失败：" + " | ".join(errors))
