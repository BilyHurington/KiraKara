"""Optional vocal separation through ``python-audio-separator``.

The package (and its model weights) are optional.  Separation runs in a child
process so a job can be cancelled by terminating it.  Failures raise
:class:`SeparationError`; there is never a silent fallback to the original.

Timeline rule: separated stems must stay on the original timeline.  Output
length mismatches are corrected only by :func:`fix_stem_length` – drop a
*known* leading padding (declared per preset, 0 for the bundled presets) and
then trim / zero-pad at the END.  Nothing is stretched.  The residual lag
against the original is measured by cross-correlation and recorded.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .io import file_sha256, load_audio, write_wav
from .sync import check_stem_sync


class SeparationError(Exception):
    pass


@dataclass(frozen=True)
class SeparationPreset:
    name: str
    model_filename: str
    architecture: str
    notes: str
    license_note: str = "Weights come from the audio-separator model registry; check upstream licence before redistribution."
    # samples of known leading padding introduced by the model at 44.1 kHz (0 = none known)
    leading_padding_samples: int = 0


# Speeds measured on Apple Silicon (MPS) with audio-separator 0.30.2, per
# minute of audio; the first run also downloads the weights.
PRESETS: list[SeparationPreset] = [
    SeparationPreset(
        "melband-roformer", "model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt", "MelBand RoFormer",
        "推荐：人声分离干净，速度与质量平衡（Apple 芯片 GPU 约 1 分钟处理 1 分钟音频）。"),
    SeparationPreset(
        "bs-roformer", "model_bs_roformer_ep_317_sdr_12.9755.ckpt", "BS-RoFormer",
        "质量最高但最慢（约为 MelBand 的 1.5–2 倍耗时）；建议使用 GPU / MPS。"),
    SeparationPreset(
        "mdx-fast", "UVR-MDX-NET-Inst_HQ_3.onnx", "MDX-Net",
        "最快（约 0.4 分钟处理 1 分钟音频），分离质量较低，适合快速试听。"),
    SeparationPreset(
        "demucs-htdemucs", "htdemucs_ft.yaml", "Demucs v4",
        "Demucs 四分轨；人声以外（鼓 + 贝斯 + 其他）合并为伴奏。",
        license_note="Demucs weights are MIT (facebookresearch/demucs); verify upstream."),
]


def get_preset(name_or_filename: str) -> SeparationPreset:
    for p in PRESETS:
        if name_or_filename in (p.name, p.model_filename):
            return p
    # allow arbitrary compatible model files, with no known padding
    return SeparationPreset(name_or_filename, name_or_filename, "custom", "custom model file")


@dataclass
class SeparationOutput:
    vocals_path: Path
    instrumental_path: Path
    report: dict = field(default_factory=dict)


def audio_separator_version() -> Optional[str]:
    try:
        from importlib.metadata import version

        return version("audio-separator")
    except Exception:
        return None


def ensure_available() -> str:
    """Return the installed audio-separator version or raise a clear error."""
    try:
        import audio_separator  # noqa: F401
    except Exception as exc:  # ImportError or broken install
        raise SeparationError(
            "人声分离需要可选依赖 audio-separator"
            "（pip install 'kara-align[separation]'）；导入失败：" + str(exc)) from exc
    return audio_separator_version() or "unknown"


def fix_stem_length(stem: np.ndarray, original_len: int, leading_padding: int = 0,
                    max_mismatch: Optional[int] = None) -> tuple[np.ndarray, dict]:
    """Bring a stem back onto the original timeline without stretching.

    1. remove ``leading_padding`` samples (a *known*, documented model delay);
    2. trim or zero-pad at the END to ``original_len``.

    ``max_mismatch`` (samples) rejects outputs whose remaining length
    difference is too large to be explained by block padding.
    """
    x = np.asarray(stem, dtype=np.float32)
    if x.ndim == 1:
        x = x[None, :]
    action = {"input_len": int(x.shape[1]), "original_len": int(original_len),
              "leading_padding_removed": 0, "end_trimmed": 0, "end_padded": 0, "stretched": False}
    if leading_padding:
        if leading_padding < 0 or leading_padding > x.shape[1]:
            raise SeparationError(f"无效的开头填充 {leading_padding}")
        x = x[:, leading_padding:]
        action["leading_padding_removed"] = int(leading_padding)
    diff = x.shape[1] - original_len
    if max_mismatch is not None and abs(diff) > max_mismatch:
        raise SeparationError(
            f"分离出的分轨长度与原曲相差 {diff} 个样本（> {max_mismatch}）；"
            "延迟未知，拒绝拉伸或猜测")
    if diff > 0:
        x = x[:, :original_len]
        action["end_trimmed"] = int(diff)
    elif diff < 0:
        x = np.pad(x, [(0, 0), (0, -diff)])
        action["end_padded"] = int(-diff)
    action["output_len"] = int(x.shape[1])
    return x, action


# tqdm progress lines of the separator, e.g. " 45%|████      | 12/27"
_PCT_RE = re.compile(r"(\d{1,3})%\|")

_CHILD_SCRIPT = r"""
import json, sys
args = json.loads(sys.argv[1])
if args.get("device") == "cpu":
    # hide accelerators from the separator (its MPS path can hang on some setups)
    import torch
    torch.backends.mps.is_available = lambda: False
    torch.cuda.is_available = lambda: False
