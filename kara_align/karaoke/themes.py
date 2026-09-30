"""Colour templates: pick a template and one theme colour, get a readable style.

Two templates (配色模版):

* ``plain`` 朴素 — coloured sweep over near-white text, dark outline, nothing else;
* ``glow``  荧光 — the same, plus a glow edge that changes colour as it is sung, glowing
  translations and soft sparkles behind the text (the look of the built-in 暖阳).

The template only decides colours and those effects; layout, timing and ruby
come from the style it is applied to (the simple mode's default style).

Palette algorithm
-----------------
Everything is computed in OKLCH (perceptual lightness L, chroma C, hue h), so
"same lightness" really looks equally light across hues:

1. **Sung** (the sweep colour) keeps the theme colour's hue and chroma, with its
   lightness pulled into 0.60–0.86: dark picks (navy, maroon) are lifted so the
   fill stays readable on the dark outline, too-light picks (pastels) are
   lowered so they still differ from the unsung text.
2. **Unsung** is near-white, faintly tinted with the hue (L 0.975).  If sung and
   unsung are closer than ΔE 0.18 (OKLab distance; yellows, which are light and
   saturated), the sung colour is darkened step by step until they differ.
3. **Outline** is a very dark shade of the hue (L ≤ 0.26, low chroma), darkened
   until both fills reach a WCAG contrast ratio of at least 4.5 against it; the
   **shadow** is darker still.
4. **Translation** is a pale tint of the hue (L 0.93), secondary to the lyrics.
5. **Glow** (glow template): the sung glow is the vivid sung colour; the unsung
   glow is a lighter analogous colour (hue shifted 25° toward yellow for warm
   hues, toward cyan for cool ones: orange → gold, blue → sky), and the sparkles
   a pale tint of it.
6. **Two colours** (主色 + 辅色, e.g. orange + yellow): the main colour does steps
   1–3 and the sung glow; the second colour replaces the analogous colour — the
   unsung glow (lightness kept within 0.65–0.9), the sparkles and translation
   (pale tints of it) and the title card's accent bar.
7. **Singers** (多人演唱): each singer's colours come from the same steps with the
   singer's colour, except that the unsung text is a clearer tint of the hue
   (L 0.94) so it shows who sings a line before it is sung.

Neutral picks (grey / black / white) give neutral palettes.  Every colour is
brought into the sRGB gamut by reducing chroma, never by clipping channels.
"""

from __future__ import annotations

import math

from ..models import KaraokeSinger, KaraokeStyle, KaraokeTheme

TEMPLATES = {"plain": "朴素", "glow": "荧光"}
DEFAULT_COLOR = "#ED35B3"

# theme colours offered as one-click swatches
SWATCHES = ["#ED35B3", "#FF4D6D", "#FF8A1E", "#F5C400", "#3CC46A", "#1FB5C9", "#2F80ED", "#8B5CF6"]
# a new singer's colour: the first of these no other singer has (neighbours far apart in hue)
SINGER_SWATCHES = ["#ED35B3", "#2F80ED", "#F5C400", "#3CC46A", "#FF8A1E", "#8B5CF6", "#1FB5C9", "#FF4D6D", "#8A8A8A"]


def new_singer_color(used: set[str], index: int) -> str:
    """The first swatch no singer has; after them, hues a golden angle apart (as the 演唱者 page's
    lib/singers.ts newSinger does)."""
    import colorsys

    free = next((c for c in SINGER_SWATCHES if c not in {u.upper() for u in used}), None)
    if free:
        return free
    r, g, b = colorsys.hls_to_rgb(((index * 137.508) % 360) / 360, 0.55, 0.70)
    return "#" + "".join(f"{round(v * 255):02X}" for v in (r, g, b))


# ------------------------------------------------------------------ colour space


def _to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _to_srgb(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def hex_to_rgb(h: str) -> tuple[float, float, float]:
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    if len(h) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in h):
        raise ValueError(f"不是有效的颜色：#{h}")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(max(0.0, min(1.0, c)) * 255):02X}" for c in rgb)


