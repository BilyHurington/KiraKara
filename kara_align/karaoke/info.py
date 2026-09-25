"""Song title card: title / artist / album / credits in a top corner at the start.

The lines come from the song data (lyrics metadata filled from the music
platform, credit lines such as ``作词：…`` kept in the lyrics) and the fields the
style picks, unless the project has its own text (``Project.song_info_text``,
one line each, the first is the title).  The card slides in beside a thin accent
bar in the lyrics' sung colour, uses the lyrics' font, outline and glow, and
fades out after ``duration_ms`` — or before the first lyric / translation that
would share the top edge.
"""

from __future__ import annotations

import re
from typing import Optional

from ..models import KaraokeStyle, Project
from .fonts import Measurer

LABELS = {"title": "歌名", "artist": "歌手", "album": "专辑", "lyricist": "作词", "composer": "作曲", "arranger": "编曲"}
_CREDIT = {"lyricist": re.compile(r"作词|作詞|^词$|^詞$|lyric|written", re.I),
           "arranger": re.compile(r"编曲|編曲|arrang", re.I),
           "composer": re.compile(r"作曲|^曲$|compos|music", re.I)}
_LABEL = re.compile(r"^\s*([^:：]{1,16}?)\s*[:：]\s*(.+?)\s*$")

L_INFO_GLOW, L_INFO = 8, 9
_BAR = 6  # accent bar width (1080p px)


def song_fields(project: Project) -> dict[str, str]:
    """Every field the song data can fill (empty ones left out)."""
    meta = project.lyrics.meta
    out = {"title": meta.title or project.name or "", "artist": meta.artist or "", "album": meta.album or ""}
    for ln in project.lyrics.lines:
        if ln.kind != "meta":
            continue
        m = _LABEL.match(ln.text)
        if not m:
            continue
        for key, pat in _CREDIT.items():
            if pat.search(m.group(1).strip()) and not out.get(key):
                out[key] = m.group(2)
                break
    return {k: v.strip() for k, v in out.items() if v and v.strip()}


def auto_lines(project: Project, style: KaraokeStyle) -> list[str]:
    fields = song_fields(project)
    lines = []
    for f in style.info.fields:
        v = fields.get(f)
        if v:
            lines.append(f"{LABELS[f]}：{v}" if f in ("lyricist", "composer", "arranger") else v)
    return lines


def info_lines(project: Project, style: KaraokeStyle) -> list[str]:
    """The card's lines: the project's own text, or the chosen fields."""
    if project.song_info_text is not None:
        return [x.strip() for x in project.song_info_text.splitlines() if x.strip()]
    return auto_lines(project, style)


def info_events(project: Project, style: KaraokeStyle, W: int, H: int, k: float, family: str,
                time_offset_ms: float, busy_from_ms: Optional[float]) -> list[str]:
    """Dialogue lines of the card (style ``KInfo``); ``busy_from_ms`` is when a lyric or
    translation first shows at the top edge (the card is gone by then)."""
    info, txt, glow = style.info, style.text, style.glow
    lines = info_lines(project, style)
    if not info.enabled or not lines:
        return []
    t0 = info.start_ms + time_offset_ms
    t1 = t0 + info.duration_ms
    if busy_from_ms is not None and busy_from_ms - 200 < t1:
        t1 = max(t0 + 2000, busy_from_ms - 200)
    t0 = max(0.0, t0)

    right = info.position == "top-right"
    title_size = info.size * k
    sub_size = max(18 * k, title_size * 0.56)
    margin = info.margin * k
    bar_w = _BAR * k
    gap = 14 * k
    max_w = W * 0.42
    color = _bgr(info.color or txt.color_unsung)
    accent = _bgr(info.accent or txt.color_sung)
    outline_c = _bgr(txt.outline_color)
    slide = 28 * k * (1 if right else -1)
    x_text = (W - margin - bar_w - gap) if right else (margin + bar_w + gap)
    an = 9 if right else 7

    events: list[str] = []
    y = margin
    for i, text in enumerate(lines):
        size = title_size if i == 0 else sub_size
        bold = txt.bold if i == 0 else False
        width = Measurer(family, bold, size).width(text)
        sx = f"\\fscx{max_w / width * 100:.1f}" if width > max_w else ""
        a = t0 + 90 * i  # lines come in one after another
        move = f"\\move({x_text + slide:.1f},{y:.1f},{x_text:.1f},{y:.1f},0,450)"
        base = (f"\\an{an}{move}\\fn{family}\\fs{size:.1f}\\b{int(bold)}{sx}\\fad(350,500)")
        body = _escape(text)
        if glow.enabled:
            events.append(_dialogue(L_INFO_GLOW, a, t1, f"{base}\\1a&HFF&\\3c{_bgr(glow.color_unsung)}"
                                    f"\\3a&H{int(round(255 * (1 - glow.strength / 100))):02X}&"
                                    f"\\bord{glow.size * k * (0.7 if i == 0 else 0.5):.1f}"
                                    f"\\blur{glow.blur * k:.1f}\\shad0", body))
        sub_alpha = "" if i == 0 else "\\1a&H18&"
        events.append(_dialogue(L_INFO, a, t1, f"{base}\\1c{color}{sub_alpha}\\3c{outline_c}"
                                f"\\bord{txt.outline * k * (0.7 if i == 0 else 0.55):.2f}"
                                f"\\shad{txt.shadow * k * 0.6:.2f}", body))
        y += size * (1.18 if i == 0 else 1.32)
    block_h = y - margin
    bar_x = (W - margin - bar_w) if right else margin
    rect = f"m 0 0 l {bar_w:.1f} 0 l {bar_w:.1f} {block_h:.1f} l 0 {block_h:.1f}"
    events.append(_dialogue(L_INFO, t0, t1, f"\\an7\\pos({bar_x:.1f},{margin:.1f})\\bord0\\shad{txt.shadow * k * 0.4:.2f}"
                            f"\\1c{accent}\\fscy0\\t(0,380,0.6,\\fscy100)\\fad(250,500)", f"{{\\p1}}{rect}{{\\p0}}"))
    return events


def info_style(family: str, size: float) -> str:
    return (f"Style: KInfo,{family},{size:.1f},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,"
            "0,0,7,0,0,0,1")


def _dialogue(layer: int, t0: float, t1: float, tags: str, body: str) -> str:
    from .ass import ass_time

    return f"Dialogue: {layer},{ass_time(t0)},{ass_time(t1)},KInfo,,0,0,0,,{{{tags}}}{body}"


def _bgr(hex_rgb: str) -> str:
    h = hex_rgb.lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
