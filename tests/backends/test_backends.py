import builtins
import json
import os

import numpy as np
import pytest

from kara_align.align import backends as B
from kara_align.align.backends.chunking import (chunk_plan, chunked_forward, conv_output_length,
                                                receptive_field)
from kara_align.align.backends.fake import ScriptedBackend
from kara_align.align.backends.wav2vec2_ctc import tokenize_chars
from kara_align.align.emission_cache import EmissionCache, emission_cache_key
from kara_align.interfaces import CancelToken, Cancelled
from kara_align.models import AlignConfig, BackendInfo

KERNELS = [10, 3, 3, 3, 3, 2, 2]
STRIDES = [5, 2, 2, 2, 2, 2, 2]
HOP, RF = 320, 400


def nframes(n):
    return conv_output_length(n, KERNELS, STRIDES)


# -- chunking arithmetic ----------------------------------------------------

def test_receptive_field_and_lengths():
    assert receptive_field(KERNELS, STRIDES) == RF
    assert int(np.prod(STRIDES)) == HOP
    assert nframes(399) == 0
    assert nframes(400) == 1
    assert nframes(16000) == 49
    for n in [400, 719, 720, 16000, 12345]:
        assert nframes(n) == (n - RF) // HOP + 1


def _position_forward(seg_start_holder):
    """Synthetic model: frame j of a segment encodes its absolute first sample."""
    def forward(seg):
        # the segment carries its absolute sample index as the signal value
        n = nframes(len(seg))
        starts = seg[np.arange(n) * HOP]
        ends = seg[np.arange(n) * HOP + RF - 1]
        return np.stack([starts, ends], axis=1).astype(np.float32)
    return forward


@pytest.mark.parametrize("n", [400, 5000, 16000 * 7 + 123, 16000 * 61 + 7])
@pytest.mark.parametrize("chunk,ctx", [(16000, 0), (16000, 3000), (20 * 16000, 3 * 16000), (1000, 50)])
def test_chunked_frames_contiguous(n, chunk, ctx):
    audio = np.arange(n, dtype=np.float64)  # value == absolute sample index
    out = chunked_forward(audio, _position_forward(None), hop=HOP, rf=RF, n_frames=nframes,
                          chunk_samples=chunk, context_samples=ctx)
    total = nframes(n)
    assert out.shape[0] == total
    # every global frame g must come from samples [g*hop, g*hop+rf)
    np.testing.assert_array_equal(out[:, 0], np.arange(total) * HOP)
    np.testing.assert_array_equal(out[:, 1], np.arange(total) * HOP + RF - 1)


def test_chunk_plan_covers_once():
    plan = chunk_plan(16000 * 50, HOP, RF, nframes, 16000 * 20, 16000 * 3)
    frames = [f for f0, f1, *_ in plan for f in range(f0, f1)]
    assert frames == list(range(nframes(16000 * 50)))
    for f0, f1, s0, s1 in plan:
        assert s0 % HOP == 0 and s0 <= f0 * HOP and (f1 - 1) * HOP + RF <= s1


def test_chunked_forward_cancel_and_progress():
    seen = []
    tok = CancelToken()

    def fwd(seg):
        tok.cancel()
        return np.zeros((nframes(len(seg)), 2), np.float32)

    with pytest.raises(Cancelled):
        chunked_forward(np.zeros(16000 * 5, np.float32), fwd, hop=HOP, rf=RF, n_frames=nframes,
                        chunk_samples=16000, context_samples=0, cancel=tok, progress=seen.append)
    assert seen == [pytest.approx(1 / 5)]


def test_chunked_forward_short_audio():
    out = chunked_forward(np.zeros(100, np.float32), lambda s: np.zeros((max(nframes(len(s)), 1), 3)),
                          hop=HOP, rf=RF, n_frames=nframes, chunk_samples=16000, context_samples=0)
    assert out.shape == (0, 3)


# -- tokenization -------------------------------------------------------------

