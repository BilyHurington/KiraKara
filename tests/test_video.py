"""Video in (audio extracted) → reduced-vocal video out, keeping A/V sync."""

import shutil
import subprocess

import numpy as np
import pytest
import soundfile as sf

from kara_align import service as S
from kara_align.audio.io import ffmpeg_path, load_audio
from kara_align.audio.video import probe_media

pytestmark = pytest.mark.skipif(shutil.which("ffprobe") is None and shutil.which(ffmpeg_path()) is None,
                                reason="needs ffmpeg")

SR = 48000
CLICK_S = 2.0      # click position inside the audio stream
AUDIO_OFFSET = 0.5  # audio stream starts this late relative to the video


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("KARA_ALIGN_HOME", str(tmp_path / "home"))


def _click_track(seconds=6.0):
    x = np.zeros(int(seconds * SR), dtype=np.float32)
    i = int(CLICK_S * SR)
    x[i:i + 480] = 0.8
    return x


def _make_video(tmp_path):
    wav = tmp_path / "a.wav"
    sf.write(wav, _click_track(), SR)
    out = tmp_path / "clip.mp4"
    cmd = [ffmpeg_path(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=6.5",
           "-itsoffset", str(AUDIO_OFFSET), "-i", str(wav), "-map", "0:v", "-map", "1:a",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(out)]
    subprocess.run(cmd, check=True)
    return out


def _click_time_on_video_timeline(path):
    """Absolute time of the click: audio stream start + position in the stream."""
    info = probe_media(path)
    x, sr = load_audio(path, mono=True)
    pos = int(np.argmax(np.abs(x[0]) > 0.3)) / sr
    return info["audio_start_s"] - info["start_s"] + pos


def test_video_upload_extracts_audio_and_export_keeps_sync(tmp_path):
    video = _make_video(tmp_path)
    h = S.create_dir(tmp_path / "proj", "v", "plain")
    asset = S.add_media(h, video, "original", filename="clip.mp4")
    v = h.project.video
    assert v is not None and v.width == 160 and v.audio_sha256 == asset.sha256
    assert abs(v.audio_offset_s - AUDIO_OFFSET) < 0.05
    # time 0 of the extracted original is the audio stream start (which may include
    # codec priming); on the video timeline the click is where it was authored
    x, sr = load_audio(h.dir / asset.path, mono=True)
    click_in_audio = np.argmax(np.abs(x[0]) > 0.3) / sr
    assert abs(v.audio_offset_s + click_in_audio - (AUDIO_OFFSET + CLICK_S)) < 0.005

    # stand-in stems: vocals = the click, instrumental = silence
    sf.write(tmp_path / "voc.wav", x[0], sr)
    sf.write(tmp_path / "inst.wav", np.zeros_like(x[0]), sr)
    S.add_audio(h, tmp_path / "voc.wav", "vocals")
    S.add_audio(h, tmp_path / "inst.wav", "instrumental")

    out = S.export_video(h, {"vocal_keep_pct": 100, "instrumental_pct": 100})
    path = h.dir / "exports" / out["filename"]
    assert path.suffix == ".mp4"
    info = probe_media(path)
    assert info["has_video"] and info["video_codec"] == "h264"  # picture copied
    # lip sync: the click sits at the same place on the video timeline
    before = _click_time_on_video_timeline(video)
    after = _click_time_on_video_timeline(path)
    assert abs(after - before) < 0.025, (before, after)

    quiet = S.export_video(h, {"vocal_keep_pct": 0, "instrumental_pct": 100})
    y, _ = load_audio(h.dir / "exports" / quiet["filename"], mono=True)
    assert np.max(np.abs(y)) < 0.01  # vocals removed


def test_audio_upload_clears_video_and_export_requires_video(tmp_path):
    video = _make_video(tmp_path)
    h = S.create_dir(tmp_path / "proj", "v", "plain")
    S.add_media(h, video, "original")
    sf.write(tmp_path / "plain.wav", _click_track(), SR)
    S.add_media(h, tmp_path / "plain.wav", "original")
    assert h.project.video is None
    with pytest.raises(S.ServiceError):
        S.export_video(h, {})


def test_video_without_audio_is_rejected(tmp_path):
    out = tmp_path / "silent.mp4"
    subprocess.run([ffmpeg_path(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=10:duration=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], check=True)
    h = S.create_dir(tmp_path / "proj", "v", "plain")
    with pytest.raises(Exception, match="没有音轨"):
        S.add_media(h, out, "original")
