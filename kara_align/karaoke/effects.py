"""Background motion under the subtitles.

Two kinds:

* **Particles** (樱花花瓣 / 雪花 / 星光): generated as ASS vector drawings animated
  with ``\\move`` / ``\\t``.  No files, no licences, and the preview (libass)
  shows exactly what is burned.  Deterministic: the same style gives the same
  petals every time.
* **Effect videos** imported by the user (e.g. a sakura overlay downloaded from a
  free stock site): kept in ``<KARA_ALIGN_HOME>/effects/<id>/`` with a small
  ``meta.json``; laid over the picture with their alpha channel, or with a
  "screen" blend when the effect is on a black background.
"""

from __future__ import annotations

import json
import random
import shutil
import time
from pathlib import Path
from typing import Optional

from ..models import KaraokeEffects, new_id, utcnow
from ..project.store import atomic_write_text, home_dir

LAYER = 0  # under every subtitle layer

# ---------------------------------------------------------------------------------------- particles

_PETAL = "m 0 -10 b 5 -12 9 -6 8 0 b 7 6 3 11 0 12 b -3 11 -7 6 -8 0 b -9 -6 -5 -12 0 -10"
_FLAKE = "m 0 -6 b 3.3 -6 6 -3.3 6 0 b 6 3.3 3.3 6 0 6 b -3.3 6 -6 3.3 -6 0 b -6 -3.3 -3.3 -6 0 -6"
_STAR = "m 0 -12 l 2.2 -2.2 l 12 0 l 2.2 2.2 l 0 12 l -2.2 2.2 l -12 0 l -2.2 -2.2"

_KINDS = {
    #          per minute at 100 %, base size px, palette
    "sakura": (100, 2.2, ["#FFC4D6", "#FFB0C8", "#FFD9E6", "#FFA3BF"]),
    "snow": (180, 1.5, ["#FFFFFF", "#F2F7FF"]),
    "stars": (240, 1.8, ["#FFF6C8", "#FFFFFF", "#FFE9A8"]),
}
LABELS = {"none": "无", "sakura": "樱花花瓣", "snow": "雪花", "stars": "星光"}


