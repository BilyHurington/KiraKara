"""Audio + background picture / looped background video → karaoke video."""

import shutil
import subprocess
import time

import numpy as np
import pytest
import soundfile as sf
from PIL import Image

from kara_align import pipeline as P
from kara_align import service as S
from kara_align import settings as AS
from kara_align.align.backends.fake import ScriptedBackend
from kara_align.audio.io import ffmpeg_path
from kara_align.audio.video import probe_media
from kara_align.karaoke.background import frame_for, validate_background, BackgroundError

pytestmark = pytest.mark.skipif(shutil.which(ffmpeg_path()) is None, reason="needs ffmpeg")

SCRIPT = [("ki", 1000, 1200), ("mi", 1200, 1400), ("to", 1400, 1700), ("so", 3000, 3200), ("ra", 3200, 3500)]


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("KARA_ALIGN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(ScriptedBackend, "default_script", SCRIPT)


def _wav(path, seconds=5.0, sr=22050):
    rng = np.random.default_rng(0)
    sf.write(path, (rng.standard_normal(int(seconds * sr)) * 0.05).astype(np.float32), sr)
    return path


def _png(path, size=(800, 600), color=(200, 40, 40)):
    Image.new("RGB", size, color).save(path)
    return path


def _clip(path, seconds=1.2, size="320x180"):
    subprocess.run([ffmpeg_path(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=25:duration={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def _aligned(tmp_path):
    h = S.create_dir(tmp_path / "proj", "song", "plain")
    S.update_settings(h, config={"backend": "scripted"})
    pv = S.parse_lyrics(h, "きみと\nそら\n", origin="paste")
    S.apply_lyrics(h, pv["preview_id"])
    S.add_media(h, _wav(tmp_path / "song.wav"), "original")
    S.run_align(h)
    return h


def test_frame_and_upload_checks(tmp_path):
    assert frame_for(800, 600) == (1920, 1440) and frame_for(1080, 1920) == (1080, 1920)
    assert frame_for(1280, 720) == (1920, 1080)
    with pytest.raises(BackgroundError):
        validate_background("a.png", b"not a png at all", 10)
    with pytest.raises(BackgroundError):
        validate_background("a.txt", b"x", 1)
    assert validate_background("a.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 8, 100) == "image"


def test_picture_background_is_burned_under_the_subtitles(tmp_path):
    h = _aligned(tmp_path)
    assert S.picture(h)["source"] == "black"
    bg = S.set_background(h, _png(tmp_path / "cover.png"), filename="cover.png")
    assert (bg.kind, bg.width, bg.height) == ("image", 800, 600)
    assert S.picture(h) == {"source": "background", "kind": "image", "filename": "cover.png", "width": 1920, "height": 1440}
    png = S.karaoke_preview(h, 1300)
    frame = Image.open(__import__("io").BytesIO(png)).convert("RGB")
    assert frame.size == (1920, 1440) and frame.getpixel((10, 10))[0] > 150  # the red picture, not black
    out = S.karaoke_burn(h)
    info = probe_media(h.dir / "exports" / out["filename"])
    assert (info["width"], info["height"]) == (1920, 1440) and info["has_audio"]
    assert abs(info["duration_ms"] - 5000) < 300  # the song's length, not the picture's
    # "black" still gives black; removing the background goes back to it
    assert Image.open(__import__("io").BytesIO(S.karaoke_preview(h, 1300, background="black"))).size == (1920, 1440)
    S.clear_background(h)
    assert S.picture(h)["source"] == "black" and S.picture(h)["width"] == 1920 and S.picture(h)["height"] == 1080


def test_background_video_loops_for_the_whole_song_and_comes_before_the_songs_video(tmp_path):
    h = _aligned(tmp_path)
    clip = _clip(tmp_path / "loop.mp4")
    bg = S.set_background(h, clip)
    assert bg.kind == "video" and bg.duration_ms and bg.duration_ms < 2000
    S.karaoke_preview(h, 4200)  # past the clip's end: taken from the loop
    out = S.karaoke_burn(h, audio="original")
    info = probe_media(h.dir / "exports" / out["filename"])
    assert (info["width"], info["height"]) == (1920, 1080) and info["has_audio"]
    assert abs(info["duration_ms"] - 5000) < 300


def test_task_with_audio_and_background(tmp_path, monkeypatch):
    orig = P.STAGE_FUNCS["import"]

    def wrapped(q, task, cfg, cancel, progress):
        out = orig(q, task, cfg, cancel, progress)
        S.update_settings(q.ws.get(task.project_id), config={"backend": "scripted"})
        return out

    monkeypatch.setitem(P.STAGE_FUNCS, "import", wrapped)
    AS.update({"simple": {"separate": False, "auto_export": True}})
    q = P.TaskQueue(S.Workspace(tmp_path / "projects"))
    (tmp_path / "bad.png").write_bytes(b"not a picture")
    with pytest.raises(S.ServiceError):  # a broken background is refused before the task is added
        q.add(media=_wav(tmp_path / "a.wav"), filename="a.wav", lyrics="きみと\nそら\n", mode="plain",
              background=tmp_path / "bad.png", background_filename="bad.png")
    assert not q.tasks
    t = q.add(media=_wav(tmp_path / "a.wav"), filename="a.wav", lyrics="きみと\nそら\n", mode="plain", name="A",
              background=_png(tmp_path / "bg.png", (1080, 1920)), background_filename="bg.png")
    assert t.background_filename == "bg.png"
    t0 = time.time()
    while q.get(t.id).status not in ("succeeded", "failed") and time.time() - t0 < 120:
        time.sleep(0.2)
    t = q.get(t.id)
    assert t.status == "succeeded", (t.error, t.detail)
    h = q.ws.get(t.project_id)
    assert h.project.background is not None and h.project.background.filename == "bg.png"
    info = probe_media(h.dir / "exports" / t.outputs["video"]["filename"])
    assert (info["width"], info["height"]) == (1080, 1920)  # the portrait picture's frame
    q.shutdown()


def test_tasks_http_api_takes_a_background(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from kara_align.web.server import create_app

    monkeypatch.setattr(P.TaskQueue, "_worker", lambda self: None)  # nothing runs
    monkeypatch.setattr(P.TaskQueue, "_prepare", lambda self, t: None)
    app = create_app(tmp_path / "projects")
    c = TestClient(app)
    files = {"file": ("a.wav", _wav(tmp_path / "a.wav").read_bytes(), "audio/wav"),
             "background": ("bg.png", _png(tmp_path / "bg.png").read_bytes(), "image/png")}
    r = c.post("/api/tasks", data={"lyrics": "きみと", "mode": "plain"}, files=files)
    assert r.status_code == 200, r.text
    assert r.json()["background_filename"] == "bg.png"
    files["background"] = ("bg.png", b"not an image", "image/png")
    r = c.post("/api/tasks", data={"lyrics": "きみと", "mode": "plain"}, files=files)
    assert r.status_code == 400 and "图片" in r.json()["detail"]
    # without a background: as before
    del files["background"]
    assert c.post("/api/tasks", data={"lyrics": "きみと", "mode": "plain"}, files=files).status_code == 200
