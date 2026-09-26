"""Karaoke effects around the lyrics, fired by each syllable as it is sung.

The lyric line itself stays one \\kf karaoke event per chunk; every effect is a
set of extra, short events placed at the sung syllable (its centre and width
come from the same measurement the layout uses).  This is how karaoke
templates (Aegisub's templater, PyonFX) build their effects: \\k tags cannot
scale or move a single syllable, so each syllable gets its own positioned
events.  Everything is plain ASS (\\move, \\t, \\fad, \\clip, drawings), so libass
renders the preview and the burned video identically, and the same style
always gives the same result (random choices are seeded per syllable).

Effects (``KaraokeEffects.kind``):

* ``pulse``   – 光晕扩散: a copy of the syllable swells and fades behind it;
* ``ring``    – 光环爆开: the syllable's outline bursts outward as a soft ring;
* ``shine``   – 闪光扫过: a bright band sweeps across the syllable's glyphs;
* ``sparkle`` – 星光迸发: small four-point stars burst out and twinkle;
* ``petals``  – 花瓣飘落: a sakura petal now and then drifts down, tumbling;
* ``hearts``  – 爱心飘升: small hearts pop up and float away;
* ``ball``    – 跳跃小球: a ball hops from syllable to syllable (bouncing-ball karaoke).

Particle bursts are rate-limited (a burst at most every ~200 ms) so fast
passages don't turn into noise.  Particles are drawn behind the lyrics by default
(``behind``) so they never cover a glyph.  Drawings are centred on (0, 0) and always get
``\\bord0\\shad0`` (the style's outline would apply to them too).  Copies of a syllable's
text are drawn ``\\an5`` at the middle of its line box, which puts them exactly on the
lyric's glyphs (drawn ``\\an2`` at the box's bottom) and scales them around their centre
(libass scales around the alignment point; ``\\org`` only moves the rotation centre).  The
bouncing ball never hops higher than the room above its line (rows are kept apart for it,
ball_room()).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from ..models import KaraokeStyle

LAYER_BACK = 3  # with the glow, under the lyric text
LAYER_UNDER_TEXT = 0  # under every subtitle: lyrics, ruby, their glow and the translations
LAYER_FRONT = 7  # above lyrics and ruby

LABELS = {"none": "无", "pulse": "光晕扩散", "ring": "光环爆开", "shine": "闪光扫过", "sparkle": "星光迸发",
          "petals": "花瓣飘落", "hearts": "爱心飘升", "ball": "跳跃小球"}

# shapes drawn around (0, 0), about 20 px across at 100 %
_STAR = "m 0 -10 b 1 -2 2 -1 10 0 b 2 1 1 2 0 10 b -1 2 -2 1 -10 0 b -2 -1 -1 -2 0 -10"
_PETAL = "m 0 -10 b 5 -12 9 -6 8 0 b 7 6 3 11 0 12 b -3 11 -7 6 -8 0 b -9 -6 -5 -12 0 -10"
_HEART = "m 0 -3 b -2 -9 -10 -8 -10 -2 b -10 3 -4 7 0 10 b 4 7 10 3 10 -2 b 10 -8 2 -9 0 -3"
_BALL = "m 0 -8 b 4.4 -8 8 -4.4 8 0 b 8 4.4 4.4 8 0 8 b -4.4 8 -8 4.4 -8 0 b -8 -4.4 -4.4 -8 0 -8"

FX_STYLE = "Style: KFx,Arial,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1"

_MIN_GAP = {"sparkle": 180, "petals": 320, "hearts": 260}  # ms between two bursts


@dataclass
class Syllable:
    text: str
    start: int  # ms, sung from
    end: int
    x: float  # centre on screen
    y: float  # middle of the line box: text drawn \an5 here sits exactly on the lyric's glyphs
    w: float  # width of the syllable
    h: float  # font size (px)
    font: str
    size: float
    ruby: bool
    visible_until: int  # the line disappears here: effects never outlive it
    group: str = ""  # syllables shown together (one line while it is on screen)
    top: float = 0.0  # top edge of the whole line, ruby included
    room: float = 1e9  # free space above ``top`` (up to the line above or the frame's edge)


def _bgr(hex_rgb: str) -> str:
    from .ass import bgr_tag

    return bgr_tag(hex_rgb)


def _ball_geometry(fx_size: int, h: float, k: float) -> tuple[float, float, float]:
    """(radius, resting lift of its centre above the line, highest hop) of the bouncing ball."""
    r = h * 0.1 * fx_size / 100
    return r, 2 * r + 4 * k, h * 0.25


def ball_room(style: KaraokeStyle, h: float, k: float) -> float:
    """Room the bouncing ball needs above a line (0 for the other effects): rows are kept at
    least this far apart so it never hops into the line above."""
    if style.effects.kind != "ball":
        return 0.0
    r, lift, hop = _ball_geometry(style.effects.size, h, k)
    return lift + r + hop


def _alpha(visible: float) -> str:
    return f"&H{int(round(255 * (1 - max(0.0, min(1.0, visible))))):02X}&"


def effect_color(style: KaraokeStyle) -> str:
    """The effect colour: its own, or the sung glow, or the sung lyric colour."""
    fx = style.effects
    if fx.color:
        return fx.color
    return style.glow.color_sung if style.glow.enabled else style.text.color_sung


def syllable_events(style: KaraokeStyle, syllables: list[Syllable], k: float) -> list[tuple[int, int, int, str, str]]:
    """(layer, start ms, end ms, override tags, body) for every effect event."""
    fx = style.effects
    if fx.kind == "none" or not syllables:
        return []
    # particles: behind the text (readable) or in front of it; pulse / ring always sit under the
    # text, the shine always runs over the glyphs it lights up
    particle_layer = LAYER_UNDER_TEXT if fx.behind else LAYER_FRONT
    if fx.kind == "ball":
        return _ball(style, syllables, k, particle_layer)
    color = _bgr(effect_color(style))
    size = fx.size / 100
    amount = fx.amount / 100
    out: list[tuple[int, int, int, str, str]] = []
    last_burst: dict[bool, float] = {}
    for i, s in enumerate(sorted(syllables, key=lambda s: (s.ruby, s.start))):
        rnd = random.Random(f"{fx.kind}-{i}-{s.start}-{s.text}")
        t0 = s.start
        small = 0.6 if s.ruby else 1.0
        # shapes are sized relative to the lyric: \fscx/\fscy % that draws a shape `across` px wide
        # at `rel` × the font size
        def scale(rel: float, across: float) -> float:
            return 100 * rel * s.h * size * small / across

        def add(layer: int, dur: float, tags: str, body: str, delay: float = 0.0) -> None:
            a = int(t0 + delay)
            b = int(min(a + dur, s.visible_until))  # gone with the line (the fade is inside)
            if b - a >= 60:
                out.append((layer, a, b, tags, body))

        gap = _MIN_GAP.get(fx.kind)
        if gap is not None:
            if t0 - last_burst.get(s.ruby, -1e9) < gap / max(0.5, amount):
                continue
            last_burst[s.ruby] = t0

        text_at = f"\\an5\\pos({s.x:.1f},{s.y:.1f})\\fn{s.font}\\fs{s.size:.1f}\\b{int(style.text.bold)}"
        if fx.kind == "pulse":
            dur = 520
            grow = 100 + 70 * size
            add(LAYER_BACK, dur,
                f"{text_at}\\1c{color}\\3c{color}\\bord{3 * k:.1f}\\shad0\\blur{2 * k:.1f}"
                f"\\1a{_alpha(0.9)}\\3a{_alpha(0.9)}"
                f"\\t(0,{dur},0.6,\\fscx{grow:.0f}\\fscy{grow:.0f}\\blur{6 * k:.1f}\\1a&HFF&\\3a&HFF&)",
                _escape(s.text))
        elif fx.kind == "ring":
            dur = 380
            add(LAYER_BACK, dur,
                f"{text_at}\\1a&HFF&\\3c{color}\\3a{_alpha(0.9)}\\bord{1 * k:.1f}\\shad0\\blur{1 * k:.1f}"
                f"\\t(0,{dur},0.7,\\bord{s.h * 0.16 * size:.1f}\\blur{4 * k:.1f}\\3a&HFF&)",
                _escape(s.text))
        elif fx.kind == "shine":
            # runs once the syllable is filled, so the band shows on the sung colour
            dur = max(300, min(450, s.w * 2.2))
            half = s.h * 0.75
            band = max(8 * k, s.w * 0.22) * size
            left, right = s.x - s.w / 2, s.x + s.w / 2
            top, bottom = s.y - half, s.y + half
            add(LAYER_FRONT, dur,
                f"{text_at}\\1c&HFFFFFF&\\bord0\\shad0\\blur{1 * k:.1f}\\1a{_alpha(0.9)}"
                f"\\clip({left - band:.0f},{top:.0f},{left:.0f},{bottom:.0f})"
                f"\\t(0,{dur:.0f},\\clip({right:.0f},{top:.0f},{right + band:.0f},{bottom:.0f}))",
                _escape(s.text), delay=max(0, s.end - s.start - 60))
        elif fx.kind == "sparkle":
            n = max(2, round((2 + 2 * rnd.random()) * min(1.5, amount) * small))
            for j in range(n):
                x0 = s.x + rnd.uniform(-0.5, 0.5) * s.w
                y0 = s.y - s.h * rnd.uniform(0.25, 0.5)
                ang = rnd.uniform(-math.pi * 0.95, -math.pi * 0.05)  # upward half
                r = s.h * rnd.uniform(0.35, 0.7) * small
                x1, y1 = x0 + math.cos(ang) * r, y0 + math.sin(ang) * r
                dur = rnd.uniform(500, 700)
                sc = scale(0.5, 20) * rnd.uniform(0.7, 1.2)
                spin = rnd.choice((-1, 1)) * 90
                add(particle_layer, dur,
                    f"\\an5\\move({x0:.1f},{y0:.1f},{x1:.1f},{y1:.1f})\\bord0\\shad0\\blur{0.8 * k:.1f}\\1c{color}"
                    f"\\fscx{sc * 0.4:.0f}\\fscy{sc * 0.4:.0f}"
                    f"\\t(0,{dur * 0.3:.0f},\\fscx{sc:.0f}\\fscy{sc:.0f})"
                    f"\\t({dur * 0.3:.0f},{dur:.0f},\\fscx{sc * 0.25:.0f}\\fscy{sc * 0.25:.0f}\\frz{spin})"
                    f"\\fad(0,250)",
                    f"{{\\p1}}{_STAR}{{\\p0}}", delay=j * rnd.uniform(30, 60))
        elif fx.kind == "petals":
            x0 = s.x + rnd.uniform(-0.4, 0.4) * s.w
            y0 = s.y - s.h * 0.5
            x1 = x0 + rnd.choice((-1, 1)) * rnd.uniform(0.3, 0.7) * s.h
            y1 = y0 + s.h * rnd.uniform(0.9, 1.3)
            dur = rnd.uniform(1300, 1800)
            sc = scale(0.3, 17) * rnd.uniform(0.8, 1.1)
            add(particle_layer, dur,
                f"\\an5\\move({x0:.1f},{y0:.1f},{x1:.1f},{y1:.1f})\\bord0\\shad0\\blur{0.6 * k:.1f}"
                f"\\1c{color}\\fscx{sc:.0f}\\fscy{sc:.0f}\\frz{rnd.uniform(0, 360):.0f}\\fad(150,500)"
                f"\\t(\\frz{rnd.choice((-1, 1)) * rnd.uniform(120, 220):.0f}\\fry{rnd.choice((-1, 1)) * 360})",
                f"{{\\p1}}{_PETAL}{{\\p0}}")
        elif fx.kind == "hearts":
            x0 = s.x + rnd.uniform(-0.25, 0.25) * s.w
            y0 = min(s.y - s.h * 0.55, s.top + s.h * 0.1)
            y1 = y0 - s.h * rnd.uniform(0.35, 0.55)
            dur = rnd.uniform(650, 850)
            sc = scale(0.34, 20) * rnd.uniform(0.85, 1.1)
            add(particle_layer, dur,
                f"\\an5\\move({x0:.1f},{y0:.1f},{x0 + rnd.uniform(-0.15, 0.15) * s.h:.1f},{y1:.1f})"
                f"\\bord0\\shad0\\blur{0.6 * k:.1f}\\1c{color}\\fscx0\\fscy0"
                f"\\t(0,150,\\fscx{sc * 1.15:.0f}\\fscy{sc * 1.15:.0f})\\t(150,260,\\fscx{sc:.0f}\\fscy{sc:.0f})"
                f"\\fad(0,300)",
                f"{{\\p1}}{_HEART}{{\\p0}}")
    return out


def _ball(style: KaraokeStyle, syllables: list[Syllable], k: float,
          layer: int = LAYER_FRONT) -> list[tuple[int, int, int, str, str]]:
    """A ball that lands on each syllable as it starts and hops on to the next one.

    Each hop is a few straight \\move segments along a parabola (\\move is linear
    and one per event), so the arc looks round without one event per frame.
    """
    fx = style.effects
    color = _bgr(effect_color(style))
    out: list[tuple[int, int, int, str, str]] = []
    groups: dict[str, list[Syllable]] = {}
    for s in syllables:
        if not s.ruby:
            groups.setdefault(s.group, []).append(s)
    for syl in groups.values():
        syl.sort(key=lambda s: s.start)
        h = syl[0].h
        sc = 100 * 0.2 * h * fx.size / 100 / 16  # 20 % of the font size across
        # the ball rests this far above the line (its ruby included) and hops no higher than the room
        # above it (the rows are kept apart for it, ball_room(); the top row: up to the frame's edge)
        r, lift, _ = _ball_geometry(fx.size, h, k)
        room = min(s.room for s in syl)
        lift = max(r, min(lift, room - r))
        headroom = max(0.0, room - lift - r)
        tags = f"\\an5\\bord0\\shad0\\blur{0.8 * k:.1f}\\1c{color}\\fscx{sc:.0f}\\fscy{sc:.0f}"
        body = f"{{\\p1}}{_BALL}{{\\p0}}"
        until = syl[-1].visible_until

        def seg(t0: float, t1: float, a: tuple[float, float], b: tuple[float, float], extra: str = "") -> None:
            t0, t1 = int(t0), int(min(t1, until))
            if t1 - t0 >= 40:
                out.append((layer, t0, t1,
                            f"{tags}\\move({a[0]:.1f},{a[1]:.1f},{b[0]:.1f},{b[1]:.1f}){extra}", body))

        # drops in onto the first syllable
        first = syl[0]
        rest = (first.x, first.top - lift)
        seg(first.start - 250, first.start, (first.x, rest[1] - min(h * 0.5, headroom)), rest, "\\fad(150,0)")
        for a, b in zip(syl, syl[1:]):
            here, there = (a.x, a.top - lift), (b.x, b.top - lift)
            hop = min(420.0, float(b.start - a.start))
            wait_until = b.start - hop
            if wait_until > a.start:
                seg(a.start, wait_until, here, here)
            height = min(h * 0.55, 12 * k + abs(b.x - a.x) * 0.25, headroom)
            steps = 4 if hop >= 200 else 1  # very fast syllables: a straight slide
            pts = [(here[0] + (there[0] - here[0]) * i / steps,
                    here[1] + (there[1] - here[1]) * i / steps - height * 4 * (i / steps) * (1 - i / steps))
                   for i in range(steps + 1)]
            for i in range(steps):
                seg(wait_until + hop * i / steps, wait_until + hop * (i + 1) / steps, pts[i], pts[i + 1])
        last = syl[-1]
        end = (last.x, last.top - lift)
        seg(last.start, max(last.end, last.start + 200) + 250, end, end, "\\fad(0,250)")
    return out


def _escape(text: str) -> str:
    from .ass import escape_text

    return escape_text(text)
