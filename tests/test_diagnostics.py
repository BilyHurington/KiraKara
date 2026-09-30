"""Failure diagnostics (log file, report), the low-confidence check, the video encoder choice."""

import logging
import subprocess
from pathlib import Path

import pytest

from kara_align.align.checks import check_confidence
from kara_align.models import ManualEdit, UnitTiming


def _units(scores_per_line):
    out = []
    t = 0
    for li, scores in enumerate(scores_per_line):
        for ui, s in enumerate(scores):
            out.append(UnitTiming(unit_id=f"u{li}_{ui}", line_id=f"L{li}", segment_id="s", reading="あ",
                                  start_ms=t, end_ms=t + 200, acoustic_score=s))
            t += 250
    return out


def test_low_confidence_lines():
    normal = [[-1.0, -1.1, -0.9, -1.05, -0.95]] * 12  # 60 units around -1
    units = _units(normal + [[-1.0, -6.0, -7.0, -1.0], [-1.0, -6.5, -1.0, -1.0, -1.0, -1.0, -1.0, -1.0]])
    issues = check_confidence(units)
    # the line with 2 of 4 units far off; not the one with only 1 of 8
    assert [i.line_id for i in issues] == ["L12"]
    assert issues[0].code == "low_confidence" and issues[0].data == {"low_units": 2, "units": 4}
    assert sorted(u.unit_id for u in units if "low_confidence" in u.flags) == ["u12_1", "u12_2"]
    # a unit set by hand does not count; too few units: nothing to compare with
    units = _units(normal + [[-1.0, -6.0, -7.0, -1.0]])
    units[-2].manual = ManualEdit(start_ms=0, end_ms=10)
    assert check_confidence(units) == []
    assert check_confidence(_units([[-1.0, -9.0, -9.0]])) == []


def test_report_and_log(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from kara_align import diagnostics as D
    from kara_align.web.server import create_app

    monkeypatch.setenv("KARA_ALIGN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(D, "_configured", None)
    kara = logging.getLogger("kara_align")
    before = list(kara.handlers)
    try:
        path = D.setup_logging()
        assert path == tmp_path / "home" / "logs" / "milikara.log"
        D.failure("任务 t1", "CUDA out of memory", "Traceback …\nRuntimeError: CUDA out of memory")
        for h in kara.handlers:
            h.flush()
        assert "CUDA out of memory" in path.read_text(encoding="utf-8")
        c = TestClient(create_app(tmp_path / "projects"))
        text = c.get("/api/diagnostics").json()["text"]
        assert text.startswith("MiliKara 诊断信息") and "ffmpeg：" in text and "视频编码：" in text
        assert "任务 t1 failed: CUDA out of memory" in text  # the end of the log
        assert "api_key" not in text.lower()
        assert c.get("/api/diagnostics", params={"task": "nope"}).status_code in (200, 404)
    finally:
        for h in list(kara.handlers):
            if h not in before:
                kara.removeHandler(h)
                h.close()
        logging.getLogger("uvicorn.error").handlers = [h for h in logging.getLogger("uvicorn.error").handlers if h in before]


def test_report_hides_the_home_folder(monkeypatch):
    from kara_align import diagnostics as D

    home = str(Path.home())
    text = D.report(task={"id": "t1", "name": "x", "error": f"找不到 {home}/a.mp4", "stages": [], "detail": None})
    assert home not in text and "找不到 ~/a.mp4" in text


def test_encoder_choice(monkeypatch):
    from kara_align.karaoke import render as R

    monkeypatch.setattr(R, "_encoders", lambda: " libx264 h264_nvenc h264_videotoolbox ")
    works = {"h264_nvenc": False, "h264_videotoolbox": True}
    monkeypatch.setattr(R, "_encoder_works", lambda name, args: works.get(name, False))
    assert R.video_encoder("standard", hardware=False)[:2] == ["-c:v", "libx264"]
    assert R.video_encoder("standard", (3840, 2160), hardware=True) == ["-c:v", "h264_videotoolbox", "-b:v", "40M", "-pix_fmt", "yuv420p"]
    works["h264_nvenc"] = True  # NVIDIA first
    enc = R.video_encoder("high", hardware=True)
    assert enc[:2] == ["-c:v", "h264_nvenc"] and enc[enc.index("-cq") + 1] == "18"
    # no libx264: VideoToolbox even without the setting
    monkeypatch.setattr(R, "_encoders", lambda: " h264_videotoolbox ")
    assert R.video_encoder("standard", hardware=False)[1] == "h264_videotoolbox"


def test_the_setting(tmp_path, monkeypatch):
    import sys

    from kara_align import settings as app_settings
    from kara_align.karaoke import render as R

    monkeypatch.setenv("KARA_ALIGN_HOME", str(tmp_path / "home"))
    assert app_settings.load().hardware_encoding is True
    monkeypatch.setattr(sys, "platform", "win32")
    assert R.prefer_hardware(False) and R.prefer_hardware(True)
    monkeypatch.setattr(sys, "platform", "darwin")  # VideoToolbox only over a video (measured)
    assert not R.prefer_hardware(False) and R.prefer_hardware(True)
    app_settings.update({"hardware_encoding": False})
    assert not R.prefer_hardware(True)


def test_a_failing_gpu_encoder_falls_back_to_the_cpu(tmp_path, monkeypatch):
    from kara_align.karaoke import render as R

    if not R.ffmpeg_available() or "libx264" not in R._encoders():
        pytest.skip("needs ffmpeg with libx264")
    # an encoder that passes the probe but fails on the real burn
    monkeypatch.setattr(R, "video_encoder", lambda quality, size=(0, 0), hardware=False: ["-c:v", "libx264", "-preset", "nope"])
    monkeypatch.setattr(R, "_subtitles_filter", lambda name: "null")
    steps = []
    out = R.burn("", tmp_path / "o.mp4", (64, 64), 300, progress=lambda f, m: steps.append(m))
    assert out.exists() and "显卡编码失败，改用 CPU 编码" in steps
    r = subprocess.run([R.ffmpeg_path(), "-v", "error", "-i", str(out), "-f", "null", "-"], capture_output=True)
    assert r.returncode == 0
