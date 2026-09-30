"""Saved sets of singers ("演唱者预设"): names, colours and keys of the singers, the combinations and
how parts sung together look, for songs with the same singers.

The library lives in ``<KARA_ALIGN_HOME>/singer_presets.json``, shared by every project (as the
subtitle styles are, karaoke/styles.py).  A preset is used in a song by service.apply_singer_preset.
"""

from __future__ import annotations

import json
import threading
from typing import Optional

from ..models import KaraokeSingers, new_id, utcnow
from ..project.store import atomic_write_text, home_dir

_lock = threading.Lock()


class PresetError(ValueError):
    pass


def _path():
    return home_dir() / "singer_presets.json"


def _read_raw() -> list:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception:  # unreadable: kept aside (never overwritten by the next save), start empty
        try:
            _path().replace(_path().with_suffix(f".broken-{new_id()[:6]}.json"))
        except OSError:
            pass
        return []
    return raw if isinstance(raw, list) else []


def _parse(item) -> Optional[dict]:
    try:
        return {"id": str(item["id"]), "name": str(item["name"]), "updated": item.get("updated"),
                "singers": KaraokeSingers.model_validate(item["singers"]).model_dump(mode="json")}
    except Exception:
        return None


def _load() -> list[dict]:
    return [x for x in (_parse(i) for i in _read_raw()) if x is not None]


def _save(items: list[dict]) -> None:
    unreadable = [i for i in _read_raw() if _parse(i) is None]  # (e.g. saved by a newer version): kept
    atomic_write_text(_path(), json.dumps(items + unreadable, ensure_ascii=False, indent=1))


def list_presets() -> list[dict]:
    """[{id, name, updated, singers}], by name."""
    return sorted(_load(), key=lambda x: x["name"])


def get_preset(preset_id: str) -> dict:
    for x in _load():
        if x["id"] == preset_id:
            return x
    raise PresetError("没有这个演唱者预设")


def save_preset(name: str, singers: dict, preset_id: Optional[str] = None) -> dict:
    """Create (no id) or overwrite (id); a new preset with a name already used replaces that one."""
    name = (name or "").strip()
    if not name:
        raise PresetError("请给预设起个名字")
    try:
        sg = KaraokeSingers.model_validate(singers, context={"strict": True})
    except Exception as e:
        raise PresetError(f"演唱者设置无效：{e}") from e
    if not sg.members:
        raise PresetError("还没有演唱者，添加后再保存")
    with _lock:
        items = _load()
        target = next((x for x in items if (preset_id and x["id"] == preset_id) or (not preset_id and x["name"] == name)), None)
        if preset_id and target is None:
            raise PresetError("没有这个演唱者预设")
        entry = {"id": target["id"] if target else new_id("sp"), "name": name, "updated": utcnow(),
                 "singers": sg.model_dump(mode="json")}
        items = [entry if (target and x["id"] == target["id"]) else x for x in items]
        if target is None:
            items.append(entry)
        _save(items)
    return entry


def delete_preset(preset_id: str) -> None:
    with _lock:
        items = _load()
        if not any(x["id"] == preset_id for x in items):
            raise PresetError("没有这个演唱者预设")
        _save([x for x in items if x["id"] != preset_id])
