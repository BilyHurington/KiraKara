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
from .fonts import Measurer, default_family, installed

REF_WIDTH = 1920  # style pixel values are defined for a frame this wide; other widths scale
DEFAULT_SIZE = (1920, 1080)
PAUSE_HIDE_MS = 6000  # a pause inside a line at least this long hides the line meanwhile


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
    """User text as literal ASS text.  libass has no escape for a backslash (``\\\\`` is shown
    as two, and a trailing one before the next ``{\\k..}`` breaks the tag), and ``\\N`` /
    ``\\n`` / ``\\h`` are line breaks / spaces: backslashes and braces become full-width."""
    return s.replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\n", " ")


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
    units: list[tuple[int, int]] = field(default_factory=list)  # timed units (start, end), for pauses


def _ruby_text(reading: str, script: str, romaji: Optional[str]) -> str:
    if script == "katakana":
        return to_katakana(to_hiragana(reading))
    if script == "romaji":
        return romaji if romaji is not None else reading
    return to_hiragana(reading)


def _split_affixes(seg: Segment) -> list[tuple[str, list]]:
    """Split the kana of a kanji word off its kanji: [(surface, units)].

    Handles a kana prefix, okurigana and kana between kanji (笑い合え →
    笑 い 合 え), so ruby sits only over kanji.  Only splits where the
    reading boundary falls on a unit boundary, so every piece keeps whole
    units (and their times); otherwise the word stays one piece.
    """
    surface, units = seg.surface, seg.units
    if not has_kanji(surface) or not units:
        return [(surface, units)]
    reading = "".join(u.reading for u in units)
    runs = [(m.group(), bool(m.group(1))) for m in re.finditer(r"([^\u3041-\u30ff]+)|([\u3041-\u30ff]+)", surface)]
    if len(runs) == 1:
        return [(surface, units)]
    # kana runs must appear literally in the reading; kanji runs take at least one kana
    pattern = "".join(("(.+?)" if is_k else f"({re.escape(to_hiragana(text))})") for text, is_k in runs)
    m = re.fullmatch(pattern, reading)
    if m is None:
        return [(surface, units)]
    edges = [0]
    for g in m.groups():
        edges.append(edges[-1] + len(g))
    unit_edges = [0]
    for u in units:
        unit_edges.append(unit_edges[-1] + len(u.reading))
    pieces: list[tuple[str, list]] = []
    ui = 0
    text_parts: list[str] = []
    for (text, _), end in zip(runs, edges[1:]):
        text_parts.append(text)
        if end not in unit_edges:
            continue  # the boundary cuts a unit: keep this run with the next one
        k = unit_edges.index(end)
        pieces.append(("".join(text_parts), units[ui:k]))
        ui, text_parts = k, []
    if text_parts or ui < len(units):
        return [(surface, units)]
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


def visible_spans(ll: LaidLine, style: KaraokeStyle) -> list[tuple[int, int]]:
    """Display intervals of a scheduled line.

    Normally one interval.  If the line itself contains a long pause (the
    singer stops mid-line for an interlude), the line is hidden during it:
    it stays ``hold_ms`` after the last sung part before the pause and comes
    back ``lead_in_ms`` before singing resumes.
    """
    tm = style.timing
    min_pause = max(PAUSE_HIDE_MS, tm.hold_ms + tm.lead_in_ms + 2000)
    times = ll.units or [(p.start, p.end) for c in ll.chunks for p in c.base
                         if p.start is not None and p.end is not None]
    spans: list[tuple[int, int]] = []
    a = ll.show_from
    reach: Optional[int] = None
    for s, e in sorted(times):
        if reach is not None and s - reach >= min_pause:
            spans.append((a, reach + tm.hold_ms))
            a = s - tm.lead_in_ms
        reach = e if reach is None else max(reach, e)
    spans.append((a, ll.show_to))
    return [(x, y) for x, y in spans if y > x]


def translation_windows(laid: list[LaidLine], style: KaraokeStyle) -> list[tuple[LaidLine, int, int]]:
    """When each line's translation is shown as a single line (not under its lyric).

    One translation at a time, following the singing: from shortly before a line
    is sung until it is done (plus the hold), cut short when the next line starts.
    """
    tm = style.timing
    lead = min(tm.lead_in_ms, 800)
    out: list[tuple[LaidLine, int, int]] = []
    lines = [ll for ll in laid if ll.translation]
    prev_to = 0
    for i, ll in enumerate(lines):
        t_to = ll.end + tm.hold_ms
        if i + 1 < len(lines):
            t_to = min(t_to, max(lines[i + 1].start - lead, ll.end))
        t_from = max(ll.start - lead, prev_to, 0)
        if t_to > t_from:
            out.append((ll, t_from, t_to))
            prev_to = t_to
    return out


