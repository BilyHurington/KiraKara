"""App-wide settings (not per project): AI provider and the simple-mode pipeline.

Stored in ``<KARA_ALIGN_HOME>/settings.json`` (mode 0600).  The API key is
never sent back to the browser and never written into a project or package.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Literal

from pydantic import Field

from .karaoke.styles import default_style as simple_default_style
from .models import KaraokeStyle, _Base
from .project.store import atomic_write_text, home_dir

AiProvider = Literal["none", "claude", "codex", "openai"]


class AiSettings(_Base):
    provider: AiProvider = "none"
    model: str = ""  # empty: the CLI's own default; required for the API
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""  # stored locally only; GET returns has_api_key instead
    api_key_env: str = "OPENAI_API_KEY"  # used when no key is stored
    timeout_s: int = Field(default=600, ge=30, le=3600)


class SimpleSettings(_Base):
    default_mode: Literal["plain", "lrc"] = "lrc"
    ai_readings: bool = True  # use the AI provider (when one is set) to check readings
    separate: bool = True
    separation_preset: str = "melband-roformer"
    separation_device: Literal["auto", "cpu"] = "auto"
    # the complete subtitle style of new tasks (layout, colours, ruby, timing);
    # output.vocal_keep_pct is taken from vocal_keep_pct below
    karaoke: KaraokeStyle = Field(default_factory=simple_default_style)
    auto_export: bool = True
    video_audio: Literal["original", "mix", "none"] = "original"
    vocal_keep_pct: float = Field(default=20.0, ge=0.0, le=100.0)
    quality: Literal["standard", "high"] = "standard"


class AppSettings(_Base):
    version: int = 1
    ai: AiSettings = Field(default_factory=AiSettings)
    simple: SimpleSettings = Field(default_factory=SimpleSettings)


_lock = threading.Lock()


def settings_path():
    return home_dir() / "settings.json"


def load() -> AppSettings:
    p = settings_path()
    try:
        return AppSettings.model_validate(json.loads(p.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return AppSettings()
    except Exception:  # a broken file must never stop the app; keep it for inspection
        try:
            p.replace(p.with_suffix(".broken.json"))
        except OSError:
            pass
        return AppSettings()


def save(s: AppSettings) -> None:
    p = settings_path()
    atomic_write_text(p, json.dumps(s.model_dump(mode="json"), ensure_ascii=False, indent=2))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def update(patch: dict) -> AppSettings:
    """Merge a partial update.  ``ai.api_key`` is only replaced when given;
    ``ai.clear_api_key: true`` removes it."""
    with _lock:
        cur = load().model_dump(mode="json")
        simple_patch = dict(patch.get("simple") or {})
        if simple_patch.pop("reset_karaoke", False):
            simple_patch["karaoke"] = simple_default_style().model_dump(mode="json")
            cur["simple"].pop("karaoke", None)
        ai_patch = dict(patch.get("ai") or {})
        clear = bool(ai_patch.pop("clear_api_key", False))
        if not ai_patch.get("api_key"):
            ai_patch.pop("api_key", None)
        for key, sub in (("ai", ai_patch), ("simple", simple_patch)):
            cur[key] = _merge(cur[key], sub)
        if clear:
            cur["ai"]["api_key"] = ""
        s = AppSettings.model_validate(cur)
        save(s)
        return s


def _merge(base: dict, over: dict) -> dict:
    """Nested merge, so a partial style ({"karaoke": {"timing": {...}}}) keeps the rest."""
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def public(s: AppSettings) -> dict:
    """Settings as sent to the browser (no secret)."""
    d = s.model_dump(mode="json")
    key = d["ai"].pop("api_key")
    d["ai"]["has_api_key"] = bool(key)
    d["ai"]["env_key_present"] = bool(os.environ.get(s.ai.api_key_env or ""))
    return d


def api_key(s: AiSettings) -> str:
    return s.api_key or os.environ.get(s.api_key_env or "", "")
