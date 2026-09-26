"""Shared test setup: tests never move, copy or re-download the user's model files."""

from pathlib import Path

import pytest

# the app's own model folder: read by the few tests that run a real model (never downloaded again)
_APP_MODELS = Path(__file__).resolve().parents[1] / "models"


@pytest.fixture(autouse=True)
def _own_folders_elsewhere(tmp_path_factory, monkeypatch):
    """The app's home, the Hugging Face cache and the old places models were moved from point into
    the test's temporary folder: nothing is moved out of the user's folders or copied into the app's
    model folder.  Models are read from the app's own folder.  Tests that need their own home or
    model folder set KARA_ALIGN_HOME / KARA_ALIGN_MODELS again."""
    base = tmp_path_factory.mktemp("folders")
    monkeypatch.setenv("KARA_ALIGN_MODELS", str(_APP_MODELS))
    monkeypatch.setenv("KARA_ALIGN_HOME", str(base / "home"))
    monkeypatch.setenv("HF_HUB_CACHE", str(base / "hf-hub"))
    from kara_align.audio import separation

    monkeypatch.setattr(separation, "_OLD_MODEL_DIRS", [])
