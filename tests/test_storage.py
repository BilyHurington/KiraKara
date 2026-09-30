"""Disk space: sizes per project and part, cleaning exports / stems / cache / leftovers, and replaced
files no longer left behind."""

import os
import time

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from kara_align import service as S
from kara_align import storage
from kara_align.web.server import create_app


def _wav(path, seconds=3.0, seed=0):
    x = (np.random.default_rng(seed).standard_normal(int(22050 * seconds)) * 0.05).astype(np.float32)
    sf.write(path, x, 22050)
    return path


def _age(path, seconds=3600):
    t = time.time() - seconds
    os.utime(path, (t, t))


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("KARA_ALIGN_HOME", str(tmp_path / "home"))
    root = tmp_path / "projects"
    c = TestClient(create_app(root))
    ws = c.app.state.workspace
    h = ws.create("song", "plain")
    S.add_audio(h, _wav(tmp_path / "a.wav"), "original")
    S.add_audio(h, _wav(tmp_path / "v.wav", seed=1), "vocals")
    S.add_audio(h, _wav(tmp_path / "i.wav", seed=2), "instrumental")
    (h.dir / "exports").mkdir(exist_ok=True)
    (h.dir / "exports" / "song-karaoke.mp4").write_bytes(b"v" * 5000)
    (h.dir / "exports" / "song-mix.flac").write_bytes(b"m" * 3000)
    return c, ws, h


def test_sizes_and_cleaning_a_project(tmp_path, monkeypatch):
    c, ws, h = _setup(tmp_path, monkeypatch)
    listed = c.get("/api/projects").json()
    assert listed[0]["size"] == storage.size_of(h.dir)
    st = c.get("/api/storage").json()
    p = st["projects"][0]
    assert p["name"] == "song" and p["stems"] and not p["busy"]
    assert p["parts"]["exports"] == 8000 and p["parts"]["media"] > 0 and p["parts"]["stems"] > 0
    assert sum(p["parts"].values()) == p["size"]
    assert {e["filename"]: e["size"] for e in p["exports"]} == {"song-karaoke.mp4": 5000, "song-mix.flac": 3000}
    assert st["disk"]["free"] > 0 and st["projects_size"] == p["size"]
    # one exported file, then the stems (the original stays)
    r = c.post(f"/api/projects/{h.project.id}/storage/clean", json={"exports": ["song-mix.flac"]}).json()
    assert r["freed"] == 3000 and [e["filename"] for e in r["projects"][0]["exports"]] == ["song-karaoke.mp4"]
    stems = r["projects"][0]["parts"]["stems"]
    r = c.post(f"/api/projects/{h.project.id}/storage/clean", json={"stems": True, "exports": True}).json()
    assert r["freed"] == stems + 5000
    assert [a.role for a in ws.get(h.project.id).project.audio] == ["original"]
    assert len(list((h.dir / "assets").iterdir())) == 1  # the stems' files went with them
    assert r["projects"][0]["parts"]["stems"] == 0 and not r["projects"][0]["stems"]


def test_a_busy_project_is_left_alone(tmp_path, monkeypatch):
    c, ws, h = _setup(tmp_path, monkeypatch)
    jm = c.app.state.jobs
    import threading

    go = threading.Event()
    jm.submit("burn", lambda job: go.wait(5), project_id=h.project.id, heavy=False)
    try:
        assert c.get("/api/storage").json()["projects"][0]["busy"]
        r = c.post(f"/api/projects/{h.project.id}/storage/clean", json={"exports": True})
        assert r.status_code == 409 and (h.dir / "exports" / "song-karaoke.mp4").exists()
        assert c.post("/api/storage/clean", json={"cache": True}).status_code == 409
    finally:
        go.set()


def test_leftovers_and_cache(tmp_path, monkeypatch):
    c, ws, h = _setup(tmp_path, monkeypatch)
    root = ws.root
    # an audio file nothing points to, a folder without a project file, a finished task's upload
    stray = h.dir / "assets" / ("0" * 64 + ".flac")
    stray.write_bytes(b"x" * 700)
    _age(stray)
    fresh = h.dir / "assets" / ("1" * 64 + ".flac")
    fresh.write_bytes(b"y" * 10)  # just written (an upload in progress): left alone
    orphan = root / "pdeadbeef01"
    (orphan / "exports").mkdir(parents=True)
    (orphan / "exports" / "a.mp4").write_bytes(b"z" * 300)
    _age(orphan)
    upload = root / ".tasks" / "tgone123456"
    upload.mkdir(parents=True)
    (upload / "a.mp4").write_bytes(b"u" * 200)
    _age(upload)
    (root / ".deleted-pold-x").mkdir()
    (root / ".deleted-pold-x" / "f").write_bytes(b"d" * 100)
    cache = tmp_path / "home" / "cache" / "playback"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / ("2" * 64 + ".wav")).write_bytes(b"c" * 400)

    st = c.get("/api/storage").json()
    assert st["leftovers"]["size"] == 700 + 300 + 200 + 100
    assert st["leftovers"]["parts"] == {"asset": 700, "folder": 300, "upload": 200, "deleted": 100}
    assert st["projects"][0]["parts"]["unused"] == 700
    assert st["cache"]["size"] >= 400 and st["cache"]["parts"]["playback"] >= 400
    r = c.post("/api/storage/clean", json={"leftovers": True, "cache": True}).json()
    assert r["freed"] >= 1300 + 400 and r["leftovers"]["size"] == 0 and r["cache"]["size"] == 0
    assert not stray.exists() and fresh.exists() and not orphan.exists() and not upload.exists()
    assert (h.dir / "project.json").exists() and len(ws.get(h.project.id).project.audio) == 3


def test_deleting_a_project_drops_its_cache(tmp_path, monkeypatch):
    c, ws, h = _setup(tmp_path, monkeypatch)
    other = ws.create("other", "plain")
    S.add_audio(other, _wav(tmp_path / "a.wav"), "original")  # the same song in another project
    mine = h.project.asset("vocals")
    shared = h.project.asset("original")
    for a in (mine, shared):
        c.get(f"/api/projects/{h.project.id}/audio/{a.id}/playback.wav")
    playback = tmp_path / "home" / "cache" / "playback"
    assert (playback / f"{mine.sha256}.wav").exists()
    assert c.delete(f"/api/projects/{h.project.id}").json() == {"ok": True}
    assert not (playback / f"{mine.sha256}.wav").exists()
    assert (playback / f"{shared.sha256}.wav").exists()  # still used by the other project


def test_replaced_files_are_not_left_behind(tmp_path, monkeypatch):
    monkeypatch.setenv("KARA_ALIGN_HOME", str(tmp_path / "home"))
    h = S.create_dir(tmp_path / "proj", "k", "plain")
    first = S.add_audio(h, _wav(tmp_path / "a.wav"), "original")
    S.add_audio(h, _wav(tmp_path / "v.wav", seed=1), "vocals")
    S.add_audio(h, _wav(tmp_path / "b.wav", seed=3), "original")  # another song: the old file goes
    assert not (h.dir / first.path).exists()
    assert len(list((h.dir / "assets").iterdir())) == 2  # the new original + the vocals (kept, marked as old)
    again = S.add_audio(h, _wav(tmp_path / "b.wav", seed=3), "original")  # the same file again: kept
    assert (h.dir / again.path).exists()
