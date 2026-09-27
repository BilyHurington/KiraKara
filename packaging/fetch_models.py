"""Download the two models the portable build ships with into ``<models>``, the way the app keeps them:

* ``<models>/alignment``: the alignment model in the Hugging Face cache layout (loaded once, so the
  cache also records the optional files the model does not have, which offline loading relies on);
* ``<models>/separation``: the default separation model (MelBand RoFormer) and audio-separator's
  model lists.

    python fetch_models.py <models>

Symlinks in the cache (snapshots -> blobs) are replaced by the files themselves: archives and
Windows without developer mode do not keep them.  The blobs are then not needed any more.
"""

import os
import shutil
import sys
from pathlib import Path


def main() -> None:
    models = Path(sys.argv[1]).resolve()
    os.environ["KARA_ALIGN_MODELS"] = str(models)
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)

    from kara_align.align.backends.wav2vec2_ctc import DEFAULT_MODEL_ID, DEFAULT_REVISION, load_model
    from kara_align.audio.separation import PRESETS, models_dir

    print(f"==> alignment model {DEFAULT_MODEL_ID}@{DEFAULT_REVISION[:8]}", flush=True)
    load_model(DEFAULT_MODEL_ID, DEFAULT_REVISION, "cpu")
    cache = models / "alignment" / ("models--" + DEFAULT_MODEL_ID.replace("/", "--"))
    for p in sorted(cache.rglob("*")):
        if p.is_symlink():
            target = p.resolve(strict=True)
            p.unlink()
            shutil.copy2(target, p)
    shutil.rmtree(cache / "blobs", ignore_errors=True)
    snap = cache / "snapshots" / DEFAULT_REVISION
    assert (snap / "model.safetensors").stat().st_size > 100_000_000, "alignment weights missing"

    preset = next(p for p in PRESETS if p.name == "melband-roformer")
    print(f"==> separation model {preset.model_filename}", flush=True)
    from audio_separator.separator import Separator

    sep_dir = models_dir()
    Separator(model_file_dir=str(sep_dir), output_dir=str(sep_dir)).load_model(model_filename=preset.model_filename)
    assert (sep_dir / preset.model_filename).stat().st_size > 100_000_000, "separation weights missing"

    for p in sorted(models.rglob("*")):
        if p.is_file():
            print(f"{p.stat().st_size:>14,}  {p.relative_to(models)}")


if __name__ == "__main__":
    main()