def test_tokenize_unknown_chars_reported():
    vocab = {"|": 0, "a": 1, "k": 11, "i": 9, "'": 27, "[PAD]": 29, "[UNK]": 28}
    toks = tokenize_chars(["u1", "u2", "u3"], ["Ki", "kā", "a i"], vocab, {"[PAD]", "[UNK]", "|"})
    assert toks[0].token_ids == [11, 9] and toks[0].unknown == []
    assert toks[1].token_ids == [11] and toks[1].unknown == ["ā"]
    assert toks[2].token_ids == [1, 9]  # whitespace skipped, not reported
    assert tokenize_chars(["x"], ["|"], vocab, {"|"})[0].unknown == ["|"]


# -- scripted backend -----------------------------------------------------------

def test_scripted_backend_deterministic():
    script = [("kimi", 500, 900), ("to", 1000, 1200)]
    b1 = ScriptedBackend(script, noise=0.3, seed=7)
    b2 = ScriptedBackend(script, noise=0.3, seed=7)
    audio = np.zeros(16000 * 2, np.float32)
    e1, e2 = b1.emissions(audio), b2.emissions(audio)
    np.testing.assert_array_equal(e1.logp, e2.logp)
    assert e1.logp.shape == (100, 28)
    np.testing.assert_allclose(np.exp(e1.logp).sum(1), 1.0, rtol=1e-5)
    clean = ScriptedBackend(script).emissions(audio)
    arg = clean.logp.argmax(1)
    assert arg[:25].tolist() == [0] * 25
    assert arg[25] == 11  # 'k' starts at 500 ms == frame 25
    assert clean.frame_map.frame_start_ms(25) == 500
    toks = b1.tokenize(["a", "b"], ["ki", "n?"])
    assert toks[0].token_ids == [11, 9] and toks[1].unknown == ["?"]


def test_scripted_origin_offset():
    e = ScriptedBackend([("a", 1000, 1100)]).emissions(np.zeros(16000), origin_samples=8000)
    assert e.frame_map.frame_start_ms(0) == 500
    assert e.logp.argmax(1)[25] == 1  # 1000 ms original -> frame 25 of the stem


# -- registry --------------------------------------------------------------------

def test_registry_lists_and_builds_scripted():
    names = {b["name"] for b in B.list_backends()}
    assert {"mms-ja", "wav2vec2-ctc", "scripted"} <= names
    mms = next(b for b in B.list_backends() if b["name"] == "mms-ja")
    assert mms["default_revision"] and mms["license"]
    be = B.get_backend(AlignConfig(backend="scripted"))
    assert isinstance(be, ScriptedBackend)
    with pytest.raises(ValueError, match="未知的对齐后端"):
        B.get_backend(AlignConfig(backend="nope"))


def test_registry_error_without_torch(monkeypatch):
    import importlib.util as iu
    real = iu.find_spec
    monkeypatch.setattr(iu, "find_spec", lambda name, *a, **k: None if name in ("torch", "transformers") else real(name, *a, **k))
    with pytest.raises(RuntimeError, match=r"milikara\[ml\]"):
        B.get_backend(AlignConfig(backend="mms-ja"))
    mms = next(b for b in B.list_backends() if b["name"] == "mms-ja")
    assert mms["available"] is False and "torch" in mms["missing"]


def test_import_ml_error_message(monkeypatch):
    from kara_align.align.backends import wav2vec2_ctc as W
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name in ("torch", "transformers"):
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match=r"pip install 'milikara\[ml\]'"):
        W._import_ml()


# -- emission cache -----------------------------------------------------------------

def _info(**kw):
    base = dict(name="mms-ja", model_id="m", model_revision="r1", profile="ja-hepburn", sample_rate=16000,
                frame_hop_samples=320, extra={"device": "cpu"})
    base.update(kw)
    return BackendInfo(**base)


def test_cache_key_sensitivity():
    k = lambda **kw: emission_cache_key(kw.pop("sha", "abc"), kw.pop("role", "original"), kw.pop("origin", 0),
                                        kw.pop("info", _info()), kw.pop("chunk", 20.0), kw.pop("ctx", 3.0),
                                        kw.pop("rs", "soxr16k"))
    base = k()
    assert base == k()
    assert base == k(info=_info(extra={"device": "mps", "notes": ["x"]}))  # device doesn't matter
    for variant in [k(sha="abd"), k(role="vocals"), k(origin=10), k(info=_info(model_id="m2")),
                    k(info=_info(model_revision="r2")), k(info=_info(profile="other")), k(chunk=10.0),
                    k(ctx=2.0), k(rs="poly")]:
        assert variant != base


