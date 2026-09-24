"""Karaoke subtitles as ASS, laid out chunk by chunk (see docs/karaoke.md).

Every lyric line becomes a row of *chunks*. A chunk is a piece of the line
that carries one ruby group (a kanji word and its reading) or plain text.
Each chunk is written as its own positioned event (lyric) plus an optional
ruby event above it, both with ``\\k``/``\\kf`` tags built from the unit times
of the alignment result (manual edits included). Nothing is animated beyond
the karaoke fill.

Times written here are on the original audio timeline plus ``time_offset_ms``
(the audio start inside a video, for burn-in / use with that video).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ..models import AlignmentResult, KaraokeStyle, Line, Project, Segment
from ..reading.japanese import is_kanji, to_hiragana
from .fonts import Measurer, default_family

REF_HEIGHT = 1080  # style pixel values are defined for this height
DEFAULT_SIZE = (1920, 1080)


# ---------------------------------------------------------------------------
# text helpers


def to_katakana(s: str) -> str:
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in s)


def has_kanji(s: str) -> bool:
    return any(is_kanji(c) for c in s)


def ass_color(hex_rgb: str, alpha_pct_transparent: int = 0) -> str:
    """#RRGGBB → &HAABBGGRR (alpha 0 = opaque)."""
    h = hex_rgb.lstrip("#")
    if len(h) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    a = max(0, min(255, round(alpha_pct_transparent * 255 / 100)))
    return f"&H{a:02X}{b}{g}{r}".upper()


