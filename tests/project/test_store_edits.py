import json
import time
import zipfile

import pytest

from kara_align.interfaces import Cancelled
from kara_align.models import (
    AlignConfig, AlignmentResult, AudioAsset, BackendInfo, Coverage, InputSnapshot, Line, LineTiming,
    LyricsDoc, Project, Segment, Unit, UnitTiming,
)
from kara_align.project import edits, exports, store
from kara_align.project.jobs import JobManager, progress_setter


def _project() -> Project:
    units = [Unit(id="u1", reading="き"), Unit(id="u2", reading="み")]
    seg1 = Segment(id="s1", surface="君", reading="きみ", units=units)
    seg2 = Segment(id="s2", surface="と", reading="と", units=[Unit(id="u3", reading="と", surface="と")])
    doc = LyricsDoc(lines=[Line(id="L0001", text="君と", segments=[seg1, seg2], imported_start_ms=1000)])
    return Project(name="t", lyrics=doc)


def _result() -> AlignmentResult:
    snap = InputSnapshot(mode="plain", lyrics_text_revision="a", lyrics_reading_revision="b",
                         audio_asset_id="a1", audio_sha256="x", audio_role="original",
                         line_ids=["L0001"], config_hash="c")
    units = [
        UnitTiming(unit_id="u1", line_id="L0001", segment_id="s1", reading="き", start_ms=1000, end_ms=1200,
                   model_start_ms=1000, model_end_ms=1200),
        UnitTiming(unit_id="u2", line_id="L0001", segment_id="s1", reading="み", start_ms=1200, end_ms=1400,
                   model_start_ms=1200, model_end_ms=1400),
        UnitTiming(unit_id="u3", line_id="L0001", segment_id="s2", reading="と", status="failed",
                   reason="no path"),
    ]
    return AlignmentResult(mode="plain", backend=BackendInfo(name="t", model_id="m", profile="p", sample_rate=16000),
                           config=AlignConfig(), snapshot=snap, units=units,
                           lines=[LineTiming(line_id="L0001", start_ms=1000, end_ms=1400)], coverage=Coverage())


def test_save_load_roundtrip(tmp_path):
    p = _project()
    store.save_project(p, tmp_path)
    q = store.load_project(tmp_path)
    assert q.lyrics.lines[0].segments[0].units[1].reading == "み"
    assert q.model_dump() == p.model_dump() | {"updated": q.updated}


def test_parse_rejects_foreign_json():
    with pytest.raises(store.ProjectError):
        store.parse_project_json(json.dumps({"format": "other", "version": 1}))
    with pytest.raises(store.ProjectError):
        store.parse_project_json(json.dumps({"format": "kara-align/project", "version": 999}))


def test_package_roundtrip_and_missing_audio(tmp_path):
    p = _project()
    src = tmp_path / "src"
    (src / "assets").mkdir(parents=True)
    (src / "assets" / "abc.wav").write_bytes(b"RIFF....")
    p.audio.append(AudioAsset(role="original", sha256="abc", path="assets/abc.wav", duration_ms=1,
                              sample_rate=16000, channels=1, num_samples=16))
    store.save_project(p, src)
    z = store.export_package(p, src, tmp_path / "p.kara.zip")
    q = store.import_package(z, tmp_path / "dst")
    assert q.audio[0].path == "assets/abc.wav"
    z2 = store.export_package(p, src, tmp_path / "noaudio.zip", include_audio=False)
    q2 = store.import_package(z2, tmp_path / "dst2")
    assert q2.audio[0].path is None  # must be re-uploaded


def test_package_rejects_traversal(tmp_path):
    z = tmp_path / "bad.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("project.json", _project().model_dump_json())
        zf.writestr("../evil.txt", "x")
    with pytest.raises(store.ProjectError):
        store.import_package(z, tmp_path / "out")


def test_manual_edit_keeps_model_prediction_and_history():
    r = _result()
    edits.set_manual(r, "u1", 1010, 1190)
    u1 = r.units[0]
    assert (u1.start_ms, u1.end_ms) == (1010, 1190)
    assert (u1.model_start_ms, u1.model_end_ms) == (1000, 1200)
    assert u1.locked
    edits.clear_manual(r, "u1")
    assert (u1.start_ms, u1.end_ms) == (1000, 1200)
    assert len(u1.manual_history) == 2
    with pytest.raises(edits.EditError):
        edits.set_manual(r, "u1", 1200, 1100)


def test_manual_resolves_failed_unit_and_line_refresh():
    r = _result()
    edits.set_manual(r, "u3", 1400, 1600)
    assert r.units[2].status == "ok"
    assert r.lines[0].end_ms == 1600
    edits.restore_manual(r, "u3", None)
    assert r.units[2].start_ms is None


def test_exports_lrc_and_csv():
    p = _project()
    r = _result()
    line = exports.export(p, "lrc-line", r)
    assert "[00:01.00]君と" in line.content
    unit = exports.export(p, "lrc-unit", r)
    # kanji segment is one chunk; failed unit's text joins previous tag
    assert "[00:01.00]<00:01.00>君と<00:01.40>" in unit.content
    assert unit.warnings
    csv_out = exports.export(p, "csv", r)
    assert "u3" in csv_out.content and "failed" in csv_out.content
    full = json.loads(exports.export(p, "alignment", r).content)
    assert full["lyrics"]["lines"][0]["text"] == "君と"
    assert exports.fmt_lrc_ts(61235) == "01:01.24"


def test_jobs_success_failure_cancel():
    jm = JobManager()
    ok = jm.submit("t", lambda job: 42)
    bad = jm.submit("t", lambda job: 1 / 0)

    def slow(job):
        set_p = progress_setter(job)
        for i in range(200):
            time.sleep(0.01)
            set_p(i / 200)
        return "done"

    c = jm.submit("t", slow)
    time.sleep(0.2)
    jm.cancel(c.id)
    for _ in range(300):
        if all(j.status in ("succeeded", "failed", "cancelled") for j in (ok, bad, c)):
            break
        time.sleep(0.02)
    assert ok.status == "succeeded" and ok.output == 42
    assert bad.status == "failed" and "ZeroDivisionError" in bad.error
    assert c.status == "cancelled" and c.output is None
    jm.shutdown()


def test_save_refuses_invalid_project_data(tmp_path):
    p = _project()
    store.save_project(p, tmp_path)
    before = (tmp_path / "project.json").read_text()
    p.calibration = (p.calibration, [])  # a bug that assigns the wrong type
    with pytest.raises(store.ProjectError):
        store.save_project(p, tmp_path)
    assert (tmp_path / "project.json").read_text() == before  # file untouched