def translation_placements(laid: list[LaidLine], style: KaraokeStyle, W: int, H: int, block_top: float,
                           block_h: float, size: float, measurer, margin_h: float,
                           margin_v: float) -> list[tuple[int, int, str, str]]:
    """(from, to, position tags, text): the translation as one line at the other
    edge of the frame or just outside the lyric block."""
    lay = style.layout
    bottom = lay.position == "bottom"
    gap = size * 0.6
    if style.translation.position == "opposite":
        an, y = (8, margin_v) if bottom else (2, H - margin_v)
    else:  # "block": right outside the lyric block, on the side away from the edge
        an, y = (2, block_top - gap) if bottom else (8, block_top + block_h + gap)
    avail = W - 2 * margin_h
    out = []
    for ll, t0, t1 in translation_windows(laid, style):
        text = ll.translation or ""
        w = measurer.width(text) or 1.0
        fs = f"\\fscx{avail / w * 100:.1f}\\fscy{avail / w * 100:.1f}" if w > avail else ""
        out.append((t0, t1, f"\\an{an}\\pos({W / 2:.1f},{y:.1f}){fs}", text))
    return out


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


# Layers, bottom to top: effects drawn behind the text, translation glow, translation,
# text glow (unsung), text glow (sung), lyrics, ruby; then effects in front (7) and the title card (8, 9).
L_FX, L_TRANS_GLOW, L_TRANS, L_GLOW, L_GLOW_SUNG, L_MAIN, L_RUBY = 0, 1, 2, 3, 4, 5, 6