def _bgr(hex_rgb: str) -> str:
    h = hex_rgb.lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def _t(ms: float) -> str:
    cs = max(0, int(round(ms / 10)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def particle_events(fx: KaraokeEffects, W: int, H: int, duration_ms: int, *, seed: int = 7) -> list[str]:
    """ASS Dialogue lines for the particle effect over the whole video."""
    if fx.particles == "none" or duration_ms <= 0:
        return []
    rate, base, palette = _KINDS[fx.particles]
    if fx.color:
        palette = [fx.color]
    k = H / 1080.0
    count = max(1, int(rate * fx.density / 100 * duration_ms / 60000))
    alpha = f"&H{int(round(255 * (1 - fx.opacity / 100))):02X}&"
    rnd = random.Random(f"{seed}-{fx.particles}")
    out = []
    for _ in range(count):
        col = _bgr(rnd.choice(palette))
        scale = base * fx.size / 100 * k * rnd.uniform(0.7, 1.3) * 100
        if fx.particles == "stars":  # twinkle in place
            life = rnd.uniform(1400, 3000)
            t0 = rnd.uniform(-life / 2, duration_ms)
            x, y = rnd.uniform(0, W), rnd.uniform(0, H * 0.75)
            half = int(life / 2)
            tags = (f"\\an7\\pos({x:.0f},{y:.0f})\\bord0\\shad0\\blur{1.2 * k:.1f}\\1c{col}\\1a&HFF&"
                    f"\\fscx{scale:.0f}\\fscy{scale:.0f}\\frz{rnd.uniform(0, 45):.0f}"
                    f"\\t(0,{half},\\1a{alpha}\\fscx{scale * 1.25:.0f}\\fscy{scale * 1.25:.0f})"
                    f"\\t({half},{int(life)},\\1a&HFF&\\fscx{scale * 0.6:.0f}\\fscy{scale * 0.6:.0f})")
            shape = _STAR
        else:  # fall across the frame, drifting sideways
            slow = fx.particles == "snow"
            life = rnd.uniform(9000, 15000) if slow else rnd.uniform(7000, 12000)
            t0 = rnd.uniform(-life, duration_ms)
            x0 = rnd.uniform(-0.1 * W, 1.1 * W)
            x1 = x0 + rnd.uniform(-0.12, 0.12) * W + (0 if slow else rnd.uniform(-0.1, 0.25) * W)
            y0, y1 = -40 * k, H + 40 * k
            if t0 < 0:  # already falling when the video starts: begin mid-flight
                f = -t0 / life
                x0, y0 = x0 + (x1 - x0) * f, y0 + (y1 - y0) * f
                life, t0 = life + t0, 0.0
            spin = rnd.choice((-1, 1)) * rnd.uniform(180, 540)
            tags = (f"\\an7\\move({x0:.0f},{y0:.0f},{x1:.0f},{y1:.0f})\\bord0\\shad0\\blur{(1.5 if slow else 0.6) * k:.1f}"
                    f"\\1c{col}\\1a{alpha}\\fscx{scale:.0f}\\fscy{scale:.0f}\\frz{rnd.uniform(0, 360):.0f}")
            if not slow:  # petals tumble: spin and flip
                tags += f"\\t(\\frz{spin:.0f}\\fry{rnd.choice((-1, 1)) * rnd.uniform(360, 900):.0f})"
            shape = _FLAKE if slow else _PETAL
        start, end = max(0.0, t0), t0 + life
        if end <= 0:
            continue
        out.append(f"Dialogue: {LAYER},{_t(start)},{_t(end)},KFx,,0,0,0,,{{{tags}\\p1}}{shape}{{\\p0}}")
    return out


# Free effect videos worth getting (checked 2026-09): free to use in your videos,
# but their licences forbid redistribution, so the app only links to the pages;
# the user downloads the file and imports it.
SOURCES = [
    {"name": "樱花飘落（循环，透明背景）", "site": "miirriin", "url": "https://miirriin.com/en/sakura03-en/",
     "format": "MOV 透明 / MP4 黑底 · 1080p · 15 秒可循环", "license": "可免费商用、无需署名；不可再分发"},
    {"name": "樱花花瓣（透明背景）", "site": "miirriin", "url": "https://miirriin.com/en/sakura01-en/",
     "format": "MOV 透明 · 1080p · 15 秒可循环", "license": "可免费商用、无需署名；不可再分发"},
    {"name": "雪花（透明背景）", "site": "miirriin", "url": "https://miirriin.com/en/snow01-en/",
     "format": "MOV 透明 · 1080p · 15 秒可循环", "license": "可免费商用、无需署名；不可再分发"},
    {"name": "星光粒子（黑底）", "site": "Pixabay", "url": "https://pixabay.com/videos/stars-particles-space-overlay-166887/",
     "format": "MP4 黑底 · 1080p", "license": "Pixabay 许可：可免费用于视频；不可单独再分发"},
    {"name": "粉色光尘（黑底）", "site": "Pixabay", "url": "https://pixabay.com/videos/particles-dust-pink-effect-dark-7956/",
     "format": "MP4 黑底 · 1080p", "license": "Pixabay 许可：可免费用于视频；不可单独再分发"},
    {"name": "雪夜（黑底）", "site": "Pexels", "url": "https://www.pexels.com/video/snowfall-in-black-background-5485148/",
     "format": "MP4 黑底 · 1080p · 44 秒", "license": "Pexels 许可：可免费用于视频，无需署名"},
]

FX_STYLE = ("Style: KFx,Arial,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1")


# ---------------------------------------------------------------------------------------- effect videos


class EffectError(ValueError):
    pass


def effects_dir() -> Path:
    p = home_dir() / "effects"
    p.mkdir(parents=True, exist_ok=True)
    return p


def list_effects() -> list[dict]:
    out = []
    for d in sorted(effects_dir().iterdir()):
        meta = d / "meta.json"
        if meta.exists():
            try:
                m = json.loads(meta.read_text(encoding="utf-8"))
                if (d / m["filename"]).exists():
                    out.append(m)
            except Exception:
                continue
    return sorted(out, key=lambda m: (m.get("order", 0), m.get("created", "")))


def get_effect(effect_id: str) -> tuple[dict, Path]:
    if not effect_id or "/" in effect_id or effect_id.startswith("."):
        raise EffectError("无效的动效 ID")
    d = effects_dir() / effect_id
    try:
        m = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise EffectError("没有这个动效（可能已删除）") from None
    return m, d / m["filename"]


def import_effect(src: Path, filename: str, name: str = "", blend: str = "auto",
                  source_url: str = "", license_note: str = "") -> dict:
    """Keep a copy of an effect video; blend "auto" picks alpha when the file has one."""
    from ..audio.video import probe_media

    info = probe_media(src)
    if not info.get("width"):
        raise EffectError("这个文件没有视频画面")
    pix = str(info.get("pix_fmt") or "")
    has_alpha = pix.startswith(("yuva", "rgba", "argb", "bgra", "abgr", "gbrap", "ya", "pal8"))
    if blend == "auto":
        blend = "alpha" if has_alpha else "screen"
    if blend not in ("alpha", "screen"):
        raise EffectError("混合方式只能是 alpha / screen")
    eid = new_id("fx")
    d = effects_dir() / eid
    d.mkdir(parents=True)
    safe = Path(filename).name or "effect.mov"
    shutil.copyfile(src, d / safe)
    meta = {"id": eid, "name": (name or Path(safe).stem).strip(), "filename": safe, "blend": blend,
            "has_alpha": has_alpha, "width": info.get("width"), "height": info.get("height"),
            "duration_ms": info.get("duration_ms"), "created": utcnow(), "order": time.time(), "source_url": source_url,
            "license": license_note}
    atomic_write_text(d / "meta.json", json.dumps(meta, ensure_ascii=False, indent=1))
    return meta


def delete_effect(effect_id: str) -> None:
    get_effect(effect_id)
    shutil.rmtree(effects_dir() / effect_id, ignore_errors=True)


def overlay_for(fx: KaraokeEffects) -> Optional[dict]:
    """{path, blend, opacity, duration_ms} for the renderer, or None."""
    if not fx.overlay:
        return None
    try:
        meta, path = get_effect(fx.overlay)
    except EffectError:
        return None
    return {"path": path, "blend": meta["blend"], "opacity": fx.overlay_opacity / 100,
            "duration_ms": meta.get("duration_ms") or 0}