from audio_separator.separator import Separator
sep = Separator(output_dir=args["out_dir"], output_format="WAV", sample_rate=args["sample_rate"],
                normalization_threshold=args.get("normalization", 0.9))
sep.load_model(model_filename=args["model_filename"])
files = sep.separate(args["input"])
print("@@RESULT@@" + json.dumps({"files": files}), flush=True)
"""


def _classify_outputs(files: list[str], out_dir: Path) -> tuple[Path, Path, list[Path]]:
    paths = [Path(f) if os.path.isabs(f) else out_dir / f for f in files]
    vocals = [p for p in paths if "(vocals)" in p.name.lower()]
    inst = [p for p in paths if any(k in p.name.lower() for k in ("(instrumental)", "(no vocals)", "(no_vocals)"))]
    others = [p for p in paths if p not in vocals and p not in inst]
    if not vocals:
        raise SeparationError(f"分离器没有输出人声分轨：{[p.name for p in paths]}")
    if not inst:
        raise SeparationError(
            "分离器没有输出伴奏分轨（多分轨模型？）；"
            f"输出：{[p.name for p in paths]}，不会用相减的方式推导伴奏")
    return vocals[0], inst[0], others


def separate(original_path, out_dir, preset: str = "melband-roformer", cancel=None,
             progress: Optional[Callable[[float, str], None]] = None, *, timeout_s: Optional[float] = None,
             python: Optional[str] = None, device: str = "auto") -> SeparationOutput:
    """Separate ``original_path`` into vocals / instrumental WAVs in ``out_dir``.

    ``cancel`` is any object with a ``cancelled`` attribute (e.g.
    :class:`kara_align.interfaces.CancelToken`); cancelling terminates the
    child process and raises :class:`kara_align.interfaces.Cancelled`.
    """
    from ..interfaces import Cancelled

    version = ensure_available()
    p = get_preset(preset)
    original_path = Path(original_path)
    out_dir = Path(out_dir)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    orig, sr = load_audio(original_path)
    # the separator always reads a WAV decoded by our own loader: the same
    # decoder (and time origin) as alignment, and no dependency on the
    # separator's handling of the source container
    decoded = out_dir / "input.wav"
    write_wav(decoded, orig, sr, subtype="FLOAT")
    if device not in ("auto", "cpu"):
        raise SeparationError(f"不支持的分离设备：{device}（可选 auto / cpu）")
    args = {"input": str(decoded), "out_dir": str(raw_dir), "model_filename": p.model_filename,
            "sample_rate": sr, "device": device}
    if progress:
        progress(0.05, f"加载分离模型 {p.model_filename}")
    proc = subprocess.Popen([python or sys.executable, "-c", _CHILD_SCRIPT, json.dumps(args)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    # drain both pipes continuously: the separator's progress bar writes to
    # stderr all the time and a full pipe would block the child forever
    out_chunks: list[str] = []
    err_tail: list[str] = []
    pct = {"value": None}

    def _drain_out() -> None:
        for chunk in iter(lambda: proc.stdout.read(4096), ""):
            out_chunks.append(chunk)

    def _drain_err() -> None:
        buf = ""
        for chunk in iter(lambda: proc.stderr.read(512), ""):
            buf = (buf + chunk)[-8192:]
            found = _PCT_RE.findall(chunk)
            if found:
                pct["value"] = int(found[-1])
        err_tail.append(buf)

    readers = [threading.Thread(target=_drain_out, daemon=True), threading.Thread(target=_drain_err, daemon=True)]
    for t in readers:
        t.start()
    started = time.monotonic()
    try:
        while proc.poll() is None:
            if cancel is not None and getattr(cancel, "cancelled", False):
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise Cancelled()
            if timeout_s and time.monotonic() - started > timeout_s:
                proc.kill()
                raise SeparationError(f"人声分离超时（{timeout_s} 秒）")
            if progress:
                if pct["value"] is not None:
                    progress(0.1 + 0.8 * pct["value"] / 100.0, f"人声分离中 {pct['value']}%")
                else:
                    elapsed = int(time.monotonic() - started)
                    progress(0.08, f"人声分离中（已用 {elapsed // 60}:{elapsed % 60:02d}）")
            time.sleep(0.25)
    finally:
        if proc.poll() is None:
            proc.kill()
        for t in readers:
            t.join(timeout=5)
    stdout = "".join(out_chunks)
    stderr = "".join(err_tail)
    if proc.returncode != 0 or "@@RESULT@@" not in stdout:
        tail = (stderr or stdout or "").strip().splitlines()[-5:]
        raise SeparationError(f"人声分离失败（退出码 {proc.returncode}）：" + " | ".join(tail))
    files = json.loads(stdout.split("@@RESULT@@", 1)[1].strip().splitlines()[0])["files"]
    v_raw, i_raw, others = _classify_outputs(files, raw_dir)

    report: dict = {
        "preset": p.name, "model_filename": p.model_filename, "architecture": p.architecture,
        "license_note": p.license_note, "audio_separator_version": version,
        "config": {"sample_rate": sr, "output_format": "WAV", "normalization": 0.9, "device": device},
        "original_sha256": file_sha256(original_path), "original_num_samples": int(orig.shape[1]),
        "extra_outputs": [o.name for o in others],
    }
    if progress:
        progress(0.92, "检查分轨时间轴")
    outputs = {}
    stems = {}
    for role, raw in (("vocals", v_raw), ("instrumental", i_raw)):
        x, xsr = load_audio(raw)
        if xsr != sr:
            raise SeparationError(f"{role} stem sample rate {xsr} != original {sr}; refusing to resample silently")
        pad = int(round(p.leading_padding_samples * sr / 44100))
        fixed, action = fix_stem_length(x, orig.shape[1], leading_padding=pad, max_mismatch=int(sr * 1.0))
        dest = out_dir / f"{role}.wav"
        write_wav(dest, fixed, sr, subtype="FLOAT")
        stems[role] = fixed
        outputs[role] = dest
        report[f"{role}_length_fix"] = action
    report["sync"] = {
        "vocals": check_stem_sync(orig, stems["vocals"], sr, "vocals", other_stem=stems["instrumental"]),
        "instrumental": check_stem_sync(orig, stems["instrumental"], sr, "instrumental"),
    }
    report["elapsed_s"] = round(time.monotonic() - started, 2)
    if progress:
        progress(1.0, "完成")
    return SeparationOutput(outputs["vocals"], outputs["instrumental"], report)


def preset_dicts() -> list[dict]:
    return [asdict(p) for p in PRESETS]
