"""Karaoke subtitles as ASS, laid out chunk by chunk.

Every lyric line becomes a row of *chunks*.  A chunk is a piece of the line
that carries one ruby group (a kanji word and its reading) or plain text; kana
around kanji are split off so ruby sits only over kanji.  Widths are measured
with the fonts libass will draw with (``fonts.Measurer``), so the layout is
computed here: chunk widths (a reading may overhang a neighbour without ruby,
never another reading), the line's place in its row (alternating left / right,
shrunk to fit the margins), the row (``schedule``: lines take turns in
``layout.lines`` rows; a line sung while every row is still busy — a duet, a
backing vocal — gets an extra row beyond the block) and when it shows
(``lead_in`` / ``hold`` / early show, hidden across long pauses inside it).

Each chunk is written as its own positioned event (lyric) plus an optional ruby
event above it:

* ruby ``own``: both are ``\\kf`` / ``\\k`` karaoke events built from the unit
  times of the alignment result (manual edits included);
* ruby ``base`` (与歌词对齐): lyric and ruby are drawn twice — unsung, and sung
  cut by an animated ``\\clip`` at the lyric's sweep position (the unsung copy by
  the matching ``\\iclip``, so fades never show one through the other).  The
  ruby's cut follows the lyric's while it is over the lyric and covers the whole
  reading once its chunk is sung.

Around that: optional glow layers (a blurred border under the text), the
translation (under each line, or one line at an edge), per-syllable effects
(``effects``) and the song title card (``info``).  Fades are shortened so a line
never fades while it is sung.

Times written here are on the original audio timeline plus ``time_offset_ms``
(the audio start inside a video, for burn-in / use with that video), minus the
style's ``advance_ms``.
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


def _rgb(hex_rgb: str) -> tuple[str, str, str]:
    """#RRGGBB → ("RR", "GG", "BB"); anything else is white (styles are validated, this is a last guard)."""
    h = (hex_rgb or "").strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        h = "FFFFFF"
    h = h.upper()
    return h[0:2], h[2:4], h[4:6]


def ass_color(hex_rgb: str, alpha_pct_transparent: float = 0) -> str:
    """#RRGGBB → &HAABBGGRR (alpha 0 = opaque)."""
    r, g, b = _rgb(hex_rgb)
    a = max(0, min(255, round(alpha_pct_transparent * 255 / 100)))
    return f"&H{a:02X}{b}{g}{r}"


def bgr_tag(hex_rgb: str) -> str:
    """#RRGGBB → &HBBGGRR& (colour override tag value)."""
    r, g, b = _rgb(hex_rgb)
    return f"&H{b}{g}{r}&"


_bgr_tag = bgr_tag