def ass_time(ms: float) -> str:
    cs = max(0, int(round(ms / 10)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def escape_text(s: str) -> str:
    return s.replace("\\", "\\\\").replace("{", "｛").replace("}", "｝").replace("\n", " ")


# ---------------------------------------------------------------------------
# chunk model


@dataclass
class Part:
    text: str
    start: Optional[int]
    end: Optional[int]


@dataclass
class Chunk:
    base: list[Part]  # lyric text pieces with their own karaoke timing
    ruby: list[Part] = field(default_factory=list)  # empty = no ruby

    @property
    def base_text(self) -> str:
        return "".join(p.text for p in self.base)

    @property
    def ruby_text(self) -> str:
        return "".join(p.text for p in self.ruby)


@dataclass
class LaidLine:
    line: Line
    chunks: list[Chunk]
    start: int  # first sung time
    end: int  # last sung time
    translation: Optional[str] = None
    show_from: int = 0
    show_to: int = 0
    slot: int = 0


def _ruby_text(reading: str, script: str, romaji: Optional[str]) -> str:
    if script == "katakana":
        return to_katakana(to_hiragana(reading))
    if script == "romaji":
        return romaji if romaji is not None else reading
    return to_hiragana(reading)


def _split_affixes(seg: Segment) -> list[tuple[str, list]]:
    """Split kana prefix / okurigana off a kanji segment: [(surface, units)].

    Only splits where the reading boundary falls on a unit boundary, so every
    piece keeps whole units (and their times).
    """
    surface, units = seg.surface, seg.units
    reading = "".join(u.reading for u in units)
    if not has_kanji(surface) or not units:
        return [(surface, units)]
    hira = to_hiragana(surface)
    pre = 0
    while pre < len(surface) and not is_kanji(surface[pre]) and pre < len(reading) and hira[pre] == reading[pre]:
        pre += 1
    suf = 0
    while (suf < len(surface) - pre and not is_kanji(surface[-1 - suf]) and suf < len(reading) - pre
           and hira[-1 - suf] == reading[-1 - suf]):
        suf += 1

    def cut(n_chars: int, from_start: bool) -> Optional[int]:
        """number of units covering exactly n reading chars from the start / end."""
        acc = 0
        seq = units if from_start else list(reversed(units))
        for i, u in enumerate(seq):
            if acc == n_chars:
                return i
            acc += len(u.reading)
        return len(seq) if acc == n_chars else None

    k_pre = cut(pre, True) if pre else 0
    k_suf = cut(suf, False) if suf else 0
    if k_pre is None or k_suf is None or k_pre + k_suf >= len(units):
        return [(surface, units)]
    pieces = []
    if pre:
        pieces.append((surface[:pre], units[:k_pre]))
    pieces.append((surface[pre:len(surface) - suf], units[k_pre:len(units) - k_suf]))
    if suf:
        pieces.append((surface[len(surface) - suf:], units[len(units) - k_suf:]))
    return pieces


def build_chunks(line: Line, times: dict[str, tuple[Optional[int], Optional[int]]], style: KaraokeStyle,
                 romaji: dict[str, str]) -> list[Chunk]:
    ruby_cfg = style.ruby
    chunks: list[Chunk] = []
    for seg in line.segments:
        if not seg.units:
            chunks.append(Chunk([Part(seg.surface, None, None)]))
            continue
        pieces = _split_affixes(seg) if seg.lang == "ja" else [(seg.surface, seg.units)]
        for surface, units in pieces:
            t = [times.get(u.id, (None, None)) for u in units]
            kanji = has_kanji(surface)
            hira = to_hiragana(surface)
            readings = [u.reading for u in units]
            # kana (or latin) whose units map 1:1 onto the surface: per-unit karaoke
            if not kanji and seg.lang == "ja" and hira == "".join(readings):
                base, pos = [], 0
                for u, (s, e) in zip(units, t):
                    base.append(Part(surface[pos:pos + len(u.reading)], s, e))
                    pos += len(u.reading)
            elif all(u.surface for u in units) and "".join(u.surface for u in units) == surface:
                base = [Part(u.surface, s, e) for u, (s, e) in zip(units, t)]
            else:
                starts = [s for s, _ in t if s is not None]
                ends = [e for _, e in t if e is not None]
                base = [Part(surface, min(starts) if starts else None, max(ends) if ends else None)]
            ruby: list[Part] = []
            wants = ruby_cfg.enabled and seg.lang == "ja" and (kanji or ruby_cfg.target == "all")
            if wants:
                ruby = [Part(_ruby_text(u.reading, ruby_cfg.script, romaji.get(u.id)), s, e)
                        for u, (s, e) in zip(units, t)]
                if "".join(p.text for p in ruby) == surface:
                    ruby = []  # e.g. hiragana ruby over hiragana
            chunks.append(Chunk(base, ruby))
    _fill_missing_times(chunks)
    return chunks


def _fill_missing_times(chunks: list[Chunk]) -> None:
    """Untimed parts (punctuation, unaligned units) highlight instantly at
    their neighbour's boundary; this only affects display, never the data."""
    seq = [p for c in chunks for p in c.base]
    last = None
    for p in seq:
        if p.start is None or p.end is None:
            p.start = p.end = last
        else:
            last = p.end
    nxt = None
    for p in reversed(seq):
        if p.start is None:
            p.start = p.end = nxt
        else:
            nxt = p.start
    for c in chunks:
        for p in c.ruby:
            if p.start is None or p.end is None:
                p.start = c.base[0].start
                p.end = c.base[-1].end


# ---------------------------------------------------------------------------
# scheduling and layout


def schedule(lines: list[LaidLine], style: KaraokeStyle) -> None:
    """Give each line a display window and a slot (rows stacked on screen).

    A line appears ``lead_in_ms`` before its first syllable, but never before
    the previous line in the same slot has gone; that line's hold is cut short
    if needed so each line is visible at least 0.2 s before it is sung.
    """
    tm = style.timing
    n = max(1, style.layout.lines)
    lead, hold = tm.lead_in_ms, tm.hold_ms
    early = max(lead, tm.early_max_ms) if tm.early_show else lead
    last_in_slot: dict[int, LaidLine] = {}
    for i, ll in enumerate(lines):
        ll.slot = i % n
        ll.show_to = ll.end + hold
        prev = last_in_slot.get(ll.slot)
        free_at = prev.show_to if prev is not None else 0
        # appear when the slot frees up (early_show), but at least `lead` ahead
        ll.show_from = max(0, ll.start - early, min(free_at, ll.start - lead))
        if prev is not None and prev.show_to > ll.show_from:
            latest = max(prev.end, ll.start - 200)
            ll.show_from = max(ll.show_from, min(prev.show_to, latest))
            prev.show_to = min(prev.show_to, ll.show_from)
        last_in_slot[ll.slot] = ll


def _karaoke(parts: list[Part], t0: int, tag: str) -> str:
    out, cursor = [], 0
    for p in parts:
        s = int(round((p.start - t0) / 10)) if p.start is not None else cursor
        e = int(round((p.end - t0) / 10)) if p.end is not None else s
        s = max(s, cursor)
        e = max(e, s)
        if s > cursor:
            out.append(f"{{\\k{s - cursor}}}")
        out.append(f"{{\\{tag}{e - s}}}{escape_text(p.text)}")
        cursor = e
    return "".join(out)


def chunk_widths(chunks: list[Chunk], m_main, m_ruby, ruby_size: float, fit: str) -> list[float]:
    """Width of each chunk on the line.

    A ruby wider than its lyric may overhang a neighbour that has no ruby of
    its own (usually kana) by up to one ruby character on each side, as in
    normal Japanese typesetting. With ``fit == "widen"`` the lyric is spaced
    out only by what is still needed, so a reading never sits over another
    kanji's reading; ``"overflow"`` never widens.
    """
    base = [m_main.width(c.base_text) for c in chunks]
    if fit != "widen" or m_ruby is None:
        return base
    out = []
    for i, (c, bw) in enumerate(zip(chunks, base)):
        if not c.ruby:
            out.append(bw)
            continue
        need = m_ruby.width(c.ruby_text) + ruby_size * 0.1 - bw
        if need <= 0:
            out.append(bw)
            continue
        # line edges are free too: nothing to collide with there
        left_free = i == 0 or not chunks[i - 1].ruby
        right_free = i == len(chunks) - 1 or not chunks[i + 1].ruby
        # the ruby is centred, so the overhang is symmetric: limited by the tighter side
        allowance = ruby_size if (left_free and right_free) else 0.0
        out.append(bw + max(0.0, need - 2 * allowance))
    return out


def _unit_times(result: AlignmentResult) -> dict[str, tuple[Optional[int], Optional[int]]]:
    return {u.unit_id: (u.start_ms, u.end_ms) for u in result.units}


def _romaji(project: Project) -> dict[str, str]:
    from ..reading.profiles import get_profile

    prof = get_profile("ja-hepburn")
    out: dict[str, str] = {}
    for ln in project.lyrics.lines:
        units = [u for s in ln.segments for u in s.units]
        segs = [s for s in ln.segments for _ in s.units]
        if not units:
            continue
        texts = prof.unit_texts([u.reading for u in units], [s.lang for s in segs], [u.flags for u in units])
        for u, t in zip(units, texts):
            out[u.id] = t
    return out


def resolution(project: Project) -> tuple[int, int]:
    v = project.video
    if v is not None and v.width and v.height:
        return int(v.width), int(v.height)
    return DEFAULT_SIZE


def build_ass(project: Project, result: AlignmentResult, style: Optional[KaraokeStyle] = None, *,
              time_offset_ms: float = 0.0, size: Optional[tuple[int, int]] = None) -> tuple[str, list[str]]:
    """Return (ASS text, warnings)."""
    style = style or project.karaoke
    W, H = size or resolution(project)
    k = H / REF_HEIGHT
    lay, txt, rb = style.layout, style.text, style.ruby
    family = txt.font or default_family()
    ruby_family = (rb.font or family) if rb.enabled else family
    main_size = txt.size * k
    ruby_size = main_size * rb.size_pct / 100
    trans_size = main_size * lay.translation_size_pct / 100
    gap = rb.gap * k
    trans_gap = 6 * k
    m_main = Measurer(family, txt.bold, main_size)
    m_ruby = Measurer(ruby_family, txt.bold, ruby_size) if rb.enabled else None
    times = _unit_times(result)
    romaji = _romaji(project) if (rb.enabled and rb.script == "romaji") else {}
    warnings: list[str] = []

    covered = set(result.coverage.line_ids) if not result.coverage.full else None
    laid: list[LaidLine] = []
    skipped = 0
    for ln in project.lyrics.sung_lines():
        if covered is not None and ln.id not in covered:
            continue
        chunks = build_chunks(ln, times, style, romaji)
        starts = [p.start for c in chunks for p in c.base if p.start is not None]
        ends = [p.end for c in chunks for p in c.base if p.end is not None]
        if not starts or not ends:
            skipped += 1
            continue
        laid.append(LaidLine(ln, chunks, min(starts), max(ends),
                             translation=ln.translation if lay.show_translation else None))
    if skipped:
        warnings.append(f"{skipped} 行没有任何时间，未写入字幕")
    laid.sort(key=lambda x: x.start)
    schedule(laid, style)

    has_ruby = rb.enabled and any(c.ruby for ll in laid for c in ll.chunks)
    slot_h = main_size + ((ruby_size + gap) if has_ruby else 0) + ((trans_size + trans_gap) if lay.show_translation else 0)
    n = max(1, lay.lines)
    spacing = lay.line_spacing * k
    margin_v = lay.margin_v * k
    margin_h = lay.margin_h * k
    block_h = n * slot_h + (n - 1) * spacing
    block_top = (H - margin_v - block_h) if lay.position == "bottom" else margin_v

    events: list[str] = []
    tag = "kf" if style.timing.highlight == "sweep" else "k"
    for ll in laid:
        widths = chunk_widths(ll.chunks, m_main, m_ruby, ruby_size, rb.fit)
        line_w = sum(widths) or 1.0
        avail = W - 2 * margin_h
        scale = min(1.0, avail / line_w) if lay.shrink_long_lines else 1.0
        if scale < 1.0:
            warnings.append(f"「{ll.line.text}」过长，已缩小到 {scale:.0%}")
        line_w *= scale
        align = "center"
        if lay.arrangement == "alternate" and n > 1:
            align = "left" if ll.slot == 0 else ("right" if ll.slot == n - 1 else "center")
        indent = lay.alternate_indent * k if align != "center" else 0.0
        free = max(0.0, avail - line_w)  # room left inside the margins
        inset = min(indent, free)  # a long line slides back toward its edge
        x0 = {"left": margin_h + inset, "right": W - margin_h - inset - line_w, "center": (W - line_w) / 2}[align]
        slot_top = block_top + ll.slot * (slot_h + spacing)
        main_y = slot_top + ((ruby_size + gap) if has_ruby else 0) * 1.0 + main_size
        # shrunk lines keep their bottom edge where it was
        ruby_y = main_y - main_size * scale - gap * scale
        start, end = ass_time(ll.show_from + time_offset_ms), ass_time(ll.show_to + time_offset_ms)
        fs = "" if scale >= 1.0 else f"\\fscx{scale * 100:.1f}\\fscy{scale * 100:.1f}"
        x = x0
        for c, w in zip(ll.chunks, widths):
            cx = x + w * scale / 2
            body = _karaoke(c.base, ll.show_from, tag)
            events.append(f"Dialogue: 1,{start},{end},KMain,,0,0,0,,{{\\an2\\pos({cx:.1f},{main_y:.1f}){fs}}}{body}")
            if c.ruby:
                rbody = _karaoke(c.ruby, ll.show_from, tag)
                events.append(f"Dialogue: 2,{start},{end},KRuby,,0,0,0,,{{\\an2\\pos({cx:.1f},{ruby_y:.1f}){fs}}}{rbody}")
            x += w * scale
        if ll.translation:
            ty = main_y + trans_gap + trans_size
            an, tx = {"left": (1, x0), "right": (3, x0 + line_w), "center": (2, W / 2)}[align]
            events.append(f"Dialogue: 0,{start},{end},KTrans,,0,0,0,,{{\\an{an}\\pos({tx:.1f},{ty:.1f})}}"
                          f"{escape_text(ll.translation)}")

    def style_line(name: str, font: str, size: float, sung: str, unsung: str, outline_c: str, outline: float) -> str:
        return (f"Style: {name},{font},{size:.1f},{ass_color(sung)},{ass_color(unsung)},{ass_color(outline_c)},"
                f"{ass_color(txt.shadow_color, 100 - txt.shadow_opacity)},{-1 if txt.bold else 0},0,0,0,100,100,0,0,1,"
                f"{outline * k:.2f},{txt.shadow * k:.2f},2,0,0,0,1")

    rc = txt if rb.follow_colors else rb
    header = [
        "[Script Info]",
        "; generated by Kara Align",
        "ScriptType: v4.00+",
        f"PlayResX: {W}",
        f"PlayResY: {H}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        style_line("KMain", family, main_size, txt.color_sung, txt.color_unsung, txt.outline_color, txt.outline),
        style_line("KRuby", ruby_family, ruby_size, rc.color_sung, rc.color_unsung, rc.outline_color,
                   rb.outline if not rb.follow_colors else max(1.0, txt.outline * 0.6)),
        style_line("KTrans", family, trans_size, txt.color_unsung, txt.color_unsung, txt.outline_color,
                   max(1.0, txt.outline * 0.6)),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    return "\n".join(header + events) + "\n", warnings
