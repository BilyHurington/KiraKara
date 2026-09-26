"""Colour templates: one or two theme colours → a readable palette and style."""

import pytest

from kara_align.karaoke.styles import default_style
from kara_align.karaoke.themes import (
    SWATCHES, contrast, delta_e, from_oklch, hex_to_rgb, oklch, palette, theme_style,
)

PICKS = SWATCHES + ["#FFFF00", "#FFF4B0", "#0A1F5C", "#000000", "#FFFFFF", "#808080", "#7F1D1D", "#00FF00", "#C0FFEE"]


def test_oklch_round_trip_and_gamut():
    for c in PICKS:
        assert from_oklch(*oklch(c)).upper() == c.upper() or delta_e(from_oklch(*oklch(c)), c) < 0.005
    assert all(len(from_oklch(0.7, 0.5, h)) == 7 for h in range(0, 360, 15))  # out of gamut: chroma reduced
    with pytest.raises(ValueError):
        hex_to_rgb("#12")


@pytest.mark.parametrize("color", PICKS)
def test_palette_is_readable_for_any_pick(color):
    p = palette(color)
    # both fills stand out from the outline, and sung is clearly different from unsung
    assert contrast(p["sung"], p["outline"]) >= 4.5 and contrast(p["unsung"], p["outline"]) >= 4.5
    assert delta_e(p["sung"], p["unsung"]) >= 0.18
    assert oklch(p["unsung"])[0] > 0.95 and oklch(p["outline"])[0] <= 0.27
    assert 0.5 <= oklch(p["sung"])[0] <= 0.87
    L, C, h = oklch(color)
    if C > 0.05:  # the sweep keeps the pick's hue
        assert abs((oklch(p["sung"])[2] - h + 180) % 360 - 180) < 6


def test_mid_tone_picks_are_kept_and_extremes_adjusted():
    assert palette("#FF8A1E")["sung"] == "#FF8A1E" and palette("#ED35B3")["sung"] == "#ED35B3"
    assert oklch(palette("#0A1F5C")["sung"])[0] > 0.59  # navy lifted
    assert palette("#FFFF00")["sung"] != "#FFFF00"  # pure yellow darkened away from the white text
    # glow: a lighter analogous colour — orange → gold, blue → toward cyan
    assert 70 < oklch(palette("#FF8A1E")["glow_unsung"])[2] < 95
    assert oklch(palette("#2F80ED")["glow_unsung"])[2] < oklch("#2F80ED")[2]


def test_two_colours_take_the_supporting_roles():
    one, two = palette("#FF8A1E"), palette("#FF8A1E", "#FFC53D")
    assert (two["sung"], two["outline"], two["glow_sung"]) == (one["sung"], one["outline"], one["glow_sung"])
    assert two["glow_unsung"] == "#FFC53D" and two["accent"] == "#FFC53D" and one["accent"] == ""
    assert abs(oklch(two["translation"])[2] - oklch("#FFC53D")[2]) < 8


def test_templates():
    base = default_style()
    plain = theme_style("plain", "#2F80ED", base)
    glow = theme_style("glow", "#2F80ED", base, "#ED35B3")
    p = palette("#2F80ED", "#ED35B3")
    assert plain.text.color_sung == glow.text.color_sung == p["sung"] and plain.ruby.follow_colors
    assert not plain.glow.enabled and plain.effects.kind == "none"
    assert glow.glow.enabled and glow.glow.color_unsung == "#ED35B3" and glow.effects.kind == "sparkle"
    assert glow.info.accent == "#ED35B3" and glow.translation.glow
    # layout, timing and ruby come from the base
    assert (glow.layout, glow.timing, glow.ruby.script) == (base.layout, base.timing, base.ruby.script)
    with pytest.raises(ValueError):
        theme_style("neon", "#FFFFFF", base)
