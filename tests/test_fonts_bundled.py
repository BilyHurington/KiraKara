"""The bundled Noto Sans CJK and the font choices that depend on the platform (issues 5 and 9)."""

import re
import shutil
from pathlib import Path

import pytest

from kara_align import service as S
from kara_align.karaoke import fonts

from .test_karaoke import _project

APP_FONTS = Path(__file__).resolve().parents[1] / "fonts"
needs_bundled = pytest.mark.skipif(not (APP_FONTS / "NotoSansCJK-Bold.ttc").exists(),
                                   reason="the bundled fonts are not here (packaging/fetch_fonts.py)")


def _clear():
    for fn in (fonts.list_faces, fonts.bundled_faces, fonts.resolve, fonts._mac_han_fallback, fonts._charmap):
        fn.cache_clear()


@pytest.fixture
def windows_like(monkeypatch):
    """No macOS fallback (Windows / Linux): the bundled font is the default."""
    from types import SimpleNamespace

    monkeypatch.setenv("KARA_ALIGN_FONTS", str(APP_FONTS))
    monkeypatch.setattr(fonts, "sys", SimpleNamespace(platform="win32"))  # (only the font module's view)
    _clear()
    yield
    monkeypatch.undo()
    _clear()


@needs_bundled
def test_bundled_font_is_the_default_where_no_system_fallback_is_known(windows_like):
    assert fonts.default_family() == fonts.BUNDLED_JP
    path, index = fonts.resolve(fonts.BUNDLED_JP, True)
    assert Path(path).name == "NotoSansCJK-Bold.ttc" and index == 0
    assert fonts.resolve(fonts.BUNDLED_SC, False)[0].endswith("NotoSansCJK-Regular.ttc")
    assert not fonts.lacking(fonts.BUNDLED_JP, True, "窓に舞う桜 我们这样说话")  # Japanese and Chinese
    assert fonts.installed(fonts.BUNDLED_SC)


@needs_bundled
def test_translation_in_chinese_forms_and_a_title_card_with_every_character(tmp_path, windows_like):
    h = _project(tmp_path)
    l1, l2 = h.project.lyrics.lines
    l1.translation, l2.translation = "窗外飞舞的樱花", "你"
    st = h.project.karaoke.model_copy(deep=True)
    st.translation.enabled = True
    S.set_karaoke_style(h, st.model_dump(mode="json"))
    text, _ = S.karaoke_ass(h)
    style_font = lambda name: [ln for ln in text.splitlines() if ln.startswith(f"Style: {name},")][0].split(",")[1]
    assert style_font("KMain") == fonts.BUNDLED_JP and style_font("KTrans") == fonts.BUNDLED_SC
    # a lyric font lacking the card's characters: the card is drawn in one that has them all
    lyric = next((f.family for f in fonts.list_faces() if f.family != fonts.BUNDLED_JP
                  and fonts.lacking(f.family, True, "我们的歌")), None)
    if lyric is None:
        pytest.skip("no font here that lacks simplified Chinese")
    st.text.font = lyric
    st.info.enabled = True
    S.set_karaoke_style(h, st.model_dump(mode="json"))
    h.project.song_info_text = "我们的歌\nプロジェクト・フェアリー"
    text, _ = S.karaoke_ass(h)
    cards = [ln for ln in text.splitlines() if ",KInfo," in ln]
    assert cards and all(f"\\fn{fonts.BUNDLED_JP}" in ln for ln in cards if "\\fn" in ln)


@needs_bundled
def test_font_folder_scan_is_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("KARA_ALIGN_HOME", str(tmp_path / "home"))
    d = tmp_path / "fonts"
    d.mkdir()
    shutil.copy(APP_FONTS / "NotoSansCJK-Bold.ttc", d)
    first = fonts._faces_in(sorted(d.iterdir()), cache=True)
    assert {f.family for f in first} >= {fonts.BUNDLED_JP, fonts.BUNDLED_SC}
    assert (tmp_path / "home" / "cache" / "fonts.json").exists()

    def boom(_):
        raise AssertionError("a cached font file was read again")

    monkeypatch.setattr(fonts, "_read_faces", boom)
    assert fonts._faces_in(sorted(d.iterdir()), cache=True) == first


def test_fontsdir_is_escaped_for_the_filter_graph(tmp_path, monkeypatch):
    from kara_align.karaoke.render import _subtitles_filter, filter_path

    d = tmp_path / "字体 a:b'c [1],y;z"
    d.mkdir()
    (d / "x.ttf").write_bytes(b"")
    monkeypatch.setenv("KARA_ALIGN_FONTS", str(d))
    v = filter_path(d)
    assert "\\\\:" in v and "\\\\\\'" in v and "\\[" in v and "\\]" in v and "\\," in v and "\\;" in v
    assert _subtitles_filter("k.ass") == f"subtitles=k.ass:fontsdir={v}"
    # unescaped once per level, the value is the path again
    once = re.sub(r"\\(.)", r"\1", v)
    assert re.sub(r"\\(.)", r"\1", once) == d.resolve().as_posix()
