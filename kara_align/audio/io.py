"""Audio decoding, probing, hashing and content-addressed import.

Decoding keeps the file's own sample rate and channel layout unless a target
rate is requested.  Formats libsndfile understands (wav/flac/aiff/ogg-vorbis…)
are read directly; everything else (mp3/m4a/aac/opus/…) is decoded by ffmpeg to
32-bit float PCM.

Encoder priming delay: ffmpeg's default decoding path already drops the
encoder delay it knows about — the LAME/Xing "encoder delay/padding" header of
mp3 files and the edit list (``elst``) / ``iTunSMPB`` priming of AAC in MP4
containers.  We therefore call ffmpeg *without* ``-ss``/``-ignore_editlist``
and without any resampling filter so that decoded sample 0 is the first sample
of the original programme material.  Files lacking such metadata cannot be
corrected automatically; a stem imported with unknown delay must go through
:mod:`kara_align.audio.sync` instead of being stretched.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Union

import numpy as np
import soundfile as sf

from ..models import AudioAsset, AudioRole, AudioSource
from .resample import resample as _resample
from .resample import to_mono

PathLike = Union[str, os.PathLike]

ALLOWED_EXTENSIONS = {".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".aif", ".aiff", ".wma", ".webm", ".mp4"}
DEFAULT_MAX_UPLOAD_BYTES = 1024 * 1024 * 1024  # 1 GiB


class AudioError(Exception):
    pass


def ffmpeg_path() -> str:
    exe = os.environ.get("KARA_ALIGN_FFMPEG") or shutil.which("ffmpeg")
    if not exe:
        for cand in ("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"):
            if os.path.exists(cand):
                return cand
        raise AudioError("ffmpeg not found; install ffmpeg or set KARA_ALIGN_FFMPEG")
    return exe


def ffprobe_path() -> Optional[str]:
    ff = ffmpeg_path()
    cand = os.path.join(os.path.dirname(ff), "ffprobe")
    if os.path.exists(cand):
        return cand
    return shutil.which("ffprobe")


def file_sha256(path: PathLike, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _sf_readable(path: PathLike) -> bool:
    try:
        sf.info(str(path))
        return True
    except Exception:
        return False


def _ffprobe_stream(path: PathLike) -> dict:
    probe = ffprobe_path()
    if not probe:
        raise AudioError("ffprobe not found")
    import json

    out = subprocess.run(
        [probe, "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=sample_rate,channels", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise AudioError(f"cannot probe audio: {out.stderr.strip()[:300]}")
    streams = json.loads(out.stdout or "{}").get("streams") or []
    if not streams:
        raise AudioError("no audio stream found")
    return {"sample_rate": int(streams[0]["sample_rate"]), "channels": int(streams[0]["channels"])}


def _ffmpeg_decode(path: PathLike) -> tuple[np.ndarray, int]:
    info = _ffprobe_stream(path)
    sr, ch = info["sample_rate"], info["channels"]
    cmd = [ffmpeg_path(), "-v", "error", "-nostdin", "-i", str(path), "-map", "0:a:0",
           "-f", "f32le", "-acodec", "pcm_f32le", "-ac", str(ch), "-ar", str(sr), "pipe:1"]
    out = subprocess.run(cmd, capture_output=True)
    if out.returncode != 0:
        raise AudioError(f"ffmpeg decode failed: {out.stderr.decode(errors='replace').strip()[:300]}")
    data = np.frombuffer(out.stdout, dtype="<f4")
    n = len(data) // ch
    data = data[: n * ch].reshape(n, ch).T.copy()
    return data.astype(np.float32, copy=False), sr


def load_audio(path: PathLike, *, target_sr: Optional[int] = None, mono: bool = False) -> tuple[np.ndarray, int]:
    """Decode ``path`` into float32 ``[channels, n]`` at its original rate.

    ``target_sr`` resamples (zero-phase, origin preserving); ``mono`` averages
    channels (returned shape ``[1, n]``).
    """
    path = Path(path)
    if not path.exists():
        raise AudioError(f"file not found: {path}")
    if _sf_readable(path) and path.suffix.lower() != ".mp3":
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        x = data.T.copy()
    else:
        x, sr = _ffmpeg_decode(path)
    if mono:
        x = to_mono(x)[None, :]
    if target_sr and target_sr != sr:
        x = _resample(x, sr, target_sr)
        sr = target_sr
    return x.astype(np.float32, copy=False), int(sr)


def probe_audio(path: PathLike) -> dict:
    """Duration / rate / channels / samples as actually decoded (priming trimmed)."""
    path = Path(path)
    if _sf_readable(path) and path.suffix.lower() != ".mp3":
        info = sf.info(str(path))
        sr, ch, n = int(info.samplerate), int(info.channels), int(info.frames)
    else:
        x, sr = _ffmpeg_decode(path)
        ch, n = x.shape
    return {"duration_ms": int(round(n * 1000.0 / sr)), "sample_rate": sr, "channels": ch, "num_samples": n}


def write_wav(path: PathLike, data: np.ndarray, sr: int, subtype: str = "PCM_16") -> Path:
    """Write ``[channels, n]`` (or 1-D) float audio as WAV."""
    if subtype not in ("PCM_16", "PCM_24", "FLOAT"):
        raise ValueError(f"unsupported subtype {subtype}")
    x = np.asarray(data, dtype=np.float32)
    if x.ndim == 1:
        x = x[None, :]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = x.T
    if subtype != "FLOAT":
        out = np.clip(out, -1.0, 1.0)
    sf.write(str(path), out, int(sr), subtype=subtype, format="WAV")
    return path


# ---------------------------------------------------------------------------
# untrusted upload validation
# ---------------------------------------------------------------------------

_MAGIC = [
    (b"RIFF", "wav"), (b"fLaC", "flac"), (b"OggS", "ogg"), (b"ID3", "mp3"),
    (b"FORM", "aiff"), (b"\x1a\x45\xdf\xa3", "webm"),
]


def sniff_audio_format(head: bytes) -> Optional[str]:
    for magic, name in _MAGIC:
        if head.startswith(magic):
            return name
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return "mp3/aac-adts"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "mp4"
    if head.startswith(b"\x30\x26\xb2\x75"):
        return "wma"
    return None


def validate_upload(filename: str, head: bytes, size: int, max_bytes: int = DEFAULT_MAX_UPLOAD_BYTES) -> str:
    """Check an uploaded file before decoding; returns a safe lower-case extension.

    Raises :class:`AudioError` for unknown extensions, oversized files or
    content whose magic bytes don't look like audio.
    """
    name = os.path.basename(filename or "")
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise AudioError(f"unsupported audio extension: {ext or '(none)'}")
    if size <= 0:
        raise AudioError("empty upload")
    if size > max_bytes:
        raise AudioError(f"upload too large ({size} bytes > {max_bytes})")
    if sniff_audio_format(head[:64]) is None:
        raise AudioError("file content does not look like a supported audio format")
    return ext


# ---------------------------------------------------------------------------
# asset import
# ---------------------------------------------------------------------------


def import_asset(
    src_path: PathLike,
    role: AudioRole,
    assets_dir: PathLike,
    source: Optional[AudioSource] = None,
    *,
    project_dir: Optional[PathLike] = None,
    origin_offset_samples: int = 0,
) -> AudioAsset:
    """Copy ``src_path`` into ``assets_dir/<sha256><ext>`` and describe it.

    ``AudioAsset.path`` is relative to ``project_dir`` (defaults to the parent
    of ``assets_dir``).  The file is decoded once to verify it and to record the
    decoded duration / rate / sample count.
    """
    src = Path(src_path)
    assets = Path(assets_dir)
    assets.mkdir(parents=True, exist_ok=True)
    sha = file_sha256(src)
    ext = src.suffix.lower() or ".bin"
    if ext not in ALLOWED_EXTENSIONS:
        raise AudioError(f"unsupported audio extension: {ext}")
    dest = assets / f"{sha}{ext}"
    if not dest.exists():
        tmp = dest.with_suffix(dest.suffix + ".part")
        shutil.copyfile(src, tmp)
        os.replace(tmp, dest)
    info = probe_audio(dest)
    base = Path(project_dir) if project_dir is not None else assets.parent
    try:
        rel = os.path.relpath(dest, base)
    except ValueError:
        rel = str(dest)
    src_meta = source.model_copy() if source is not None else AudioSource()
    if not src_meta.filename:
        src_meta.filename = src.name
    return AudioAsset(
        role=role,
        sha256=sha,
        path=rel,
        duration_ms=info["duration_ms"],
        sample_rate=info["sample_rate"],
        channels=info["channels"],
        num_samples=info["num_samples"],
        origin_offset_samples=origin_offset_samples,
        source=src_meta,
    )
