"""Vocal-keep mixing (design §5).

    mix(t) = master × [(p/100)·V(t) + (q/100)·I(t)]

``p`` is the vocal *keep* percentage ("人声保留 p%"), ``q`` the instrumental
percentage (default 100).  Both are linear amplitude factors, not loudness.
Anti-clipping uses one visible common bus gain for the whole mix so the V/I
ratio never changes.  The browser preview uses exactly the same rule
(:data:`MIX_RULE_JS`); monitor volume is never part of an export.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal, Optional

import numpy as np

from .io import file_sha256, load_audio, write_wav

FORMULA = "mix(t) = master * bus_gain * [(p/100)*V(t) + (q/100)*I(t)]"

MIX_RULE_JS = """\
Identical rule for the WebUI (Web Audio):
  vocalGain.gain.value        = p / 100      // p in [0,100], linear amplitude
  instrumentalGain.gain.value = q / 100      // q in [0,100], default 100
  busGain.gain.value          = master * busGain   // one common bus gain
  busGain = limiter == 'normalize_peak' ? min(1, ceiling / peak(p,q,master)) : 1
  peak is computed on the exact un-limited mix (server /mix/peak or client-side scan),
  so preview and export apply the same bus gain; the monitor volume is a separate node
  after the bus and is never exported. Both stems start on the same clock at the same
  original-time offset; the shorter stem is silent (zero-padded) past its end.
"""

Limiter = Literal["none", "normalize_peak"]


class MixError(Exception):
    pass


@dataclass
class MixReport:
    p: float
    q: float
    master: float
    limiter: str
    ceiling_dbfs: float
    bus_gain: float
    peak_before: float
    peak_after: float
    clipped_samples: int
    formula: str = FORMULA
    num_samples: int = 0
    sample_rate: int = 0
    notes: list[str] = field(default_factory=list)
    tracks: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _check_pct(name: str, v: float) -> float:
    v = float(v)
    if not np.isfinite(v) or v < 0 or v > 100:
        raise MixError(f"{name} must be within 0–100 (got {v})")
    return v


def _as2d(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    return x[None, :] if x.ndim == 1 else x


def _match_channels(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if a.shape[0] == b.shape[0]:
        return a, b
    if a.shape[0] == 1:
        return np.repeat(a, b.shape[0], axis=0), b
    if b.shape[0] == 1:
        return a, np.repeat(b, a.shape[0], axis=0)
    raise MixError(f"incompatible channel counts {a.shape[0]} and {b.shape[0]}")


def pad_end(x: np.ndarray, n: int) -> np.ndarray:
    """Zero-pad at the END only (keeps the time origin); never stretch."""
    if x.shape[-1] >= n:
        return x
    return np.pad(x, [(0, 0)] * (x.ndim - 1) + [(0, n - x.shape[-1])])


def mix_stems(vocals: Optional[np.ndarray], instrumental: Optional[np.ndarray], sr: int,
              p: float, q: float = 100.0, master: float = 1.0, limiter: Limiter = "normalize_peak",
              ceiling_dbfs: float = -0.3, length: Optional[int] = None) -> tuple[np.ndarray, MixReport]:
    """Mix two stems that share the original timeline. Returns ``([ch, n], report)``."""
    if vocals is None or instrumental is None:
        raise MixError("vocal-keep mixing needs both a vocals stem and an instrumental stem; "
                       "the vocals inside a full mix cannot be reduced independently")
    p = _check_pct("vocal keep %", p)
    q = _check_pct("instrumental %", q)
    master = float(master)
    if not np.isfinite(master) or master < 0:
        raise MixError("master must be a non-negative number")
    if limiter not in ("none", "normalize_peak"):
        raise MixError(f"unknown limiter {limiter}")
    v, i = _match_channels(_as2d(vocals), _as2d(instrumental))
    notes = []
    n = max(v.shape[1], i.shape[1]) if length is None else int(length)
    if v.shape[1] != i.shape[1]:
        notes.append(f"stem lengths differ ({v.shape[1]} vs {i.shape[1]} samples); shorter one zero-padded at end")
    v = pad_end(v, n)[:, :n]
    i = pad_end(i, n)[:, :n]
    mix = master * ((p / 100.0) * v.astype(np.float64) + (q / 100.0) * i.astype(np.float64))
    peak_before = float(np.max(np.abs(mix))) if mix.size else 0.0
    ceiling = 10 ** (ceiling_dbfs / 20.0)
    bus_gain = 1.0
    if limiter == "normalize_peak" and peak_before > ceiling:
        bus_gain = ceiling / peak_before
    mix *= bus_gain
    peak_after = float(np.max(np.abs(mix))) if mix.size else 0.0
    clipped = int(np.count_nonzero(np.abs(mix) > 1.0))
    if clipped:
        notes.append(f"{clipped} samples exceed full scale (limiter={limiter})")
    report = MixReport(p=p, q=q, master=master, limiter=limiter, ceiling_dbfs=ceiling_dbfs,
                       bus_gain=float(bus_gain), peak_before=peak_before, peak_after=peak_after,
                       clipped_samples=clipped, num_samples=n, sample_rate=int(sr), notes=notes)
    return mix.astype(np.float32), report


def export_mix_wav(vocals_path, instrumental_path, out_path, p: float, q: float = 100.0,
                   master: float = 1.0, limiter: Limiter = "normalize_peak", ceiling_dbfs: float = -0.3,
                   original_num_samples: Optional[int] = None, subtype: str = "PCM_16") -> MixReport:
    """Render the mix at normal speed, keeping the original duration and origin.

    ``original_num_samples`` (at the stems' rate) fixes the output length to
    the original's; otherwise the longer stem's length is used.
    """
    if vocals_path is None or instrumental_path is None:
        raise MixError("vocal-keep export needs both vocals and instrumental stems")
    v, sr_v = load_audio(vocals_path)
    i, sr_i = load_audio(instrumental_path)
    if sr_v != sr_i:
        raise MixError(f"stem sample rates differ ({sr_v} vs {sr_i}); resample explicitly before mixing")
    mix, report = mix_stems(v, i, sr_v, p, q, master, limiter, ceiling_dbfs, length=original_num_samples)
    write_wav(out_path, mix, sr_v, subtype=subtype)
    report.tracks = {
        "vocals": {"path": str(vocals_path), "sha256": file_sha256(vocals_path), "gain": p / 100.0},
        "instrumental": {"path": str(instrumental_path), "sha256": file_sha256(instrumental_path), "gain": q / 100.0},
        "output": {"path": str(Path(out_path)), "sha256": file_sha256(out_path), "subtype": subtype},
    }
    return report
