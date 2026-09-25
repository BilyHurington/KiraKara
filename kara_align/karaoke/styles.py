"""Saved subtitle styles ("预设").

The library lives in ``<KARA_ALIGN_HOME>/styles.json`` and is shared by every
project and by the simple mode.  The built-in 默认 style is always present and
read-only; saving under its name creates a user copy instead.
"""

from __future__ import annotations

import json
import threading
from typing import Optional

from ..models import KaraokeStyle, new_id, utcnow
from ..project.store import atomic_write_text, home_dir

DEFAULT_ID = "default"
DEFAULT_NAME = "默认"

# The look tuned on a real project: pink sweep, romaji over every syllable, lines
# close to the bottom, shown 4 s early and held 2 s, easing in and out.
_DEFAULT: dict = {
    "layout": {"margin_v": 40, "line_spacing": 0, "margin_h": 240},
    "text": {"color_sung": "#ED35B3"},
    "ruby": {"script": "romaji", "target": "all"},
    "timing": {"lead_in_ms": 4000, "hold_ms": 2000, "early_max_ms": 6000},
}

_lock = threading.Lock()


class StyleError(ValueError):
    pass


def default_style() -> KaraokeStyle:
    data = KaraokeStyle().model_dump()
    for key, over in _DEFAULT.items():
        data[key].update(over)
    data["preset"] = DEFAULT_NAME
    return KaraokeStyle.model_validate(data)


def _path():
    return home_dir() / "styles.json"


def _load_user() -> list[dict]:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception:
        return []
    out = []
    for item in raw if isinstance(raw, list) else []:
        try:
            out.append({"id": str(item["id"]), "name": str(item["name"]), "updated": item.get("updated"),
                        "style": KaraokeStyle.model_validate(item["style"]).model_dump(mode="json")})
        except Exception:
            continue  # skip a broken entry, keep the rest
    return out


def _save_user(items: list[dict]) -> None:
    atomic_write_text(_path(), json.dumps(items, ensure_ascii=False, indent=1))


def list_styles() -> list[dict]:
    """[{id, name, builtin, updated, style}] with 默认 first."""
    builtin = {"id": DEFAULT_ID, "name": DEFAULT_NAME, "builtin": True, "updated": None,
               "style": default_style().model_dump(mode="json")}
    return [builtin] + [{**x, "builtin": False} for x in _load_user()]


def get_style(style_id: str) -> KaraokeStyle:
    for x in list_styles():
        if x["id"] == style_id:
            return KaraokeStyle.model_validate(x["style"])
    raise StyleError("没有这个预设")


def save_style(name: str, style: dict, style_id: Optional[str] = None) -> dict:
    """Create (no id) or overwrite (id) a saved style.  A new style whose name is
    already used replaces that one, so "保存" twice never makes duplicates."""
    name = (name or "").strip()
    if not name:
        raise StyleError("请给预设起个名字")
    if style_id == DEFAULT_ID or (style_id is None and name == DEFAULT_NAME):
        raise StyleError("“默认”预设不能修改，请换个名字另存")
    try:
        st = KaraokeStyle.model_validate(style)
    except Exception as e:
        raise StyleError(f"字幕样式无效：{e}") from e
    st.preset = name
    with _lock:
        items = _load_user()
        target = next((x for x in items if (style_id and x["id"] == style_id) or (not style_id and x["name"] == name)),
                      None)
        if style_id and target is None:
            raise StyleError("没有这个预设")
        entry = {"id": target["id"] if target else new_id("st"), "name": name, "updated": utcnow(),
                 "style": st.model_dump(mode="json")}
        items = [entry if (target and x["id"] == target["id"]) else x for x in items]
        if target is None:
            items.append(entry)
        _save_user(items)
    return {**entry, "builtin": False}


def delete_style(style_id: str) -> None:
    if style_id == DEFAULT_ID:
        raise StyleError("“默认”预设不能删除")
    with _lock:
        items = _load_user()
        if not any(x["id"] == style_id for x in items):
            raise StyleError("没有这个预设")
        _save_user([x for x in items if x["id"] != style_id])
