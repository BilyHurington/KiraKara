"""Preview frames and burned-in karaoke videos (ffmpeg + libass).

Both use the same ASS and the same renderer, so the preview is what the burned
video will look like. Times: the ASS is on the audio timeline; with a video
the audio starts ``video.audio_offset_s`` after the (normalized) video start.
"""

from __future__ import annotations

import functools
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

from ..audio.io import AudioError, ffmpeg_path


class RenderError(AudioError):
    pass


@functools.lru_cache(maxsize=1)
def _encoders() -> str:
    out = subprocess.run([ffmpeg_path(), "-hide_banner", "-encoders"], capture_output=True, text=True)
    return out.stdout


def video_encoder(quality: str) -> list[str]:
    if "libx264" in _encoders():
        preset, crf = ("veryfast", "20") if quality != "high" else ("medium", "17")
        return ["-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p"]
    if "h264_videotoolbox" in _encoders():
        return ["-c:v", "h264_videotoolbox", "-b:v", "12M" if quality == "high" else "8M", "-pix_fmt", "yuv420p"]
    return ["-c:v", "mpeg4", "-q:v", "2"]


def _subtitles_filter(ass_name: str) -> str:
    # run ffmpeg inside the temp dir so the filter argument needs no escaping
    return f"subtitles={ass_name}"


def preview_png(ass_text: str, t_ms: int, size: tuple[int, int], video: Optional[Path] = None,
                audio_offset_s: float = 0.0) -> bytes:
    """One frame at audio time ``t_ms``: the video frame there, or black."""
    w, h = size
    t = max(0.0, t_ms / 1000.0)
    with tempfile.TemporaryDirectory() as td:
        Path(td, "k.ass").write_text(ass_text, encoding="utf-8")
        out = Path(td, "p.png")
        # the frame gets pts = t so the subtitles filter draws the state at t
        # millisecond timebase first: a 1 fps source would round t to whole seconds
        vf = f"settb=1/1000,setpts=PTS-STARTPTS+{t:.3f}/TB,{_subtitles_filter('k.ass')}"
        if video is not None:
            inp = ["-ss", f"{t + audio_offset_s:.3f}", "-i", str(video)]
            vf = f"scale={w}:{h},{vf}"
        else:
            inp = ["-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r=1:d=1"]
        cmd = [ffmpeg_path(), "-v", "error", "-nostdin", "-y", *inp, "-vf", vf, "-frames:v", "1", str(out)]
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=td)
        if r.returncode != 0 or not out.exists():
            raise RenderError(f"预览渲染失败：{r.stderr.strip()[-300:]}")
        return out.read_bytes()


def burn(ass_text: str, out_path: Path, size: tuple[int, int], duration_ms: int, *,
         video: Optional[Path] = None, audio: Optional[Path] = None, audio_offset_s: float = 0.0,
         use_video_audio: bool = False, quality: str = "standard", cancel=None,
         progress: Optional[Callable[[float, str], None]] = None) -> Path:
    """Render subtitles into a video (the source video, or black at ``size``).

    ``audio``: a file to use as the soundtrack (placed at ``audio_offset_s``);
    ``use_video_audio``: keep the source video's first audio stream instead.
    """
    from ..interfaces import Cancelled

    w, h = size
    dur = max(0.1, duration_ms / 1000.0)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        Path(td, "k.ass").write_text(ass_text, encoding="utf-8")
        cmd = [ffmpeg_path(), "-v", "error", "-nostdin", "-y", "-progress", "pipe:1", "-nostats"]
        if video is not None:
            cmd += ["-i", str(video)]
            vf = _subtitles_filter("k.ass")
        else:
            cmd += ["-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r=30:d={dur + audio_offset_s:.3f}"]
            vf = _subtitles_filter("k.ass")
        maps = ["-map", "0:v:0"]
        if audio is not None:
            if audio_offset_s > 0:
                cmd += ["-itsoffset", f"{audio_offset_s:.6f}"]
            cmd += ["-i", str(audio)]
            maps += ["-map", "1:a:0", "-c:a", "aac", "-b:a", "256k"]
        elif use_video_audio and video is not None:
            maps += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "256k"]
        else:
            maps += ["-an"]
        cmd += ["-vf", vf, *maps, *video_encoder(quality), "-movflags", "+faststart", str(out_path.resolve())]
        # stderr goes to a file: an undrained pipe could block ffmpeg
        err_file = open(Path(td, "err.log"), "w+", encoding="utf-8", errors="replace")
        proc = subprocess.Popen(cmd, cwd=td, stdout=subprocess.PIPE, stderr=err_file, text=True, bufsize=1)
        total = dur + audio_offset_s
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if cancel is not None and getattr(cancel, "cancelled", False):
                    proc.terminate()
                    raise Cancelled()
                if line.startswith("out_time_us=") and progress:
                    try:
                        done = int(line.split("=", 1)[1]) / 1e6
                    except ValueError:
                        continue
                    progress(min(0.99, done / total), f"烧录中 {min(100, int(done / total * 100))}%")
            proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
                time.sleep(0.1)
        err_file.seek(0)
        err = err_file.read()
        err_file.close()
        if proc.returncode != 0 or not out_path.exists():
            out_path.unlink(missing_ok=True)
            raise RenderError(f"烧录失败：{err.strip()[-400:]}")
    return out_path


def ffmpeg_available() -> bool:
    return shutil.which(ffmpeg_path()) is not None
