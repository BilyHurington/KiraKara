"""Song title card: title / artist / album / credits in a top corner at the start
(and, with ``info.outro``, the same card again at the end).

The lines come from the song data (lyrics metadata filled from the music
platform, credit lines such as ``作词：…`` kept in the lyrics) and the fields the
style picks, unless the project has its own text (``Project.song_info_text``,
one line each, the first is the title).  The card slides in beside a thin accent
bar in the lyrics' sung colour, uses the lyrics' font, outline and glow, and
fades out after ``duration_ms`` — or before the first lyric / translation that
would share the top edge (after 2 s at least), and always before anything that
would be drawn where the card is (then without the 2 s; a card that would not
last a second is left out).

The ending card is the mirror image: it stays until the end of the song for
``outro_duration_ms``, comes in only after the last lyric / translation along the
top edge has gone (staying 2 s at least) and after anything drawn where it is.
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

from ..models import KaraokeStyle, Project
from .fonts import Measurer

LABELS = {"title": "歌名", "artist": "歌手", "album": "专辑", "lyricist": "作词", "composer": "作曲", "arranger": "编曲"}
# a label may name several roles ("作词/作曲", "词曲", "Lyrics & Music"): each one is filled
_CREDIT = {"lyricist": re.compile(r"作词|作詞|^[词詞]|lyric|written", re.I),
           "arranger": re.compile(r"编曲|編曲|arrang", re.I),
           "composer": re.compile(r"作曲|^曲$|[词詞]曲|compos|music", re.I)}
_LABEL = re.compile(r"^\s*([^:：]{1,16}?)\s*[:：]\s*(.+?)\s*$")

L_INFO_GLOW, L_INFO = 8, 9
_BAR = 6  # accent bar width (px at 1920 wide)


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
                time_offset_ms: float, busy_from_ms: Optional[float], *,
                boxes: Sequence[tuple[float, float, float, float, float, float]] = (),
                warnings: Optional[list[str]] = None, end_ms: Optional[float] = None,
                busy_until_ms: Optional[float] = None) -> list[str]:
    """Dialogue lines of the card (style ``KInfo``); ``busy_from_ms`` is when a lyric or
    translation first shows at the top edge (the card is gone by then, but stays 2 s);
    ``boxes``: (from, to, x0, y0, x1, y1) of what else is drawn — the card is gone before
    any of it shows where the card is.  ``end_ms`` (the end of the song on the subtitles'
    timeline): the ending card too, which comes in after ``busy_until_ms`` (the last lyric /
    translation along the top edge) and after everything drawn where it is."""
    info = style.info
    lines = info_lines(project, style)
    if not info.enabled or not lines:
        return []
    t0 = info.start_ms + time_offset_ms
    t1 = t0 + info.duration_ms
    if busy_from_ms is not None and busy_from_ms - 200 < t1:
        t1 = max(t0 + 2000, busy_from_ms - 200)
    t0 = max(0.0, t0)
    events = _card(lines, style, W, k, family, t0, t1, boxes, warnings, at_end=False)
    if info.outro and end_ms is not None and end_ms - info.outro_duration_ms > t1:
        e1 = end_ms
        e0 = e1 - info.outro_duration_ms
        if busy_until_ms is not None and busy_until_ms + 200 > e0:
            e0 = min(e1 - 2000, busy_until_ms + 200)
        events += _card(lines, style, W, k, family, max(e0, t1), e1, boxes, warnings, at_end=True)
    return events


def _card(lines: list[str], style: KaraokeStyle, W: int, k: float, family: str, t0: float, t1: float,
          boxes: Sequence[tuple[float, float, float, float, float, float]], warnings: Optional[list[str]], *,
          at_end: bool) -> list[str]:
    """One showing of the card from t0 to t1 (cut short by what else is drawn in its corner: at the
    start it leaves before that shows, at the end it comes in after that has gone)."""
    info, txt, glow = style.info, style.text, style.glow

    title_size = info.size * k
    sub_size = max(18 * k, title_size * 0.56)
    margin = info.margin * k
    bar_w = _BAR * k
    gap = 14 * k
    max_w = W * 0.42
    color = _bgr(info.color or txt.color_unsung)
    accent = _bgr(info.accent or txt.color_sung)
    outline_c = _bgr(txt.outline_color)

    # where each line goes (\an7 / \an9: the line box's top at y, its height the font size)
    placed = []
    y = bottom = margin
    for i, text in enumerate(lines):
        size = title_size if i == 0 else sub_size
        bold = txt.bold if i == 0 else False
        width = Measurer(family, bold, size).width(text)
        placed.append((text, size, bold, width, y))
        bottom = y + size
        y += size * (1.18 if i == 0 else 1.32)
    block_h = bottom - margin  # the accent bar is as tall as the text
    # the card's area (sliding in, outline and glow included) must stay clear of everything else
    pad = (txt.outline + (glow.size + glow.blur if glow.enabled else 0)) * k + 8 * k
    reach = bar_w + gap + min(max_w, max(w for *_, w, _ in placed)) + 28 * k

    def clear(right: bool) -> tuple[float, float]:
        """The card's time with nothing else where it would be (in that corner), 0.2 s apart."""
        cx0, cx1 = (W - margin - reach - pad, W - margin + pad) if right else (margin - pad, margin + reach + pad)
        cy0, cy1 = margin - pad, margin + block_h + pad
        start, end = t0, t1
        hits = [(b0, b1) for b0, b1, bx0, by0, bx1, by1 in boxes
                if bx0 < cx1 and cx0 < bx1 and by0 < cy1 and cy0 < by1]
        if at_end:  # comes in after the last of them has gone
            moved = True
            while moved:
                moved = False
                for b0, b1 in hits:
                    if b0 - 200 < end and b1 + 200 > start:
                        start, moved = b1 + 200, True
        else:  # leaves before the first of them shows
            for b0, b1 in hits:
                if b1 > start and b0 - 200 < end:
                    end = b0 - 200
        return start, end

    where = "结尾" if at_end else "开头"
    right = info.position == "top-right"
    n0, n1 = clear(right)
    if n1 - n0 < 1000:  # no room in its corner: the other one, else no card
        o0, o1 = clear(not right)
        if o1 - o0 < 1000:
            if warnings is not None:
                warnings.append(f"{where}的歌词 / 翻译会显示在歌曲信息的位置，{where}的歌曲信息没有显示"
                                f"（可调整它的{'显示时长' if at_end else '开始时间'}）")
            return []
        right, n0, n1 = not right, o0, o1
        if warnings is not None:
            warnings.append(f"{where}的歌词 / 翻译会显示在歌曲信息的位置，{where}的歌曲信息改到了另一侧")
    t0, t1 = n0, n1
    slide = 28 * k * (1 if right else -1)
    x_text = (W - margin - bar_w - gap) if right else (margin + bar_w + gap)
    an = 9 if right else 7

    events: list[str] = []
    for i, (text, size, bold, width, y) in enumerate(placed):
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
    from .ass import bgr_tag

    return bgr_tag(hex_rgb)


def _escape(text: str) -> str:
    from .ass import escape_text

    return escape_text(text)
