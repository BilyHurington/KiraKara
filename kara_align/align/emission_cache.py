"""On-disk cache of acoustic emissions (design §6, "声学分数" layer).

Key = actual analysed audio (sha256 + role + origin) + model/revision/profile
+ resampling and chunking configuration.  Weights loaded from a local folder
have no revision: the key then includes the size and modification time of the
model files, so replacing the weights in place does not reuse old scores.  Lyrics, anchors, mode and decode
parameters are deliberately *not* part of the key: changing them reuses the
emission.  Only complete results are stored (``put(..., complete=True)``);
writes are atomic (tmp file + rename).  logp is stored as float32 (no lossy
float16) in an ``.npz`` next to a ``.json`` meta file.  No user data lives
here, so the cache can be cleared at any time.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np

from ..interfaces import Emission
from ..models import BackendInfo, stable_hash
from ..timebase import FrameMap

CACHE_FORMAT = 1


def local_model_stamp(model_id: Optional[str]) -> Optional[list]:
    """(name, size, mtime) of the files of a model given as a local path, else None."""
    if not model_id:
        return None
    try:
        path = Path(model_id).expanduser()
        if not path.exists():
            return None
        files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
        return [(str(p.relative_to(path)) if p != path else p.name, p.stat().st_size, int(p.stat().st_mtime))
                for p in files[:200]]
    except OSError:
        return None


def emission_cache_key(audio_sha256: str, role: str, origin_samples: int, backend_info: BackendInfo,
                       chunk_s: float, context_s: float, resample_desc: str) -> str:
    extra = dict(backend_info.extra or {})
    extra.pop("notes", None)
    extra.pop("device", None)  # device choice does not change the intended result
    stamp = local_model_stamp(backend_info.model_id) if not backend_info.model_revision else None
    if stamp is not None:
        extra["local_model_files"] = stamp
    return stable_hash({
        "fmt": CACHE_FORMAT,
        "audio": audio_sha256,
        "role": role,
        "origin": int(origin_samples),
        "backend": backend_info.name,
        "model": backend_info.model_id,
        "rev": backend_info.model_revision,
        "profile": backend_info.profile,
        "sr": backend_info.sample_rate,
        "extra": extra,
        "chunk_s": float(chunk_s),
        "context_s": float(context_s),
        "resample": resample_desc,
    }, n=32)


class EmissionCache:
    def __init__(self, root_dir: str | os.PathLike) -> None:
        self.root = Path(root_dir)

    def _paths(self, key: str) -> tuple[Path, Path]:
        d = self.root / key[:2]
        return d / f"{key}.npz", d / f"{key}.json"

    def has(self, key: str) -> bool:
        npz, meta = self._paths(key)
        return npz.exists() and meta.exists()

    def get(self, key: str) -> Optional[Emission]:
        npz, meta = self._paths(key)
        if not (npz.exists() and meta.exists()):
            return None
        try:
            m = json.loads(meta.read_text("utf-8"))
            if not m.get("complete") or m.get("format") != CACHE_FORMAT:
                return None
            with np.load(npz) as z:
                logp = z["logp"].astype(np.float32, copy=False)
            if logp.shape[0] != m["num_frames"]:
                return None
            fm = m["frame_map"]
            return Emission(logp=logp, frame_map=FrameMap(fm["sample_rate"], fm["hop_samples"],
                                                          fm["offset_samples"], fm["origin_samples"]),
                            blank_id=int(m["blank_id"]), cache_key=key)
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return None

    def put(self, key: str, emission: Emission, backend_info: Optional[BackendInfo] = None, *,
            complete: bool) -> None:
        if not complete:
            raise ValueError("拒绝缓存不完整或已取消的声学分数")
        npz, meta = self._paths(key)
        npz.parent.mkdir(parents=True, exist_ok=True)
        fm = emission.frame_map
        m = {
            "format": CACHE_FORMAT, "complete": True, "key": key,
            "num_frames": int(emission.logp.shape[0]), "vocab_size": int(emission.logp.shape[1]),
            "blank_id": int(emission.blank_id),
            "frame_map": {"sample_rate": fm.sample_rate, "hop_samples": fm.hop_samples,
                          "offset_samples": fm.offset_samples, "origin_samples": fm.origin_samples},
            "backend": backend_info.model_dump() if backend_info else None,
        }
        # array first, meta last: a reader requires both, meta marks completion
        fd, tmp = tempfile.mkstemp(dir=npz.parent, suffix=".npz.tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                np.savez(f, logp=np.asarray(emission.logp, dtype=np.float32))
            os.replace(tmp, npz)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        fd, tmp = tempfile.mkstemp(dir=npz.parent, suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(m, f)
            os.replace(tmp, meta)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        emission.cache_key = key

    def delete(self, key: str) -> None:
        for p in self._paths(key):
            p.unlink(missing_ok=True)

    def clear(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def size_bytes(self) -> int:
        if not self.root.exists():
            return 0
        return sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())
