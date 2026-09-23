"""Chunked acoustic inference with exact frame bookkeeping (design §4.3).

Long audio is split into chunks independent of the lyrics / LRC.  Each chunk
is analysed with extra context on both sides and only its *centre* frames are
kept.  Chunk borders lie on multiples of the model hop, so every global frame
``g`` (covering samples ``[g*hop, g*hop + rf)``) is produced by exactly one
chunk: no duplicated and no missing frames at joins.

This module is torch-free so the arithmetic can be tested with a synthetic
``forward`` function.
"""

from __future__ import annotations

from typing import Callable, Optional

import numpy as np

Forward = Callable[[np.ndarray], np.ndarray]


def conv_output_length(length: int, kernels: list[int], strides: list[int]) -> int:
    """Number of frames of a stack of valid (unpadded) 1-D convolutions."""
    for k, s in zip(kernels, strides):
        if length < k:
            return 0
        length = (length - k) // s + 1
    return length


def receptive_field(kernels: list[int], strides: list[int]) -> int:
    """Samples needed to produce one output frame."""
    rf, jump = 1, 1
    for k, s in zip(kernels, strides):
        rf += (k - 1) * jump
        jump *= s
    return rf


def _round_up(x: int, m: int) -> int:
    return ((x + m - 1) // m) * m


def chunk_plan(num_samples: int, hop: int, rf: int, n_frames: Callable[[int], int],
               chunk_samples: int, context_samples: int) -> list[tuple[int, int, int, int]]:
    """Return ``(core_f0, core_f1, seg_start, seg_end)`` per chunk.

    ``core`` is a global frame range owned by the chunk; ``seg`` the sample
    range fed to the model (core + context, clipped to the signal).
    """
    total = n_frames(num_samples)
    if total <= 0:
        return []
    chunk_samples = max(hop, _round_up(int(chunk_samples), hop))
    context_samples = _round_up(max(0, int(context_samples)), hop)
    chunk_frames = chunk_samples // hop
    plan = []
    f0 = 0
    while f0 < total:
        f1 = min(total, f0 + chunk_frames)
        seg_start = max(0, f0 * hop - context_samples)
        seg_end = min(num_samples, (f1 - 1) * hop + rf + context_samples)
        plan.append((f0, f1, seg_start, seg_end))
        f0 = f1
    return plan


def chunked_forward(audio: np.ndarray, forward: Forward, *, hop: int, rf: int,
                    n_frames: Callable[[int], int], chunk_samples: int, context_samples: int,
                    cancel=None, progress: Optional[Callable[[float], None]] = None) -> np.ndarray:
    """Run ``forward`` over chunks and stitch the centre frames.

    ``forward(segment) -> [n_frames(len(segment)), V]``.  Frame ``j`` of a
    segment starting at sample ``s`` (a multiple of ``hop``) is global frame
    ``s // hop + j``.
    """
    n = int(audio.shape[0])
    plan = chunk_plan(n, hop, rf, n_frames, chunk_samples, context_samples)
    if not plan:
        probe = forward(np.zeros(rf, dtype=np.float32))
        return np.zeros((0, probe.shape[1]), dtype=np.float32)
    out: Optional[np.ndarray] = None
    total = plan[-1][1]
    for i, (f0, f1, s0, s1) in enumerate(plan):
        if cancel is not None:
            cancel.check()
        assert s0 % hop == 0
        seg_out = np.asarray(forward(audio[s0:s1]))
        base = s0 // hop
        j0, j1 = f0 - base, f1 - base
        if seg_out.shape[0] < j1:
            raise RuntimeError(
                f"模型对 {s1 - s0} 个样本的分块返回了 {seg_out.shape[0]} 帧，"
                f"需要 {j1} 帧（帧数计算不一致）")
        if out is None:
            out = np.empty((total, seg_out.shape[1]), dtype=np.float32)
        out[f0:f1] = seg_out[j0:j1]
        if progress is not None:
            progress((i + 1) / len(plan))
    assert out is not None
    return out