def ass_time(ms: float) -> str:
    cs = max(0, int(round(ms / 10)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


# line / paragraph separators and other control characters: one space each
_BREAKS = {c: " " for c in [*range(0x00, 0x20), 0x7F, 0x85, 0x2028, 0x2029]}
_ESCAPE = str.maketrans({**_BREAKS, "\\": "＼", "{": "｛", "}": "｝"})


def escape_text(s: str) -> str:
    """User text as literal ASS text.  libass has no escape for a backslash (``\\\\`` is shown
    as two, and a trailing one before the next ``{\\k..}`` breaks the tag), and ``\\N`` /
    ``\\n`` / ``\\h`` are line breaks / spaces: backslashes and braces become full-width.
    Line breaks of any kind (``\\n``, ``\\r``, U+2028 …) and other control characters become
    spaces (an event is one line of the file)."""
    return s.translate(_ESCAPE)


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
    slot: int = 0  # row: 0 … lines-1 in the block; beyond it (-1, -2 / lines, lines+1) extra rows
    units: list[tuple[int, int]] = field(default_factory=list)  # timed units (start, end), for pauses


def _ruby_text(reading: str, script: str, romaji: Optional[str]) -> str:
    if script == "katakana":
        return to_katakana(to_hiragana(reading))
    if script == "romaji":
        return romaji if romaji is not None else reading
    return to_hiragana(reading)


def _kana_split(runs: list[tuple[str, bool]], reading: str) -> Optional[list[int]]:
    """Reading lengths of each run: kana runs match literally, kanji runs take at least one kana.

    Every valid split is tried.  The one where the kanji readings are the most even
    (the fewest kana on the busiest kanji) wins; if two splits are equally even the
    split is ambiguous (物の怪 / もののけ: も|の|のけ or もの|の|け) and None is returned.
    """
    found: list[list[int]] = []

    def walk(i: int, pos: int, acc: list[int]) -> None:
        if len(found) > 64:
            return
        if i == len(runs):
            if pos == len(reading):
                found.append(acc)
            return
        text, is_k = runs[i]
        if not is_k:
            kana = to_hiragana(text)
            if reading.startswith(kana, pos):
                walk(i + 1, pos + len(kana), acc + [len(kana)])
            return
        # leave at least one kana for every later kanji run and the literal kana runs
        rest = sum(1 if k else len(t) for t, k in runs[i + 1:])
        for n in range(1, len(reading) - pos - rest + 1):
            walk(i + 1, pos + n, acc + [n])

    walk(0, 0, [])
    if not found:
        return None

    def cost(lens: list[int]) -> float:
        return max(n / len(t) for n, (t, k) in zip(lens, runs) if k)

    best = min(cost(x) for x in found)
    top = [x for x in found if abs(cost(x) - best) < 1e-9]
    return top[0] if len(top) == 1 else None


def _split_affixes(seg: Segment) -> list[tuple[str, list]]:
    """Split the kana of a kanji word off its kanji: [(surface, units)].

    Handles a kana prefix, okurigana and kana between kanji (笑い合え →
    笑 い 合 え), so ruby sits only over kanji.  Only splits where the
    reading boundary falls on a unit boundary, so every piece keeps whole
    units (and their times); otherwise the word stays one piece.  A split the
    reading allows in more than one way is not made (_kana_split()).
    """
    surface, units = seg.surface, seg.units
    if not has_kanji(surface) or not units:
        return [(surface, units)]
    reading = "".join(to_hiragana(u.reading) for u in units)
    runs = [(m.group(), bool(m.group(1))) for m in re.finditer(r"([^ぁ-ヿ]+)|([ぁ-ヿ]+)", surface)]
    if len(runs) == 1:
        return [(surface, units)]
    lens = _kana_split(runs, reading)
    if lens is None:
        return [(surface, units)]
    edges = [0]
    for n in lens:
        edges.append(edges[-1] + n)
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


def schedule(lines: list[LaidLine], style: KaraokeStyle) -> int:
    """Give each line a display window and a slot (rows stacked on screen); return how many
    lines had to go to an extra row.

    Lines take the rows in turn.  A line appears ``lead_in_ms`` before its first syllable
    (with early show: as soon as its row is free, at most ``early_max_ms`` ahead), but never
    before the previous line in its row has gone; that line's hold is cut short if needed so
    each line is visible at least 0.2 s before it is sung.  A row whose line is still being
    sung is never taken: the line goes to the row that frees first, and when every row is
    busy (lines sung at the same time: a duet, a backing vocal inside a long line) to an
    extra row beyond the block (above it at the bottom of the frame, below it at the top).
    No line is ever dropped.
    """
    tm = style.timing
    n = max(1, style.layout.lines)
    lead, hold = max(0, tm.lead_in_ms), max(0, tm.hold_ms)
    early = max(lead, tm.early_max_ms) if tm.early_show else lead
    step = -1 if style.layout.position == "bottom" else 1  # extra rows go away from the frame edge
    last: dict[int, LaidLine] = {}
    turn = 0
    extra = 0
    for ll in lines:
        ll.show_to = ll.end + hold

        def appear(slot: int) -> int:
            prev = last.get(slot)
            free_at = prev.show_to if prev is not None else 0
            t = max(0, ll.start - early, min(free_at, ll.start - lead))
            if prev is not None and prev.show_to > t:
                t = max(t, min(prev.show_to, max(prev.end, ll.start - 200)))
            return t

        free = [s for s in range(n) if s not in last or last[s].end <= ll.start]
        natural = turn % n
        if free:
            if natural in free and appear(natural) <= ll.start - 200:
                slot = natural
            else:
                slot = min(free, key=lambda s: (appear(s), s != natural, s))
        else:
            extra += 1
            rows = sorted((s for s in last if not 0 <= s < n), key=abs)  # nearest the block first
            slot = next((s for s in rows if last[s].end <= ll.start), None)
            if slot is None:
                slot = (-1 if step < 0 else n) + step * len(rows)
        ll.slot = slot
        ll.show_from = appear(slot)
        prev = last.get(slot)
        if prev is not None:
            prev.show_to = min(prev.show_to, ll.show_from)
        last[slot] = ll
        if 0 <= slot < n:
            turn = slot + 1
    return extra


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


def alternate_insets(laid: list[LaidLine], geom: list[tuple], indent: float, avail: float) -> list[float]:
    """How far the left / right rows sit in from their margins (the alternating layout's indent).

    One indent for the whole song, so every left row starts at the same x and every right row ends
    at the same x.  It is the style's indent, reduced once as far as the lyrics need: every line
    must fit (a long line needs a smaller indent), and a left and a right row shown together keep
    the staircase — the upper (left) one starts and ends no further right than the lower (right)
    one, i.e. ``2 · indent <= avail − the longer line's extent``."""
    ext = [g[1] + g[2] + g[3] for g in geom]
    side = [i for i, g in enumerate(geom) if g[5] != "center"]
    inset = indent
    for i in side:
        inset = min(inset, max(0.0, avail - ext[i]))  # the line itself must fit
        for j in side:
            if geom[j][5] == geom[i][5] or laid[j].show_to <= laid[i].show_from or laid[j].show_from >= laid[i].show_to:
                continue
            inset = min(inset, max(0.0, avail - max(ext[i], ext[j])) / 2)
    inset = max(0.0, inset)
    return [0.0 if g[5] == "center" else inset for g in geom]


def _sung_within(ll: LaidLine, a: float, b: float) -> tuple[float, float]:
    """First start and last end of the line's singing inside the display span [a, b)."""
    times = ll.units or [(p.start, p.end) for c in ll.chunks for p in c.base
                         if p.start is not None and p.end is not None]
    inside = [(s, e) for s, e in times if a <= s < b] or [(ll.start, ll.end)]
    return min(s for s, _ in inside), max(e for _, e in inside)


def fade_tag(fade_in: float, fade_out: float, t_from: float, t_to: float, sung_from: float, sung_to: float) -> str:
    """\\fad shortened so the line never fades while it is sung (a line right after another
    in the same row has almost no time to ease in)."""
    fi = int(max(0, min(fade_in, sung_from - t_from)))
    fo = int(max(0, min(fade_out, t_to - sung_to)))
    return f"\\fad({fi},{fo})" if (fi or fo) else ""


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


Box = tuple[float, float, float, float, float, float]  # shown from, to (ASS time), x0, y0, x1, y1


def translation_placements(laid: list[LaidLine], style: KaraokeStyle, W: int, H: int, block_top: float,
                           block_h: float, size: float, measurer, margin_h: float,
                           margin_v: float) -> list[tuple[int, int, str, str, tuple[float, float, float, float]]]:
    """(from, to, position tags, text, (x0, y0, x1, y1)): the translation as one line at the
    other edge of the frame or just outside the lyric block."""
    lay = style.layout
    bottom = lay.position == "bottom"
    gap = size * 0.6
    if style.translation.position == "opposite":
        an, y = (8, margin_v) if bottom else (2, H - margin_v)
    else:  # "block": right outside the lyric block, on the side away from the edge
        an, y = (2, block_top - gap) if bottom else (8, block_top + block_h + gap)
    avail = max(1.0, W - 2 * margin_h)
    out = []
    for ll, t0, t1 in translation_windows(laid, style):
        text = ll.translation or ""
        w = measurer.width(text) or 1.0
        fs = f"\\fscx{avail / w * 100:.1f}\\fscy{avail / w * 100:.1f}" if w > avail else ""
        w, h = min(w, avail), size * min(1.0, avail / w)
        box = (W / 2 - w / 2, y if an == 8 else y - h, W / 2 + w / 2, y + h if an == 8 else y)
        out.append((t0, t1, f"\\an{an}\\pos({W / 2:.1f},{y:.1f}){fs}", text, box))
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
    its own (usually kana) by up to one ruby character, and by no more than
    that neighbour is wide — half of it when a ruby on its other side overhangs
    it too — so two readings never meet, as in normal Japanese typesetting.  At
    the line's edges it may overhang by one ruby character (counted in the
    line's extent, ruby_overhang()).  With ``fit == "widen"`` the lyric is spaced
    out only by what is still needed; ``"overflow"`` never widens.
    """
    base = [m_main.width(c.base_text) for c in chunks]
    if fit != "widen" or m_ruby is None:
        return base
    pad = ruby_size * 0.1
    need = [max(0.0, m_ruby.width(c.ruby_text) + pad - bw) if c.ruby else 0.0 for c, bw in zip(chunks, base)]
    n = len(chunks)

    def room(i: int, j: int) -> float:
        """How far chunk i's ruby may reach over its neighbour j (-1 / n: the line's edge)."""
        if not 0 <= j < n:
            return ruby_size
        if chunks[j].ruby:
            return 0.0
        other = j - 1 if j < i else j + 1  # the neighbour's other side
        shared = 0 <= other < n and need[other] > 0
        return min(ruby_size, base[j] / 2 if shared else base[j])

    out = []
    for i, bw in enumerate(base):
        if need[i] <= 0:
            out.append(bw)
            continue
        # the ruby is centred, so the overhang is symmetric: limited by the tighter side
        allowance = min(room(i, i - 1), room(i, i + 1))
        out.append(bw + max(0.0, need[i] - 2 * allowance))
    return out


def ruby_overhang(chunks: list[Chunk], widths: list[float], m_ruby) -> tuple[float, float]:
    """How far the readings reach past the line's left and right edge (unscaled px)."""
    if m_ruby is None or not chunks:
        return 0.0, 0.0
    total = sum(widths)
    left = right = 0.0
    x = 0.0
    for c, w in zip(chunks, widths):
        if c.ruby:
            half = m_ruby.width(c.ruby_text) / 2
            left = max(left, half - (x + w / 2))
            right = max(right, x + w / 2 + half - total)
        x += w
    return max(0.0, left), max(0.0, right)


def piece_segments(c: Chunk, cx: float, width: float, t0: int, m_main) -> list[tuple[int, int, float, float]]:
    """Where and when the lyric's sweep runs over a chunk: [(start, end) ms from t0, x from, x to].

    The lyric's \\kf fills each piece from its left to its right edge over the piece's time
    (\\k: at once when it starts) and jumps over the space between pieces; ``width`` is the
    drawn width of the chunk's text centred at ``cx``.  The same centisecond timing as
    _karaoke()."""
    widths = [m_main.width(p.text) for p in c.base]
    norm = width / (sum(widths) or 1.0)
    x, cursor = cx - width / 2, 0
    segs: list[tuple[int, int, float, float]] = []
    for p, pw in zip(c.base, widths):
        s = int(round((p.start - t0) / 10)) if p.start is not None else cursor
        e = int(round((p.end - t0) / 10)) if p.end is not None else s
        s = max(s, cursor)
        e = max(e, s)
        segs.append((s * 10, e * 10, x, x + pw * norm))
        x += pw * norm
        cursor = e
    return segs


def sweep_time(segs: list[tuple[int, int, float, float]], x: float, instant: bool) -> int:
    """When the sweep of piece_segments() reaches ``x`` (ms from t0): before the chunk its
    start, past it its end."""
    for s, e, xa, xb in segs:
        if x <= xa:
            return s
        if x < xb:
            return s if instant or xb <= xa else int(s + (e - s) * (x - xa) / (xb - xa))
    return segs[-1][1] if segs else 0


def sweep_clip(segs: list[tuple[int, int, float, float]], hi: float, instant: bool, H: int,
               inverse: bool = False) -> str:
    """An animated \\clip (``inverse``: \\iclip, the rest) whose right edge is the sweep over one chunk.

    Nothing is sung before the chunk's first piece starts; then the cut jumps to the lyric's
    left edge (so a reading reaching further left turns sung there at once), follows the
    lyric's sweep, and when the last piece is done jumps to ``hi`` (the right edge of what
    is drawn: a reading wider than its lyric turns sung completely).  Lyric and ruby of a
    chunk follow the same sweep over the lyric, so both are cut by one vertical line meanwhile."""
    name = "iclip" if inverse else "clip"

    def clip(x: float) -> str:
        return f"\\{name}(0,0,{int(round(x))},{H})"

    if not segs:
        return clip(hi)
    tags, cur = [clip(0)], None
    for i, (s, e, xa, xb) in enumerate(segs):
        if i == len(segs) - 1 and (instant or e <= s):
            xb = max(xb, hi)
        if cur is None or abs(xa - cur) > 0.5:  # on to the next piece
            tags.append(f"\\t({s},{s + 1},{clip(xa)})")
        if not instant and e > s:
            tags.append(f"\\t({s},{e},{clip(xb)})")
        else:
            tags.append(f"\\t({s},{s + 1},{clip(xb)})")
        cur = xb
    last_e = segs[-1][1]
    if hi > (cur or 0) + 0.5:
        tags.append(f"\\t({last_e},{last_e + 1},{clip(hi)})")
    return "".join(tags)


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
    """The frame the subtitles are made for: the background's, else the video's, else 1920×1080."""
    b = project.background
    if b is not None:
        from .background import frame_for

        return frame_for(b.width, b.height)
    v = project.video
    if v is not None and v.width and v.height:
        return int(v.width), int(v.height)
    return DEFAULT_SIZE


# Layers, bottom to top: effects drawn behind the text, translation glow, translation,
# text glow (unsung), text glow (sung), lyrics, ruby; then effects in front (7) and the title card (8, 9).
L_FX, L_TRANS_GLOW, L_TRANS, L_GLOW, L_GLOW_SUNG, L_MAIN, L_RUBY = 0, 1, 2, 3, 4, 5, 6


def build_ass(project: Project, result: AlignmentResult, style: Optional[KaraokeStyle] = None, *,
              time_offset_ms: float = 0.0, size: Optional[tuple[int, int]] = None) -> tuple[str, list[str]]:
    """Return (ASS text, warnings).  ``size``: the frame the subtitles are drawn on (PlayRes)."""
    from .effects import FX_STYLE, Syllable, ball_room, syllable_events
    from .info import info_events, info_style

    style = style or project.karaoke
    audio_offset_ms = time_offset_ms  # the title card keeps real time
    # show / highlight everything a little before it is sung (display only)
    time_offset_ms -= style.timing.advance_ms
    W, H = size or resolution(project)
    W, H = max(16, int(W)), max(16, int(H))
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
    trans_family = (usable(tr.font) or family) if tr.enabled else family
    # guards for styles built in code without validation: every size positive, room left between the margins
    main_size = max(1.0, txt.size * k)
    ruby_size = max(1.0, main_size * max(1, rb.size_pct) / 100)
    trans_size = max(1.0, main_size * max(1, tr.size_pct) / 100)
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
    extra = schedule(laid, style)
    if extra:
        warnings.append(f"{extra} 行与其他行同时演唱（对唱 / 和声），已临时显示在歌词区外多出的一行")

    has_ruby = rb.enabled and any(c.ruby for ll in laid for c in ll.chunks)
    per_line_trans = tr.enabled and tr.position == "line"
    ruby_h = (ruby_size + gap) if has_ruby else 0.0
    slot_h = main_size + ruby_h + ((trans_size + trans_gap) if per_line_trans else 0)
    n = max(1, lay.lines)
    # the bouncing ball hops above each line: rows keep room for it
    spacing = max(lay.line_spacing * k, ball_room(style, main_size, k))
    margin_v = min(lay.margin_v * k, H * 0.4)
    margin_h = min(lay.margin_h * k, W * 0.4)
    avail = W - 2 * margin_h
    block_h = n * slot_h + (n - 1) * spacing
    block_top = max(0.0, H - margin_v - block_h) if lay.position == "bottom" else margin_v
    top_row = min((ll.slot for ll in laid), default=0)

    fade_in, fade_out = max(0, tm.fade_in_ms), max(0, tm.fade_out_ms)
    g_alpha = f"&H{int(round(255 * (1 - glow.strength / 100))):02X}&"

    def glow_tags(color: str, width: float, font: str, size: float, bold: bool) -> str:
        return (f"\\fn{font}\\fs{size:.1f}\\b{1 if bold else 0}\\3c{bgr_tag(color)}\\3a{g_alpha}"
                f"\\bord{width:.1f}\\blur{glow.blur * k:.1f}\\shad0")

    events: list[str] = []
    first_shown: dict[str, float] = {}  # style -> first time an event of it shows
    last_shown: dict[str, float] = {}  # style -> last time an event of it is on screen
    boxes: list[Box] = []  # where the lyrics and translations are, and when (for the title card)

    def emit(layer: int, t_from: float, t_to: float, name: str, tags: str, body: str, fad: str) -> None:
        first_shown[name] = min(first_shown.get(name, float("inf")), t_from + time_offset_ms)
        last_shown[name] = max(last_shown.get(name, float("-inf")), t_to + time_offset_ms)
        events.append(f"Dialogue: {layer},{ass_time(t_from + time_offset_ms)},{ass_time(t_to + time_offset_ms)},"
                      f"{name},,0,0,0,,{{{tags}{fad}}}{body}")

    def emit_text(layer: int, t_from: float, t_to: float, name: str, pos: str, parts: list[Part], width: float,
                  with_glow: bool, font: str, size: float, fad: str) -> None:
        """A karaoke text event, with its glow layers when the glow is on."""
        if glow.enabled and with_glow:
            plain = escape_text("".join(p.text for p in parts))
            emit(L_GLOW, t_from, t_to, "KGlow", pos + glow_tags(glow.color_unsung, width, font, size, txt.bold), plain,
                 fad)
            # \ko: the border (the glow) appears as each syllable is sung
            emit(L_GLOW_SUNG, t_from, t_to, "KGlow", pos + glow_tags(glow.color_sung, width, font, size, txt.bold),
                 _karaoke(parts, int(t_from), "ko"), fad)
        emit(layer, t_from, t_to, name, pos, _karaoke(parts, int(t_from), tag), fad)

    ruby_unsung = (txt if rb.follow_colors else rb).color_unsung

    def emit_following(layer: int, name: str, t_from: float, t_to: float, pos: str, text: str, unsung: str,
                       font: str, size: float, glow_width: float, with_glow: bool, clip: str, iclip: str,
                       fad: str) -> None:
        """Text swept by a moving \\clip instead of \\kf: the text in the unsung colour cut at
        ``iclip`` (right of the sweep) and in the sung colour (the style's primary) cut at
        ``clip`` (left of it), so every pixel comes from one of the two, also while fading."""
        plain = escape_text(text)
        if glow.enabled and with_glow:
            emit(L_GLOW, t_from, t_to, "KGlow",
                 pos + glow_tags(glow.color_unsung, glow_width, font, size, txt.bold) + iclip, plain, fad)
            emit(L_GLOW_SUNG, t_from, t_to, "KGlow",
                 pos + glow_tags(glow.color_sung, glow_width, font, size, txt.bold) + clip, plain, fad)
        emit(layer, t_from, t_to, name, pos + f"\\1c{bgr_tag(unsung)}" + iclip, plain, fad)
        emit(layer, t_from, t_to, name, pos + clip, plain, fad)

    def emit_trans(t_from: float, t_to: float, pos: str, text: str, fad: str) -> None:
        if glow.enabled and tr.glow:
            emit(L_TRANS_GLOW, t_from, t_to, "KGlow",
                 pos + glow_tags(glow.color_unsung, glow.size * k * 0.7, trans_family, trans_size, tr.bold),
                 escape_text(text), fad)
        emit(L_TRANS, t_from, t_to, "KTrans", pos, escape_text(text), fad)

    tag = "kf" if tm.highlight == "sweep" else "k"
    instant = tm.highlight != "sweep"
    following = rb.sweep == "base" and has_ruby
    edge = (max(txt.outline, 0) + max(txt.shadow, 0) + (glow.size + glow.blur if glow.enabled else 0)) * k + 2
    fx_on = style.effects.kind != "none"
    syllables: list[Syllable] = []
    # widths first: how far a line sits in from its edge depends on the lines shown next to it
    geom = []
    for ll in laid:
        widths = chunk_widths(ll.chunks, m_main, m_ruby, ruby_size, rb.fit)
        line_w = sum(widths) or 1.0
        over_l, over_r = ruby_overhang(ll.chunks, widths, m_ruby)
        extent = line_w + over_l + over_r  # readings reaching past the line's ends count too
        scale = min(1.0, avail / extent) if lay.shrink_long_lines else 1.0
        if scale < 1.0:
            warnings.append(f"「{ll.line.text}」过长，已缩小到 {scale:.0%}")
        align = "center"
        if lay.arrangement == "alternate" and n > 1 and 0 <= ll.slot < n:
            align = "left" if ll.slot == 0 else ("right" if ll.slot == n - 1 else "center")
        geom.append((widths, line_w * scale, over_l * scale, over_r * scale, scale, align))
    insets = alternate_insets(laid, geom, lay.alternate_indent * k, avail)
    for ll, (widths, line_w, over_l, over_r, scale, align), inset in zip(laid, geom, insets):
        if align == "left":
            x0 = margin_h + inset + over_l
        elif align == "right":
            x0 = W - margin_h - inset - line_w - over_r
        else:  # centred, but its readings kept inside the margins
            lo, hi = margin_h + over_l, W - margin_h - over_r - line_w
            x0 = min(max((W - line_w) / 2, lo), hi) if lo <= hi else (lo + hi) / 2
        slot_top = block_top + ll.slot * (slot_h + spacing)
        main_y = slot_top + ruby_h + main_size
        # shrunk lines keep their bottom edge where it was
        ruby_y = main_y - main_size * scale - gap * scale
        line_top = (ruby_y - ruby_size * scale) if has_ruby else (main_y - main_size * scale)
        room_above = spacing if ll.slot > top_row else line_top  # free room above the line (the ball)
        fs = "" if scale >= 1.0 else f"\\fscx{scale * 100:.1f}\\fscy{scale * 100:.1f}"
        spans = visible_spans(ll, style)
        if len(spans) > 1:
            pause = (spans[1][0] - spans[0][1] + tm.hold_ms + tm.lead_in_ms) / 1000
            warnings.append(f"「{ll.line.text}」中间停顿约 {pause:.0f} 秒，停顿期间暂时隐藏该行")
        cxs, x = [], x0
        for w in widths:
            cxs.append(x + w * scale / 2)
            x += w * scale
        for t_from, t_to in spans:
            # an ASS event cannot start before 0:00: start it there and time the sweep from there,
            # or the fill would lag by what was cut off
            t_from = max(t_from, -time_offset_ms)
            if t_to <= t_from:
                continue
            fad = fade_tag(fade_in, fade_out, t_from, t_to, *_sung_within(ll, t_from, t_to))
            bottom = main_y + ((trans_gap + trans_size) if (ll.translation and per_line_trans) else 0)
            boxes.append((t_from + time_offset_ms, t_to + time_offset_ms, x0 - over_l, line_top,
                          x0 + line_w + over_r, bottom))
            for c, cx in zip(ll.chunks, cxs):
                main_pos = f"\\an2\\pos({cx:.1f},{main_y:.1f}){fs}"
                ruby_pos = f"\\an2\\pos({cx:.1f},{ruby_y:.1f}){fs}"
                bw = m_main.width(c.base_text) * scale
                rw = m_ruby.width(c.ruby_text) * scale if (c.ruby and m_ruby is not None) else 0.0
                segs = piece_segments(c, cx, bw, int(t_from), m_main) if (following or fx_on) else []
                if following:
                    # lyric and ruby cut by one computed line: libass places its own \\kf boundary by
                    # glyph ink, a few pixels away from any position computed outside it
                    # once sung, the edges beyond the last glyph's advance (outline, glow) turn too
                    hi = cx + bw / 2 + edge
                    emit_following(L_MAIN, "KMain", t_from, t_to, main_pos, c.base_text, txt.color_unsung,
                                   family, main_size, glow.size * k, True,
                                   sweep_clip(segs, hi, instant, H), sweep_clip(segs, hi, instant, H, True), fad)
                else:
                    emit_text(L_MAIN, t_from, t_to, "KMain", main_pos, c.base, glow.size * k, True, family,
                              main_size, fad)
                if c.ruby and following:
                    hi = max(cx + bw / 2, cx + rw / 2) + edge
                    emit_following(L_RUBY, "KRuby", t_from, t_to, ruby_pos, c.ruby_text, ruby_unsung, ruby_family,
                                   ruby_size, glow.size * k * 0.55, glow.ruby,
                                   sweep_clip(segs, hi, instant, H), sweep_clip(segs, hi, instant, H, True), fad)
                elif c.ruby:
                    emit_text(L_RUBY, t_from, t_to, "KRuby", ruby_pos, c.ruby, glow.size * k * 0.55, glow.ruby,
                              ruby_family, ruby_size, fad)
                if fx_on:
                    group = f"{ll.line.id}@{t_from}"
                    syllables += _syllables(c.base, cx, main_y, main_size, scale, m_main, family, False, t_from,
                                            t_to, group, line_top, room_above)
                    if c.ruby and style.effects.ruby and m_ruby is not None:
                        # with the ruby following the lyric, a reading syllable is sung when the sweep
                        # passes it, not at its own time
                        timing = (lambda xa, xb: (int(t_from) + sweep_time(segs, xa, instant),
                                                  int(t_from) + sweep_time(segs, xb, instant))) if following else None
                        syllables += _syllables(c.ruby, cx, ruby_y, ruby_size, scale, m_ruby, ruby_family, True,
                                                t_from, t_to, group, line_top, room_above, timing)
            if ll.translation and per_line_trans:
                ty = main_y + trans_gap + trans_size
                # a translation wider than the room between the margins is shrunk, and kept inside them
                tw = m_trans.width(ll.translation) or 1.0
                tfs = f"\\fscx{avail / tw * 100:.1f}\\fscy{avail / tw * 100:.1f}" if tw > avail else ""
                tw = min(tw, avail)
                an, tx = {"left": (1, max(margin_h, min(x0, W - margin_h - tw))),
                          "right": (3, min(W - margin_h, max(x0 + line_w, margin_h + tw))),
                          "center": (2, W / 2)}[align]
                emit_trans(t_from, t_to, f"\\an{an}\\pos({tx:.1f},{ty:.1f}){tfs}", ll.translation, fad)

    if tr.enabled and not per_line_trans:
        fad = f"\\fad({fade_in},{fade_out})" if (fade_in or fade_out) else ""
        for t0, t1, pos, text, (bx0, by0, bx1, by1) in translation_placements(
                laid, style, W, H, block_top, block_h, trans_size, m_trans, margin_h, margin_v):
            emit_trans(t0, t1, pos, text, fad)
            boxes.append((t0 + time_offset_ms, t1 + time_offset_ms, bx0, by0, bx1, by1))

    if fx_on:
        for layer, t0, t1, tags, body in syllable_events(style, syllables, k):
            events.append(f"Dialogue: {layer},{ass_time(t0 + time_offset_ms)},{ass_time(t1 + time_offset_ms)},"
                          f"KFx,,0,0,0,,{{{tags}}}{body}")

    # the title card leaves before anything else appears along the top edge (after 2 s at least), and
    # before anything that would be drawn where it is
    top_busy = [first_shown.get("KMain")] if lay.position == "top" else []
    if tr.enabled and tr.position == "opposite":
        top_busy.append(first_shown.get("KTrans"))
    busy = [t for t in top_busy if t is not None]
    # the ending card: until the song ends, after the last lyric / translation along the top edge
    top_last = [last_shown.get("KMain")] if lay.position == "top" else []
    if tr.enabled and tr.position == "opposite":
        top_last.append(last_shown.get("KTrans"))
    busy_end = [t for t in top_last if t is not None]
    orig = project.asset("original")
    song_end = (orig.duration_ms + audio_offset_ms) if orig is not None and orig.duration_ms else None
    events += info_events(project, style, W, H, k, family, audio_offset_ms, min(busy) if busy else None,
                          boxes=boxes, warnings=warnings, end_ms=song_end,
                          busy_until_ms=max(busy_end) if busy_end else None)

    shadow_back = ass_color(txt.shadow_color, 100 - txt.shadow_opacity)

    def style_line(name: str, font: str, size: float, sung: str, unsung: str, outline_c: str, outline: float,
                   shadow: float, bold: bool, fill_alpha: int = 0) -> str:
        return (f"Style: {name},{font},{size:.1f},{ass_color(sung, fill_alpha)},{ass_color(unsung, fill_alpha)},"
                f"{ass_color(outline_c)},{shadow_back},{-1 if bold else 0},0,0,0,100,100,0,0,1,"
                f"{max(0.0, outline) * k:.2f},{max(0.0, shadow) * k:.2f},2,0,0,0,1")

    rc = txt if rb.follow_colors else rb
    header = [
        "[Script Info]",
        "; generated by KiraKara",
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
               ruby: bool, t_from: float, t_to: float, group: str = "", top: float = 0.0, room: float = 1e9,
               timing=None) -> list:
    """Where each sung piece of a chunk sits on screen (the chunk text is centred at ``cx``, its
    bottom at ``bottom_y`` as drawn with \\an2); ``timing(x0, x1)`` gives a piece's (start, end)
    when it is not its own (a reading that follows the lyric's sweep)."""
    from .effects import Syllable

    widths = [measurer.width(p.text) * scale for p in parts]
    left = cx - sum(widths) / 2
    out = []
    for p, w in zip(parts, widths):
        start, end = (p.start, p.end) if timing is None else timing(left, left + w)
        if start is not None and end is not None and p.text.strip() and t_from <= start < t_to:
            # \an5 at the middle of the line box (its height is the font size) sits exactly on the
            # glyphs drawn with \an2 at the bottom, and scales around their centre
            out.append(Syllable(text=p.text, start=int(start), end=int(max(end, start)), x=left + w / 2,
                                y=bottom_y - size * scale * 0.5, w=max(w, size * scale * 0.4), h=size * scale,
                                font=font, size=size * scale, ruby=ruby, visible_until=int(t_to), group=group,
                                top=top, room=room))
        left += w
    return out