def test_cache_roundtrip_and_refuse_partial(tmp_path):
    cache = EmissionCache(tmp_path / "em")
    e = ScriptedBackend([("ka", 100, 300)]).emissions(np.zeros(16000, np.float32), origin_samples=640)
    with pytest.raises(ValueError):
        cache.put("k1", e, complete=False)
    assert cache.get("k1") is None
    cache.put("k1", e, _info(), complete=True)
    got = cache.get("k1")
    np.testing.assert_array_equal(got.logp, e.logp)
    assert got.logp.dtype == np.float32
    assert got.frame_map == e.frame_map and got.blank_id == 0 and got.cache_key == "k1"
    assert cache.size_bytes() > 0
    assert not list((tmp_path / "em").rglob("*.tmp"))
    cache.clear()
    assert cache.get("k1") is None and cache.size_bytes() == 0


def test_cache_ignores_incomplete_or_corrupt(tmp_path):
    cache = EmissionCache(tmp_path)
    e = ScriptedBackend().emissions(np.zeros(3200, np.float32))
    cache.put("kk", e, complete=True)
    npz, meta = cache._paths("kk")
    m = json.loads(meta.read_text())
    m["complete"] = False
    meta.write_text(json.dumps(m))
    assert cache.get("kk") is None
    cache.put("kk", e, complete=True)
    npz.write_bytes(b"garbage")
    assert cache.get("kk") is None
    cache.put("kk", e, complete=True)
    meta.unlink()  # array without meta == crashed write
    assert cache.get("kk") is None


# -- real weights --------------------------------------------------------------------

@pytest.mark.ml
def test_mms_chunked_matches_unchunked():
    from kara_align.align.backends.wav2vec2_ctc import MMSJapaneseBackend
    rng = np.random.default_rng(0)
    t = np.arange(16000 * 6) / 16000
    audio = (0.3 * np.sin(2 * np.pi * 220 * t) * (1 + np.sin(2 * np.pi * 1.5 * t)) +
             0.02 * rng.normal(size=t.size)).astype(np.float32)
    whole = MMSJapaneseBackend(device="cpu", chunk_s=100, context_s=0).emissions(audio)
    chunked = MMSJapaneseBackend(device="cpu", chunk_s=2.0, context_s=2.0).emissions(audio)
    assert whole.logp.shape == chunked.logp.shape == (nframes(audio.size), 32)
    # identical frame grid; values close (context limited to 2 s)
    diff = np.abs(np.exp(whole.logp) - np.exp(chunked.logp)).max(axis=1)
    assert np.median(diff) < 0.05
    assert (whole.logp.argmax(1) == chunked.logp.argmax(1)).mean() > 0.9


@pytest.mark.ml
def test_mms_tokenize_and_info():
    from kara_align.align.backends.wav2vec2_ctc import DEFAULT_REVISION, MMSJapaneseBackend
    be = MMSJapaneseBackend(device="cpu")
    toks = be.tokenize(["u1", "u2"], ["kyo", "n-"])
    assert toks[0].unknown == [] and len(toks[0].token_ids) == 3
    assert toks[1].unknown == ["-"]
    info = be.info()
    assert info.model_revision == DEFAULT_REVISION and info.frame_hop_samples == 320
    assert info.license == "cc-by-nc-sa-4.0"
    assert be.hop_samples == 320 and be.sample_rate == 16000


def test_backend_info_names_the_device_without_loading_the_model():
    pytest.importorskip("torch")
    from kara_align.align.backends.wav2vec2_ctc import MMSJapaneseBackend

    b = MMSJapaneseBackend(device="cpu")
    assert b.info().extra["device"] == "cpu" and b._entry is None  # recorded in results, nothing loaded
    assert MMSJapaneseBackend(device="auto").info().extra["device"] in ("cpu", "mps", "cuda")