def build_ass(project: Project, result: AlignmentResult, style: Optional[KaraokeStyle] = None, *,
              time_offset_ms: float = 0.0, size: Optional[tuple[int, int]] = None) -> tuple[str, list[str]]:
    """Return (ASS text, warnings)."""
    from .effects import FX_STYLE, Syllable, syllable_events
    from .info import info_events, info_style

    style = style or project.karaoke
    audio_offset_ms = time_offset_ms  # the title card keeps real time
    # show / highlight everything a little before it is sung (display only)
    time_offset_ms -= style.timing.advance_ms
    W, H = size or resolution(project)
    k = W / REF_WIDTH  # by width: the text takes the same share of the frame's width at any size
    lay, txt, rb, tr, glow, tm = style.layout, style.text, style.ruby, style.translation, style.glow, style.timing
    # a style may name a font this machine does not have (styles travel between machines, the built-in
    # 暖阳 uses macOS fonts): then the default font is used for measuring *and* drawing, so the layout
    # still matches what libass draws
    missing_fonts: list[str] = []

    def usable(name: str) -> str:
        if name and not installed(name):
            missing_fonts.append(name)
            return ""
        return name

    family = usable(txt.font) or default_family()
    ruby_family = (usable(rb.font) or family) if rb.enabled else family
    trans_family = usable(tr.font) or family
    main_size = txt.size * k
    ruby_size = main_size * rb.size_pct / 100
    trans_size = main_size * tr.size_pct / 100
    gap = rb.gap * k
    trans_gap = 6 * k
    m_main = Measurer(family, txt.bold, main_size)
    m_trans = Measurer(trans_family, tr.bold, trans_size)
    m_ruby = Measurer(ruby_family, txt.bold, ruby_size) if rb.enabled else None
    times = _unit_times(result)
    romaji = _romaji(project) if (rb.enabled and rb.script == "romaji") else {}
    warnings: list[str] = []
    if missing_fonts:
        warnings.append(f"这台电脑没有字体 {'、'.join(sorted(set(missing_fonts)))}，已改用 {family}")

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
        unit_times = [times[u.id] for u in ln.units() if u.id in times and None not in times[u.id]]
        laid.append(LaidLine(ln, chunks, min(starts), max(ends),
                             translation=(ln.translation or None) if tr.enabled else None,
                             units=unit_times))  # type: ignore[arg-type]
    if skipped:
        warnings.append(f"{skipped} 行没有任何时间，未写入字幕")
    if tr.enabled and not any(ll.translation for ll in laid):
        warnings.append("已开启翻译字幕，但歌词里没有翻译")
    laid.sort(key=lambda x: x.start)
    schedule(laid, style)

    has_ruby = rb.enabled and any(c.ruby for ll in laid for c in ll.chunks)
    per_line_trans = tr.enabled and tr.position == "line"
    slot_h = main_size + ((ruby_size + gap) if has_ruby else 0) + ((trans_size + trans_gap) if per_line_trans else 0)
    n = max(1, lay.lines)
    spacing = lay.line_spacing * k
    margin_v = lay.margin_v * k
    margin_h = lay.margin_h * k
    block_h = n * slot_h + (n - 1) * spacing
    block_top = (H - margin_v - block_h) if lay.position == "bottom" else margin_v

    fad = f"\\fad({tm.fade_in_ms},{tm.fade_out_ms})" if (tm.fade_in_ms or tm.fade_out_ms) else ""
    g_alpha = f"&H{int(round(255 * (1 - glow.strength / 100))):02X}&"

    def glow_tags(color: str, width: float, font: str, size: float, bold: bool) -> str:
        return (f"\\fn{font}\\fs{size:.1f}\\b{1 if bold else 0}\\3c{_bgr_tag(color)}\\3a{g_alpha}"
                f"\\bord{width:.1f}\\blur{glow.blur * k:.1f}\\shad0")

    events: list[str] = []
    first_shown: dict[str, float] = {}  # style -> first time an event of it shows

    def emit(layer: int, t_from: float, t_to: float, name: str, tags: str, body: str) -> None:
        first_shown[name] = min(first_shown.get(name, float("inf")), t_from + time_offset_ms)
        events.append(f"Dialogue: {layer},{ass_time(t_from + time_offset_ms)},{ass_time(t_to + time_offset_ms)},"
                      f"{name},,0,0,0,,{{{tags}{fad}}}{body}")

    def emit_text(layer: int, t_from: float, t_to: float, name: str, pos: str, parts: list[Part], width: float,
                  with_glow: bool, font: str, size: float) -> None:
        """A karaoke text event, with its glow layers when the glow is on."""
        if glow.enabled and with_glow:
            plain = escape_text("".join(p.text for p in parts))
            emit(L_GLOW, t_from, t_to, "KGlow", pos + glow_tags(glow.color_unsung, width, font, size, txt.bold), plain)
            # \ko: the border (the glow) appears as each syllable is sung
            emit(L_GLOW_SUNG, t_from, t_to, "KGlow", pos + glow_tags(glow.color_sung, width, font, size, txt.bold),
                 _karaoke(parts, int(t_from), "ko"))
        emit(layer, t_from, t_to, name, pos, _karaoke(parts, int(t_from), tag))

    ruby_unsung = (txt if rb.follow_colors else rb).color_unsung

    def line_clip(chunks: list[Chunk], cxs: list[float], t0: int) -> str:
        """An animated \\clip whose right edge is where the lyric's sweep is on this line.

        The lyric's \\kf fills each piece from its left to its right edge over the piece's time
        (\\k: at once when it starts) and jumps over the space between pieces; the ruby's sung
        part is cut at exactly the same x, so both are swept by one vertical line.  Nothing is
        sung before the line's first piece starts.
        """
        segs: list[tuple[int, int, float, float]] = []  # (start, end) ms from t0, x from, x to
        for c, cx in zip(chunks, cxs):
            widths = [m_main.width(p.text) for p in c.base]
            bw = m_main.width(c.base_text) * scale
            norm = bw / (sum(widths) or 1.0)
            x, cursor = cx - bw / 2, 0
            for p, pw in zip(c.base, widths):
                # the same centisecond timing as _karaoke()
                s = int(round((p.start - t0) / 10)) if p.start is not None else cursor
                e = int(round((p.end - t0) / 10)) if p.end is not None else s
                s = max(s, cursor)
                e = max(e, s)
                segs.append((s * 10, e * 10, x, x + pw * norm))
                x += pw * norm
                cursor = e
        segs.sort(key=lambda g: g[0])

        def clip(x: float) -> str:
            return f"\\clip(0,0,{int(round(x))},{H})"

        tags, cur = [clip(0)], None
        for s, e, xa, xb in segs:
            if cur is None or abs(xa - cur) > 0.5:  # on to the next piece
                tags.append(f"\\t({s},{s + 1},{clip(xa)})")
            if tm.highlight == "sweep" and e > s:
                tags.append(f"\\t({s},{e},{clip(xb)})")
            else:
                tags.append(f"\\t({s},{s + 1},{clip(xb)})")
            cur = xb
        return "".join(tags)

    def emit_following(layer: int, name: str, t_from: float, t_to: float, pos: str, text: str, unsung: str,
                       font: str, size: float, glow_width: float, with_glow: bool, clip: str) -> None:
        """Text swept by a moving \\clip instead of \\kf: the whole text in the unsung colour, and
        the sung colour (the style's primary) cut at ``clip`` (line_clip()).  Lyric and ruby of a
        line share the clip, so their sung parts end at the same x."""
        plain = escape_text(text)
        if glow.enabled and with_glow:
            emit(L_GLOW, t_from, t_to, "KGlow", pos + glow_tags(glow.color_unsung, glow_width, font, size, txt.bold),
                 plain)
            emit(L_GLOW_SUNG, t_from, t_to, "KGlow",
                 pos + glow_tags(glow.color_sung, glow_width, font, size, txt.bold) + clip, plain)
        emit(layer, t_from, t_to, name, pos + f"\\1c{_bgr_tag(unsung)}", plain)
        emit(layer, t_from, t_to, name, pos + "\\shad0" + clip, plain)  # outline drawn again: no fringe

    def emit_trans(t_from: float, t_to: float, pos: str, text: str) -> None:
        if glow.enabled and tr.glow:
            emit(L_TRANS_GLOW, t_from, t_to, "KGlow", pos + glow_tags(glow.color_unsung, glow.size * k * 0.7, trans_family, trans_size, tr.bold),
                 escape_text(text))
        emit(L_TRANS, t_from, t_to, "KTrans", pos, escape_text(text))

    tag = "kf" if tm.highlight == "sweep" else "k"
    fx_on = style.effects.kind != "none"
    syllables: list[Syllable] = []
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
        fs = "" if scale >= 1.0 else f"\\fscx{scale * 100:.1f}\\fscy{scale * 100:.1f}"
        spans = visible_spans(ll, style)
        if len(spans) > 1:
            pause = (spans[1][0] - spans[0][1] + tm.hold_ms + tm.lead_in_ms) / 1000
            warnings.append(f"「{ll.line.text}」中间停顿约 {pause:.0f} 秒，停顿期间暂时隐藏该行")
        for t_from, t_to in spans:
            # an ASS event cannot start before 0:00: start it there and time the sweep from there,
            # or the fill would lag by what was cut off
            t_from = max(t_from, -time_offset_ms)
            cxs, x = [], x0
            for w in widths:
                cxs.append(x + w * scale / 2)
                x += w * scale
            ruby_clip = line_clip(ll.chunks, cxs, int(t_from)) if rb.sweep == "base" and has_ruby else ""
            x = x0
            for c, w, cx in zip(ll.chunks, widths, cxs):
                main_pos = f"\\an2\\pos({cx:.1f},{main_y:.1f}){fs}"
                ruby_pos = f"\\an2\\pos({cx:.1f},{ruby_y:.1f}){fs}"
                if ruby_clip:
                    # lyric and ruby cut by one computed line: libass places its own \\kf boundary by
                    # glyph ink, a few pixels away from any position computed outside it
                    emit_following(L_MAIN, "KMain", t_from, t_to, main_pos, c.base_text, txt.color_unsung,
                                   family, main_size, glow.size * k, True, ruby_clip)
                else:
                    emit_text(L_MAIN, t_from, t_to, "KMain", main_pos, c.base, glow.size * k, True, family,
                              main_size)
                if c.ruby and ruby_clip:
                    emit_following(L_RUBY, "KRuby", t_from, t_to, ruby_pos, c.ruby_text, ruby_unsung, ruby_family,
                                   ruby_size, glow.size * k * 0.55, glow.ruby, ruby_clip)
                elif c.ruby:
                    emit_text(L_RUBY, t_from, t_to, "KRuby", ruby_pos, c.ruby, glow.size * k * 0.55, glow.ruby,
                              ruby_family, ruby_size)
                if fx_on:
                    group = f"{ll.line.id}@{t_from}"
                    top = (ruby_y - ruby_size * scale) if has_ruby else (main_y - main_size * scale)
                    syllables += _syllables(c.base, cx, main_y, main_size, scale, m_main, family, False, t_from, t_to,
                                            group, top)
                    if c.ruby and style.effects.ruby and m_ruby is not None:
                        syllables += _syllables(c.ruby, cx, ruby_y, ruby_size, scale, m_ruby, ruby_family, True,
                                                t_from, t_to, group, top)
                x += w * scale
            if ll.translation and per_line_trans:
                ty = main_y + trans_gap + trans_size
                # a translation wider than the room between the margins is shrunk, and kept inside them
                tw = m_trans.width(ll.translation) or 1.0
                tfs = f"\\fscx{avail / tw * 100:.1f}\\fscy{avail / tw * 100:.1f}" if tw > avail else ""
                tw = min(tw, avail)
                an, tx = {"left": (1, max(margin_h, min(x0, W - margin_h - tw))),
                          "right": (3, min(W - margin_h, max(x0 + line_w, margin_h + tw))),
                          "center": (2, W / 2)}[align]
                emit_trans(t_from, t_to, f"\\an{an}\\pos({tx:.1f},{ty:.1f}){tfs}", ll.translation)

    if tr.enabled and not per_line_trans:
        for t0, t1, pos, text in translation_placements(laid, style, W, H, block_top, block_h, trans_size,
                                                        Measurer(trans_family, tr.bold, trans_size), margin_h,
                                                        margin_v):
            emit_trans(t0, t1, pos, text)

    if fx_on:
        for layer, t0, t1, tags, body in syllable_events(style, syllables, k):
            events.append(f"Dialogue: {layer},{ass_time(t0 + time_offset_ms)},{ass_time(t1 + time_offset_ms)},"
                          f"KFx,,0,0,0,,{{{tags}}}{body}")

    # the title card leaves before anything else appears along the top edge
    top_busy = [first_shown.get("KMain")] if lay.position == "top" else []
    if tr.enabled and tr.position == "opposite":
        top_busy.append(first_shown.get("KTrans"))
    busy = [t for t in top_busy if t is not None]
    events += info_events(project, style, W, H, k, family, audio_offset_ms, min(busy) if busy else None)

    shadow_back = ass_color(txt.shadow_color, 100 - txt.shadow_opacity)

    def style_line(name: str, font: str, size: float, sung: str, unsung: str, outline_c: str, outline: float,
                   shadow: float, bold: bool, fill_alpha: int = 0) -> str:
        return (f"Style: {name},{font},{size:.1f},{ass_color(sung, fill_alpha)},{ass_color(unsung, fill_alpha)},"
                f"{ass_color(outline_c)},{shadow_back},{-1 if bold else 0},0,0,0,100,100,0,0,1,"
                f"{outline * k:.2f},{shadow * k:.2f},2,0,0,0,1")

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
        style_line("KMain", family, main_size, txt.color_sung, txt.color_unsung, txt.outline_color, txt.outline,
                   txt.shadow, txt.bold),
        style_line("KRuby", ruby_family, ruby_size, rc.color_sung, rc.color_unsung, rc.outline_color,
                   rb.outline if not rb.follow_colors else max(1.0, txt.outline * 0.6), txt.shadow * 0.6, txt.bold),
        style_line("KTrans", trans_family, trans_size, tr.color, tr.color, tr.outline_color, tr.outline, tr.shadow,
                   tr.bold),
        # glow layers: invisible fill, the (blurred) border is the glow; sizes set per event
        style_line("KGlow", family, main_size, "#FFFFFF", "#FFFFFF", "#FFFFFF", 0, 0, txt.bold, fill_alpha=100),
        FX_STYLE,
        info_style(family, main_size),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    return "\n".join(header + events) + "\n", warnings


def _syllables(parts: list[Part], cx: float, bottom_y: float, size: float, scale: float, measurer, font: str,
               ruby: bool, t_from: float, t_to: float, group: str = "", top: float = 0.0) -> list:
    """Where each sung piece of a chunk sits on screen (the chunk text is centred at ``cx``)."""
    from .effects import Syllable

    widths = [measurer.width(p.text) * scale for p in parts]
    left = cx - sum(widths) / 2
    out = []
    for p, w in zip(parts, widths):
        if p.start is not None and p.end is not None and p.text.strip() and t_from <= p.start < t_to:
            out.append(Syllable(text=p.text, start=int(p.start), end=int(p.end), x=left + w / 2,
                                y=bottom_y - size * scale * 0.55, w=max(w, size * scale * 0.4), h=size * scale,
                                font=font, size=size * scale, ruby=ruby, visible_until=int(t_to), group=group,
                                top=top))
        left += w
    return out


def _bgr_tag(hex_rgb: str) -> str:
    """#RRGGBB -> &HBBGGRR& (colour override tag value)."""
    h = hex_rgb.lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()
