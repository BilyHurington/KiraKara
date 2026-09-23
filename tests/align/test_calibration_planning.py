import pytest

from kara_align.align import calibration as calib
from kara_align.align.planning import plan_lrc, plan_plain
from kara_align.models import Calibration, DecodeConfig, LineAnchor
from kara_align.timebase import FrameMap

from .helpers import make_doc


def test_design_example_mark_recomputes_not_accumulates():
    doc = make_doc([["a"], ["b"]], starts=[12300, 15000])
    cal = Calibration()
    lid = doc.lines[0].id
    cal = calib.mark_first_onset(cal, doc, lid, 12950)
    assert cal.user_shift_ms == 650
    cal = calib.mark_first_onset(cal, doc, lid, 13000)
    assert cal.user_shift_ms == 700
    assert calib.effective_ms(doc, cal, doc.lines[1]) == 15700
    cal = calib.undo(cal)
    assert cal.user_shift_ms == 650


def test_embedded_offset_applied_once_and_manual_anchor_fixed():
    doc = make_doc([["a"], ["b"]], starts=[1000, 2000])
    doc.embedded_shift_ms = -500  # [offset:500] -> lyrics earlier
    doc.lines[1].anchor = LineAnchor(abs_ms=2600)
    cal = calib.set_user_shift(Calibration(), 100)
    assert calib.base_ms(doc, doc.lines[0]) == 500
    assert calib.effective_ms(doc, cal, doc.lines[0]) == 600
    assert calib.effective_line_starts(doc, cal)[doc.lines[1].id] == (2600, "hard")
    cal2 = calib.set_user_shift(cal, 900)
    assert calib.effective_ms(doc, cal2, doc.lines[1]) == 2600
    # marking relative to base with embedded shift
    cal3 = calib.mark_first_onset(Calibration(), doc, doc.lines[0].id, 700)
    assert cal3.user_shift_ms == 200


def test_confirm_zero_and_checks():
    doc = make_doc([["a"], ["b"], ["c"]], starts=[1000, 30000, 60000])
    cal = calib.confirm_zero(Calibration(user_shift_ms=40))
    assert cal.confirmed and cal.user_shift_ms == 0
    cal, issues = calib.add_check(cal, doc, doc.lines[2].id, 61500)
    assert issues and issues[0].code == "calibration_mismatch"
    assert cal.checks[0].residual_ms == 1500
    cal, issues = calib.add_check(cal, doc, doc.lines[1].id, 30050)
    assert [i.line_id for i in issues] == [doc.lines[2].id]
    # timeline is never stretched: shift unchanged
    assert cal.user_shift_ms == 0


def test_validate_anchors():
    doc = make_doc([["a"], ["b"], ["c"]], starts=[100, 5000, 4000])
    cal = Calibration(user_shift_ms=-200)
    codes = {i.code for i in calib.validate_anchors(doc, cal, 4500)}
    assert codes == {"anchor_negative", "anchor_out_of_range", "anchor_order_conflict"}
    doc2 = make_doc([["a"]])
    assert calib.validate_anchors(doc2, Calibration(), 1000)[0].code == "lrc_no_times"
    with pytest.raises(ValueError):
        calib.mark_first_onset(Calibration(), doc2, doc2.lines[0].id, 100)


def test_plain_plan_single_ordered_task_per_voice():
    doc = make_doc([["a"], ["b"], ["c"]])
    doc.lines[2].voice = "harmony"
    tasks = plan_plain(doc, 500)
    assert [(t.voice, t.participating_line_ids, t.start_frame, t.end_frame) for t in tasks] == [
        ("main", [doc.lines[0].id, doc.lines[1].id], 0, 500),
        ("harmony", [doc.lines[2].id], 0, 500),
    ]
    sub = plan_plain(doc, 500, [doc.lines[1].id])
    assert sub[0].retained_line_ids == [doc.lines[1].id]
    assert len(sub[0].participating_line_ids) == 2


def test_lrc_windows_and_groups():
    fm = FrameMap(16000, 320)
    doc = make_doc([["a"], ["b"], ["c"], ["d"]], starts=[2000, 6000, None, 12000])
    cfg = DecodeConfig(left_margin_ms=1000, right_margin_ms=1000, joint_context_lines=0)
    tasks = plan_lrc(doc, Calibration(), cfg, fm, 1000, 20000)
    ids = [ln.id for ln in doc.lines]
    assert [t.retained_line_ids for t in tasks] == [[ids[0]], [ids[1], ids[2]], [ids[3]]]
    # W0 = [2000-1000, 6000+1000] ms -> frames [50, 350]
    assert (tasks[0].start_frame, tasks[0].end_frame) == (50, 350)
    # unanchored line 2 joins line 1, bounded by next anchor (line 3)
    assert (tasks[1].start_frame, tasks[1].end_frame) == (250, 650)
    # last line bounded by audio end (clipped to frame count)
    assert tasks[2].end_frame == 1000
    assert tasks[1].kind == "joint"


def test_lrc_context_when_gap_unknown():
    fm = FrameMap(16000, 320)
    doc = make_doc([["a"], ["b"], ["c"]], starts=[2000, 4000, 6000])
    cfg = DecodeConfig(joint_context_lines=1, left_margin_ms=500, right_margin_ms=500)
    tasks = plan_lrc(doc, Calibration(), cfg, fm, 1000, 20000)
    mid = tasks[1]
    assert mid.retained_line_ids == [doc.lines[1].id]
    assert mid.participating_line_ids == [ln.id for ln in doc.lines]
    assert mid.context_line_ids == [doc.lines[0].id, doc.lines[2].id]
    # known wide gaps -> no context
    for ln, end in zip(doc.lines, [2500, 4500, 6500]):
        ln.imported_end_ms = end
    tasks = plan_lrc(doc, Calibration(), cfg, fm, 1000, 20000)
    assert tasks[1].participating_line_ids == [doc.lines[1].id]
