"""Time coordinate helpers.

Internal coordinates are samples (at a known rate) or model frames.  Public
times are integer ms on the *original* audio timeline.  Conversion to ms only
happens at output time via :func:`samples_to_ms`.
"""

from __future__ import annotations

from dataclasses import dataclass


def samples_to_ms(samples: float, sample_rate: int) -> int:
    """Round a sample position to integer ms (half-up, deterministic)."""
    return int((samples * 1000.0 / sample_rate) + 0.5) if samples >= 0 else -int((-samples * 1000.0 / sample_rate) + 0.5)


def ms_to_samples(ms: float, sample_rate: int) -> int:
    return int(round(ms * sample_rate / 1000.0))


@dataclass(frozen=True)
class FrameMap:
    """Maps model frames of an emission matrix to samples of the analysed audio.

    Frame ``t`` covers samples ``[offset + t*hop, offset + (t+1)*hop)`` of the
    *analysed* signal (after resampling).  ``origin_samples`` is where sample 0
    of the analysed signal lies on the original timeline, in analysed-rate
    samples (non-zero for stems with known padding / for sliced emissions).
    Each backend constructs its own FrameMap; nothing hard-codes 20 ms.
    """

    sample_rate: int
    hop_samples: int
    offset_samples: int = 0
    origin_samples: int = 0

    def frame_start_samples(self, t: float) -> float:
        return self.origin_samples + self.offset_samples + t * self.hop_samples

    def frame_start_ms(self, t: float) -> int:
        return samples_to_ms(self.frame_start_samples(t), self.sample_rate)

    def frame_time_ms_float(self, t: float) -> float:
        return self.frame_start_samples(t) * 1000.0 / self.sample_rate

    def ms_to_frame(self, ms: float) -> float:
        """Fractional frame index for an original-timeline time."""
        samples = ms * self.sample_rate / 1000.0
        return (samples - self.origin_samples - self.offset_samples) / self.hop_samples

    def shifted(self, frames: int) -> "FrameMap":
        """FrameMap for a slice starting at frame ``frames`` of this one."""
        return FrameMap(self.sample_rate, self.hop_samples,
                        self.offset_samples + frames * self.hop_samples, self.origin_samples)

    @property
    def frame_ms(self) -> float:
        return self.hop_samples * 1000.0 / self.sample_rate
