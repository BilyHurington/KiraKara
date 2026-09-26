import numpy as np
import pytest

from kara_align.align.runner import AlignmentInputError, run_alignment
from kara_align.interfaces import CancelToken, Cancelled
from kara_align.models import AlignConfig, Calibration, ManualEdit, TailConfig

from .helpers import make_doc, make_emission, inputs

SCRIPT = [
    [("ki", 1000, 1300), ("mi", 1300, 1700), ("to", 1700, 2200)],
    [("a", 4000, 4300), ("ru", 4300, 4700), ("ki", 4700, 5200)],
    [("ki", 7000, 7300), ("mi", 7300, 7700), ("to", 7700, 8200)],  # repeated chorus text
]


def _doc(starts=None):
    return make_doc([[u for u, _, _ in line] for line in SCRIPT], starts=starts)


def _emission(noise=0.0):
    return make_emission([u for line in SCRIPT for u in line], 10000, noise=noise)


def _check_times(res, tol=40):
    got = [(u.start_ms, u.end_ms) for u in res.units]
    exp = [(s, e) for line in SCRIPT for _, s, e in line]
    for (gs, ge), (es, ee) in zip(got, exp):
        assert abs(gs - es) <= tol and abs(ge - ee) <= tol, (got, exp)


def test_plain_mode_end_to_end():
    doc = _doc()
    res = run_alignment(inputs(doc, _emission()))
    assert res.mode == "plain" and res.coverage.full
    assert len(res.units) == 9
    _check_times(res)
    # repeated chorus lines keep distinct unit ids and different times
    assert res.units[0].unit_id != res.units[6].unit_id
    assert res.lines[2].start_ms == pytest.approx(7000, abs=40)
    assert all(isinstance(u.start_ms, int) for u in res.units)
    assert res.snapshot.lyrics_text_revision == doc.text_revision()


def test_lrc_mode_end_to_end_with_offset():
    doc = _doc(starts=[500, 3500, 6500])  # LRC 500 ms early
    cal = Calibration(user_shift_ms=500, confirmed=True)
    res = run_alignment(inputs(doc, _emission(noise=0.3), mode="lrc", cal=cal))
    _check_times(res)
    lt = res.lines[1]
    assert lt.anchor_ms == 4000 and lt.anchor_kind == "soft"
    assert abs(lt.anchor_residual_ms) <= 40
    assert lt.window_ms is not None
    assert res.snapshot.calibration_hash


def test_lrc_invalid_anchor_raises():
    doc = _doc(starts=[500, 3500, 20000])
    with pytest.raises(AlignmentInputError) as e:
        run_alignment(inputs(doc, _emission(), mode="lrc"))
    assert e.value.issues[0].code == "anchor_out_of_range"


def test_untokenizable_unit_reported_not_invented():
    doc = _doc()
    doc.lines[1].segments[1].units[0].reading = "ル"  # not in vocab
    res = run_alignment(inputs(doc, _emission()))
    u = next(u for u in res.units if u.reading == "ル")
    assert u.status == "unaligned" and u.start_ms is None and u.end_ms is None and u.reason
    assert any(i.code == "untokenizable_unit" for i in res.issues)
    assert len(res.units) == 9


def test_manual_lock_preserved_and_local_rerun():
    doc = _doc()
    first = run_alignment(inputs(doc, _emission()))
    target = first.units[4]
    target.manual = ManualEdit(start_ms=4321, end_ms=4654, locked=True)
    target.manual_history = [target.manual]
    second = run_alignment(inputs(doc, _emission(), previous=first))
    u = next(x for x in second.units if x.unit_id == target.unit_id)
    assert (u.start_ms, u.end_ms) == (4321, 4654)
    assert u.model_start_ms is not None and u.model_start_ms != 4321
    assert "manual" in u.flags
    # local rerun of line 1 only
    lid = doc.lines[1].id
    part = run_alignment(inputs(doc, _emission(), previous=second, line_ids=[lid]))
    assert not part.coverage.full and part.coverage.line_ids == [lid]
    assert {u.line_id for u in part.units} == {lid}
    assert part.parent_result_id == second.id
    assert next(x for x in part.units if x.unit_id == target.unit_id).start_ms == 4321


def test_cancel():
    tok = CancelToken()
    tok.cancel()
    with pytest.raises(Cancelled):
        run_alignment(inputs(_doc(), _emission()), cancel=tok)


def test_missing_role_is_error_not_fallback():
    cfg = AlignConfig(audio_role="vocals")
    with pytest.raises(AlignmentInputError):
        run_alignment(inputs(_doc(), _emission(), config=cfg))


def test_retry_switches_audio_when_better():
    # original emission is garbage for line 1 region; vocals is clean
    doc = _doc(starts=[1000, 4000, 7000])
    bad_script = [u for i, line in enumerate(SCRIPT) for u in line if i != 1]
    orig = make_emission(bad_script, 10000)
    voc = _emission()
    cfg = AlignConfig()
    cfg.decode.soft_lambda = 0.0001
    res = run_alignment(inputs(doc, orig, mode="lrc", config=cfg, emissions={"original": orig, "vocals": voc}))
    tried = [x for x in res.stats["retry"] if x.get("line_id") == doc.lines[1].id]
    assert tried, res.issues
    assert any(t.startswith("audio:vocals") for t in tried[0]["tried"])


def test_tail_energy_applied():
    doc = _doc()
    env = np.full(500, -60.0)  # 20 ms hop
    for line in SCRIPT:
        s, e = line[0][1], line[-1][2]
        env[s // 20: e // 20 + 10] = -10.0  # vocal continues 200 ms past the model end
    cfg = AlignConfig(tail=TailConfig(strategy="energy", max_extend_ms=800))
    res = run_alignment(inputs(doc, _emission(), config=cfg, energy_for=lambda r: (env, 20.0)))
    last = [u for u in res.units if u.line_id == doc.lines[0].id][-1]
    assert last.tail is not None and last.tail.original_end_ms == last.model_end_ms
    assert last.end_ms == pytest.approx(2400, abs=40)