def _rgb_to_oklab(rgb):
    r, g, b = (_to_linear(c) for c in rgb)
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)


def _oklab_to_linear(lab):
    L, a, b = lab
    l_ = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
            -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
            -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_)


def oklch(hex_color: str) -> tuple[float, float, float]:
    L, a, b = _rgb_to_oklab(hex_to_rgb(hex_color))
    return L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360


def from_oklch(L: float, C: float, h: float) -> str:
    """OKLCH → hex, reducing chroma until the colour fits in sRGB."""
    L = max(0.0, min(1.0, L))
    lo, hi = 0.0, max(0.0, C)
    rad = math.radians(h)

    def fits(c: float):
        lin = _oklab_to_linear((L, c * math.cos(rad), c * math.sin(rad)))
        return all(-1e-6 <= x <= 1 + 1e-6 for x in lin), lin

    ok, lin = fits(hi)
    if not ok:
        for _ in range(30):
            mid = (lo + hi) / 2
            if fits(mid)[0]:
                lo = mid
            else:
                hi = mid
        lin = fits(lo)[1]
    return rgb_to_hex(tuple(_to_srgb(max(0.0, min(1.0, x))) for x in lin))  # type: ignore[arg-type]


def relative_luminance(hex_color: str) -> float:
    r, g, b = (_to_linear(c) for c in hex_to_rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio (1–21)."""
    la, lb = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def delta_e(a: str, b: str) -> float:
    """OKLab distance (≈ 0.02 is a just-noticeable difference)."""
    x, y = _rgb_to_oklab(hex_to_rgb(a)), _rgb_to_oklab(hex_to_rgb(b))
    return math.dist(x, y)


# ------------------------------------------------------------------ palette


def _toward_light_hue(h: float, by: float) -> float:
    """Shift a hue by up to ``by`` degrees toward the light, saturated hues: warm → yellow (110°),
    cool → cyan (195°)."""
    target = 195.0 if 110 <= h < 290 else 110.0
    d = (target - h + 540) % 360 - 180  # signed shortest way round
    return (h + max(-by, min(by, d))) % 360


def palette(color: str, secondary: str | None = None, *, tinted: bool = False) -> dict[str, str]:
    """The colours of every role, derived from one theme colour (and an optional second one).
    ``tinted``: the unsung text a clearer tint of the hue (a singer's colours)."""
    L0, C0, h = oklch(color)
    neutral = C0 < 0.03
    C = 0.0 if neutral else C0
    if tinted:
        unsung = from_oklch(0.94, 0.0 if neutral else min(0.06, C * 0.4), h)
    else:
        unsung = from_oklch(0.975, 0.0 if neutral else min(0.025, C * 0.15), h)

    Ls = min(0.86, max(0.60, L0))
    sung = from_oklch(Ls, C, h)
    while delta_e(sung, unsung) < 0.18 and Ls > 0.5:  # light, saturated picks (yellow): darken
        Ls -= 0.02
        sung = from_oklch(Ls, C, h)

    Lo = 0.26
    outline = from_oklch(Lo, 0.0 if neutral else min(0.06, C * 0.4), h)
    while min(contrast(sung, outline), contrast(unsung, outline)) < 4.5 and Lo > 0.08:
        Lo -= 0.02
        outline = from_oklch(Lo, 0.0 if neutral else min(0.06, C * 0.4), h)
    shadow = from_oklch(max(0.05, Lo - 0.12), 0.0 if neutral else min(0.03, C * 0.2), h)

    glow_sung = from_oklch(max(0.55, Ls - 0.03), C * 1.1, h)
    if secondary:
        # the second colour takes the supporting roles: unsung glow, sparkles, translation, accent bar
        L2, C2, h2 = oklch(secondary)
        C2 = 0.0 if C2 < 0.03 else C2
        glow_unsung = from_oklch(min(0.9, max(0.65, L2)), C2, h2)
        accent = from_oklch(min(0.86, max(0.60, L2)), C2, h2)
        hg, Cg = h2, C2
    else:
        hg = h if neutral else _toward_light_hue(h, 25)
        glow_unsung = from_oklch(min(0.9, Ls + 0.1), C * 0.9, hg)
        accent, Cg = "", C
    return {
        "sung": sung,
        "unsung": unsung,
        "outline": outline,
        "shadow": shadow,
        "translation": from_oklch(0.93, min(0.07, Cg * 0.5), hg),
        "glow_unsung": glow_unsung,
        "glow_sung": glow_sung,
        "sparkle": from_oklch(0.93, min(0.12, Cg * 0.7), hg),
        "accent": accent,  # "" = the sung colour
    }


def theme_style(template: str, color: str, base: KaraokeStyle, secondary: str | None = None) -> KaraokeStyle:
    """``base`` (layout, timing, ruby …) with the template's colours and effects."""
    if template not in TEMPLATES:
        raise ValueError(f"没有这个模版：{template}")
    # as #RRGGBB (a style's colours are validated as such; "#f80" is accepted here)
    color = rgb_to_hex(hex_to_rgb(color))
    secondary = rgb_to_hex(hex_to_rgb(secondary)) if secondary else None
    p = palette(color, secondary)
    st = base.model_copy(deep=True)
    st.theme = KaraokeTheme(template=template, color=color, secondary=secondary or "")
    t = st.text
    t.color_unsung, t.color_sung, t.outline_color, t.shadow_color = p["unsung"], p["sung"], p["outline"], p["shadow"]
    st.ruby.follow_colors = True
    tr = st.translation
    tr.color, tr.outline_color = p["translation"], p["outline"]
    st.info.color, st.info.accent = "", p["accent"]  # text follows the lyrics; the bar the second colour
    if template == "plain":
        st.glow.enabled = False
        st.effects.kind = "none"
    else:
        g = st.glow
        g.enabled, g.color_unsung, g.color_sung = True, p["glow_unsung"], p["glow_sung"]
        g.size, g.blur, g.strength, g.ruby = 10.0, 9.0, 75, True
        tr.glow = True
        fx = st.effects
        fx.kind, fx.amount, fx.size, fx.color, fx.behind = "sparkle", 60, 90, p["sparkle"], True
    return st


# ------------------------------------------------------------------ singers


def singer_colors(member: KaraokeSinger) -> dict[str, str]:
    """A singer's colours: its own where set, the rest derived from its colour (palette step 7)."""
    p = palette(rgb_to_hex(hex_to_rgb(member.color)), tinted=True)
    return {
        "sung": member.color_sung or p["sung"],
        "unsung": member.color_unsung or p["unsung"],
        "outline": member.outline_color or p["outline"],
        "glow_sung": member.glow_sung or p["glow_sung"],
        "glow_unsung": member.glow_unsung or p["glow_unsung"],
        "translation": p["translation"],
        "sparkle": p["sparkle"],
    }


def mix(a: str, b: str, t: float) -> str:
    """``a`` blended toward ``b`` by ``t`` (0–1), in OKLab (even steps look even)."""
    x, y = _rgb_to_oklab(hex_to_rgb(a)), _rgb_to_oklab(hex_to_rgb(b))
    lab = tuple(p + (q - p) * t for p, q in zip(x, y))
    return rgb_to_hex(tuple(_to_srgb(max(0.0, min(1.0, c))) for c in _oklab_to_linear(lab)))  # type: ignore[arg-type]


def blend(colors: list[str], t: float) -> str:
    """The colour at ``t`` (0–1) along a gradient through ``colors`` at even stops."""
    if len(colors) == 1:
        return colors[0]
    t = max(0.0, min(1.0, t)) * (len(colors) - 1)
    i = min(int(t), len(colors) - 2)
    return mix(colors[i], colors[i + 1], t - i)
