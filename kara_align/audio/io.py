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

ALLOWED_EXTENSIONS = {".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".aif", ".aiff", ".wma", ".webm", ".mp4",
                      # video containers: the audio track is extracted
                      ".mov", ".m4v", ".mkv", ".avi", ".flv", ".ts", ".mts", ".m2ts", ".wmv", ".mpg", ".mpeg", ".3gp"}
DEFAULT_MAX_UPLOAD_BYTES = 1024 * 1024 * 1024  # 1 GiB


class AudioError(Exception):
    pass


def ffmpeg_path() -> str:
    exe = os.environ.get("KARA_ALIGN_FFMPEG") or shutil.which("ffmpeg")
    if not exe:
        for cand in ("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"):
            if os.path.exists(cand):
                return cand
        raise AudioError("找不到 ffmpeg；请安装 ffmpeg 或设置 KARA_ALIGN_FFMPEG")
    return exe


def ffprobe_path() -> Optional[str]:
    ff = ffmpeg_path()
    for name in ("ffprobe", "ffprobe.exe"):
        cand = os.path.join(os.path.dirname(ff), name)
        if os.path.isfile(cand):
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


# ffprobe answers in seconds; decoding / extracting / muxing a long video can take minutes
PROBE_TIMEOUT_S = 120.0
DECODE_TIMEOUT_S = 30 * 60.0


def run_tool(cmd: list[str], *, timeout: float, cancel=None, stdout=None, what: str = "ffmpeg") -> tuple[int, bytes, str]:
    """Run ffmpeg / ffprobe with a time limit, stopped by ``cancel`` (anything with ``cancelled``).

    Output goes to temporary files (never a full pipe, never doubled in memory); ``stdout`` may
    be an open binary file to receive it instead.  The process is always gone when this returns
    or raises.  Returns ``(returncode, stdout bytes, stderr text)``.
    """
    import tempfile
    import time

    from ..interfaces import Cancelled

    own_out = stdout is None
    out_f = tempfile.TemporaryFile() if own_out else stdout
    with tempfile.TemporaryFile() as err_f:
        try:
            try:
                proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out_f, stderr=err_f)
            except OSError as e:
                raise AudioError(f"无法启动 {what}：{e}") from e
            t0 = time.monotonic()
            try:
                while True:
                    try:
                        proc.wait(timeout=0.2)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                    if cancel is not None and getattr(cancel, "cancelled", False):
                        raise Cancelled()
                    if time.monotonic() - t0 > timeout:
                        raise AudioError(f"{what} 超过 {int(timeout)} 秒没有完成，已停止")
            finally:
                if proc.poll() is None:
                    proc.kill()
                    try:
                        proc.wait(10)
                    except subprocess.TimeoutExpired:
                        pass
            err_f.seek(0)
            err = err_f.read().decode("utf-8", errors="replace")
            data = b""
            if own_out:
                out_f.seek(0)
                data = out_f.read()
            return proc.returncode, data, err
        finally:
            if own_out:
                out_f.close()


def _ffprobe_stream(path: PathLike, cancel=None) -> dict:
    probe = ffprobe_path()
    if not probe:
        raise AudioError("找不到 ffprobe")
    import json

    code, out, err = run_tool([probe, "-v", "error", "-select_streams", "a:0", "-show_entries",
                               "stream=sample_rate,channels", "-of", "json", str(path)],
                              timeout=PROBE_TIMEOUT_S, cancel=cancel, what="ffprobe")
    if code != 0:
        raise AudioError(f"无法读取音频信息：{err.strip()[:300]}")
    try:
        streams = json.loads(out.decode("utf-8", errors="replace") or "{}").get("streams") or []
        info = {"sample_rate": int(streams[0]["sample_rate"]), "channels": int(streams[0]["channels"])} \
            if streams else None
    except (ValueError, KeyError, TypeError, AttributeError):
        raise AudioError("无法读取音频信息：ffprobe 的输出无法识别") from None
    if not info:
        raise AudioError("文件中没有音频流")
    if info["channels"] < 1 or info["sample_rate"] < 1:
        raise AudioError("无法读取音频信息：声道数或采样率无效")
    return info


def _ffmpeg_decode(path: PathLike, cancel=None) -> tuple[np.ndarray, int]:
    import tempfile

    info = _ffprobe_stream(path, cancel)
    sr, ch = info["sample_rate"], info["channels"]
    cmd = [ffmpeg_path(), "-v", "error", "-nostdin", "-i", str(path), "-map", "0:a:0",
           "-f", "f32le", "-acodec", "pcm_f32le", "-ac", str(ch), "-ar", str(sr), "pipe:1"]
    # the PCM goes to a temporary file and is read once: no pipe buffer + joined copy + array copy
    with tempfile.TemporaryFile() as pcm:
        code, _, err = run_tool(cmd, timeout=DECODE_TIMEOUT_S, cancel=cancel, stdout=pcm)
        if code != 0:
            raise AudioError(f"ffmpeg 解码失败：{err.strip()[:300]}")
        pcm.seek(0)
        data = np.fromfile(pcm, dtype="<f4")
    n = len(data) // ch
    if ch == 1:
        return data[:n].astype(np.float32, copy=False).reshape(1, n), sr
    data = np.ascontiguousarray(data[: n * ch].reshape(n, ch).T)
    return data.astype(np.float32, copy=False), sr


