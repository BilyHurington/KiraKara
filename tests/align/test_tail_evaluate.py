import numpy as np

from kara_align.align.evaluate import evaluate
from kara_align.align.tail import apply_tail
from kara_align.models import (
    AlignConfig,
    AlignmentResult,
    BackendInfo,
    InputSnapshot,
    LineTiming,
    ManualEdit,
    TailConfig,
    UnitTiming,
)


def U(uid, s, e, line="L1", locked=False):
    u = UnitTiming(unit_id=uid, line_id=line, segment_id="s", reading=uid, start_ms=s, end_ms=e,
                   model_start_ms=s, model_end_ms=e)
    if locked:
        u.manual = ManualEdit(start_ms=s, end_ms=e)
    return u


def env_with(on_ranges, n=300, hop=10):
    env = np.full(n, -70.0)
    for s, e in on_ranges:
        env[s // hop: e // hop] = -12.0
    return env, float(hop)


def test_tail_off_does_nothing():
    units = [U("a", 0, 100), U("b", 100, 400)]
    assert apply_tail(units, TailConfig(strategy="off"), env_with([(0, 900)])) == []
    assert units[1].end_ms == 400 and units[1].tail is None


def test_energy_extend_bounded_by_next_line():
    units = [U("a", 0, 200), U("b", 200, 400), U("c", 1000, 1200, line="L2")]
    apply_tail(units, TailConfig(strategy="energy", max_extend_ms=800), env_with([(0, 700), (1000, 1200)]))
    assert units[1].end_ms == 700
    assert units[1].tail.original_end_ms == 400 and units[1].tail.method == "energy:energy"


def test_energy_unresolved_keeps_and_flags():
    units = [U("a", 0, 200), U("b", 200, 400), U("c", 600, 800, line="L2")]
    issues = apply_tail(units, TailConfig(strategy="energy"), env_with([(0, 2900)]))
    assert units[1].end_ms == 400  # voicing continues into the next line: not extended blindly
    assert "tail_unresolved" in units[1].flags
    assert any(i.code == "tail_unresolved" for i in issues)


def test_trim_only_shortens_and_respects_lock():
    units = [U("a", 0, 200), U("b", 200, 900)]
    apply_tail(units, TailConfig(strategy="trim"), env_with([(0, 500)]))
    assert units[1].end_ms == 500 and units[1].tail.original_end_ms == 900
    locked = [U("a", 0, 200), U("b", 200, 900, locked=True)]
    apply_tail(locked, TailConfig(strategy="trim"), env_with([(0, 500)]))
    assert locked[1].end_ms == 900 and locked[1].tail is None


def test_trim_without_energy_caps_duration():
    units = [U("a", 0, 100), U("b", 100, 200), U("c", 200, 1500)]
    apply_tail(units, TailConfig(strategy="trim", max_trim_ms=600), None)
    assert units[2].end_ms == 900 and units[2].tail.method == "trim:duration_cap"


def _result(units):
    return AlignmentResult(
        mode="plain", backend=BackendInfo(name="t", model_id="m", profile="p", sample_rate=16000),
        config=AlignConfig(),
        snapshot=InputSnapshot(mode="plain", lyrics_text_revision="x", lyrics_reading_revision="y",
                               audio_asset_id="a", audio_sha256="0", audio_role="original", line_ids=["L1"],
                               config_hash="c"),
        units=units, lines=[LineTiming(line_id="L1", start_ms=units[0].start_ms, end_ms=units[-1].end_ms)],
    )


def test_evaluate_metrics_json_and_csv():
    ref = _result([U("a", 0, 100), U("b", 100, 200), U("c", 200, 300)])
    hyp_units = [U("a", 20, 100), U("b", 600, 700), U("c", None, None)]
    hyp_units[0].manual = ManualEdit(start_ms=20, end_ms=100)
    hyp = _result(hyp_units)
    rep = evaluate(hyp, ref, gross_ms=300)
    u = rep["units"]
    assert u["onset"]["n"] == 2 and u["onset"]["gross"] == 1 and u["missing_in_hypothesis"] == 1
    assert u["onset"]["mae_ms"] == (20 + 500) / 2
    assert rep["manual_corrections"] == 1
    csv_text = "id,start_ms,end_ms,level\na,0,100,unit\nb,100,200,unit\nL1,0,300,line\n"
    rep2 = evaluate(hyp, csv_text)
    assert rep2["units"]["onset"]["n"] == 2
    assert rep2["lines"]["onset"]["n"] == 1
    rep3 = evaluate(hyp, ref.model_dump(), match="order")
    assert rep3["units"]["onset"]["n"] == 2
