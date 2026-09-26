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


def test_style_remembers_its_template_and_the_editor_can_apply_one(tmp_path):
    from fastapi.testclient import TestClient

    from kara_align.models import KaraokeStyle
    from kara_align.web.server import create_app

    st = theme_style("glow", "#ff8a1e", default_style(), "#ffc53d")
    assert st.theme.model_dump() == {"template": "glow", "color": "#FF8A1E", "secondary": "#FFC53D"}
    assert st.preset == default_style().preset  # the preset bar shows "默认 · 已修改"
    assert KaraokeStyle().theme is None and KaraokeStyle.model_validate(st.model_dump(mode="json")).theme == st.theme
    # the detailed editor applies a template on top of its current style
    base = default_style()
    base.text.size, base.layout.lines = 70, 1
    client = TestClient(create_app(tmp_path / "projects"))
    r = client.post("/api/karaoke/theme", json={"template": "plain", "color": "#2F80ED",
                                                 "base": base.model_dump(mode="json")}).json()["style"]
    assert (r["text"]["size"], r["layout"]["lines"], r["theme"]["template"]) == (70, 1, "plain")
    assert r["text"]["color_sung"] == palette("#2F80ED")["sung"]
    # a base style with a value that does not fit is read like a stored style: that value's default
    r = client.post("/api/karaoke/theme", json={"template": "plain", "color": "#2F80ED",
                                                 "base": {"text": {"size": "big"}}})
    assert r.status_code == 200 and r.json()["style"]["text"]["size"] == KaraokeStyle().text.size
