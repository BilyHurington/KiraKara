"""Built-in karaoke looks. Each is a complete, ready-to-use style."""

from __future__ import annotations

from ..models import KaraokeStyle

# (label, description, colors: unsung, sung, outline, text overrides)
_PRESETS: dict[str, dict] = {
    "classic": {"label": "经典", "description": "白字 · 蓝色扫光 · 深蓝描边",
                "text": {"color_unsung": "#FFFFFF", "color_sung": "#2F80ED", "outline_color": "#0B1F3A"}},
    "fresh": {"label": "清新", "description": "白字 · 橙黄扫光 · 深棕描边",
              "text": {"color_unsung": "#FFFFFF", "color_sung": "#FFB020", "outline_color": "#3A2300"}},
    "sakura": {"label": "樱花", "description": "白字 · 粉色扫光 · 深紫红描边",
               "text": {"color_unsung": "#FFFFFF", "color_sung": "#FF5C8A", "outline_color": "#3D0B24"}},
    "minimal": {"label": "极简", "description": "灰字变白 · 细黑描边 · 无阴影",
                "text": {"color_unsung": "#A7ADB8", "color_sung": "#FFFFFF", "outline_color": "#000000",
                         "outline": 2.5, "shadow": 0.0}},
}


def preset_list() -> list[dict]:
    return [{"name": k, "label": v["label"], "description": v["description"],
             "style": make_preset(k).model_dump(mode="json")} for k, v in _PRESETS.items()]


def make_preset(name: str, keep: KaraokeStyle | None = None) -> KaraokeStyle:
    """A preset's style. With ``keep``, layout / ruby choices / timing / font are kept
    and only the look (colors, outline, shadow) changes."""
    if name not in _PRESETS:
        raise KeyError(name)
    base = keep.model_copy(deep=True) if keep is not None else KaraokeStyle()
    fresh = KaraokeStyle()
    data = base.model_dump()
    look = {**fresh.text.model_dump(), **_PRESETS[name]["text"]}
    for k in ("color_unsung", "color_sung", "outline_color", "outline", "shadow", "shadow_color", "shadow_opacity"):
        data["text"][k] = look[k]
    for k in ("color_unsung", "color_sung", "outline_color"):
        data["ruby"][k] = look[k]
    data["preset"] = name
    return KaraokeStyle.model_validate(data)