def load_audio(path: PathLike, *, target_sr: Optional[int] = None, mono: bool = False,
               cancel=None) -> tuple[np.ndarray, int]:
    """Decode ``path`` into float32 ``[channels, n]`` at its original rate.

    ``target_sr`` resamples (zero-phase, origin preserving); ``mono`` averages
    channels (returned shape ``[1, n]``).  ``cancel`` stops an ffmpeg decode.
    """
    path = Path(path)
    if not path.exists():
        raise AudioError(f"找不到文件：{path.name}")
    x = None
    if _sf_readable(path) and path.suffix.lower() != ".mp3":
        try:
            data, sr = sf.read(str(path), dtype="float32", always_2d=True)
            x = data.T.copy()
        except (RuntimeError, sf.LibsndfileError):
            # some FLAC files have a valid header but trip libsndfile's decoder
            x = None
    if x is None:
        x, sr = _ffmpeg_decode(path, cancel)
    if mono:
        x = to_mono(x)[None, :]
    if target_sr and target_sr != sr:
        x = _resample(x, sr, target_sr)
        sr = target_sr
    return x.astype(np.float32, copy=False), int(sr)


def probe_audio(path: PathLike, cancel=None) -> dict:
    """Duration / rate / channels / samples as actually decoded (priming trimmed)."""
    path = Path(path)
    if _sf_readable(path) and path.suffix.lower() != ".mp3":
        info = sf.info(str(path))
        sr, ch, n = int(info.samplerate), int(info.channels), int(info.frames)
    else:
        x, sr = _ffmpeg_decode(path, cancel)
        ch, n = x.shape
    return {"duration_ms": int(round(n * 1000.0 / sr)), "sample_rate": sr, "channels": ch, "num_samples": n}


def write_wav(path: PathLike, data: np.ndarray, sr: int, subtype: str = "PCM_16") -> Path:
    """Write ``[channels, n]`` (or 1-D) float audio as WAV."""
    if subtype not in ("PCM_16", "PCM_24", "FLOAT"):
        raise ValueError(f"不支持的 WAV 格式 {subtype}")
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


def write_stem(path_base: PathLike, data: np.ndarray, sr: int) -> Path:
    """Store a stem compactly: 24-bit FLAC (lossless for audio in [-1, 1]).

    Falls back to 32-bit float WAV when the signal exceeds full scale, so
    nothing is ever clipped silently.  ``path_base`` gets the right suffix.
    """
    x = np.asarray(data, dtype=np.float32)
    base = Path(path_base).with_suffix("")
    base.parent.mkdir(parents=True, exist_ok=True)
    if x.size and float(np.max(np.abs(x))) > 1.0:
        return write_wav(base.with_suffix(".wav"), x, sr, subtype="FLOAT")
    out = x[None, :] if x.ndim == 1 else x
    path = base.with_suffix(".flac")
    sf.write(str(path), out.T, int(sr), subtype="PCM_24", format="FLAC")
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
    if head.startswith(b"FLV"):
        return "flv"
    if head.startswith(b"\x00\x00\x01\xba") or head.startswith(b"\x00\x00\x01\xb3"):
        return "mpeg-ps"
    if head[:1] == b"\x47" and (len(head) < 189 or head[188:189] == b"\x47"):
        return "mpeg-ts"
    if len(head) >= 12 and head[4:8] in (b"moov", b"mdat", b"wide", b"free", b"skip"):
        return "quicktime"
    return None


def validate_upload(filename: str, head: bytes, size: int, max_bytes: int = DEFAULT_MAX_UPLOAD_BYTES) -> str:
    """Check an uploaded file before decoding; returns a safe lower-case extension.

    Raises :class:`AudioError` for unknown extensions, oversized files or
    content whose magic bytes don't look like audio.
    """
    name = os.path.basename(filename or "")
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise AudioError(f"不支持的音频 / 视频扩展名：{ext or '（无）'}")
    if size <= 0:
        raise AudioError("上传的文件为空")
    if size > max_bytes:
        raise AudioError(f"上传文件过大（{size} 字节 > {max_bytes}）")
    if sniff_audio_format(head[:64]) is None:
        raise AudioError("文件内容不像受支持的音频或视频格式")
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
        raise AudioError(f"不支持的音频扩展名：{ext}")
    dest = assets / f"{sha}{ext}"
    if not dest.exists():
        tmp = dest.with_suffix(dest.suffix + ".part")
        shutil.copyfile(src, tmp)
        os.replace(tmp, dest)
    info = probe_audio(dest)
    base = Path(project_dir) if project_dir is not None else assets.parent
    try:
        rel = Path(os.path.relpath(dest, base)).as_posix()  # stored with "/" on every system
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
