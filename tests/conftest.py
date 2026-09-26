"""Shared test setup: nothing a test does reaches the user's own folders."""

import pytest


@pytest.fixture(autouse=True)
def _own_folders_elsewhere(tmp_path_factory, monkeypatch):
    """Model folders, the app's home and the old places models are moved from all point into the
    test's temporary folder (a test must never move or copy the user's real model files).  Tests
    that need their own home set KARA_ALIGN_HOME again."""
    base = tmp_path_factory.mktemp("folders")
    monkeypatch.setenv("KARA_ALIGN_MODELS", str(base / "models"))
    monkeypatch.setenv("KARA_ALIGN_HOME", str(base / "home"))
    monkeypatch.setenv("HF_HUB_CACHE", str(base / "hf-hub"))
    from kara_align.audio import separation

    monkeypatch.setattr(separation, "_OLD_MODEL_DIRS", [])
