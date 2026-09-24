"""Karaoke subtitles: chunking, ruby, timing, schedule, ASS, rendering."""

import io
import re
import shutil

import numpy as np
import pytest
import soundfile as sf

from kara_align import service as S
from kara_align.align.backends.fake import ScriptedBackend
from kara_align.karaoke import ass as A
from kara_align.karaoke.fonts import Measurer, default_family
from kara_align.models import KaraokeStyle, Line, Segment, Unit
from kara_align.reading.prepare import units_from_spec

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffprobe") is None, reason="needs ffmpeg with libass")

# 窓に舞う桜: まど に まう さくら
SCRIPT = [("ma", 1000, 1200), ("do", 1200, 1400), ("ni", 1400, 1700), ("ma", 1800, 2000), ("u", 2000, 2200),
          ("sa", 2300, 2500), ("ku", 2500, 2700), ("ra", 2700, 3000),
          ("ki", 5000, 5200), ("mi", 5200, 5400)]


def _seg(surface, reading, lang="ja"):
    units = units_from_spec(reading, None, lang) if reading else []
    return Segment(surface=surface, reading=reading, units=units, reading_source="ai" if reading else "none")


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("KARA_ALIGN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(ScriptedBackend, "default_script", SCRIPT)


def _project(tmp_path):
    h = S.create_dir(tmp_path / "proj", "k", "plain")
    S.update_settings(h, config={"backend": "scripted"})
    pv = S.parse_lyrics(h, "窓に舞う桜\n君\n", origin="paste")
    S.apply_lyrics(h, pv["preview_id"])
    l1, l2 = h.project.lyrics.lines
    l1.segments = [_seg("窓", "まど"), _seg("に", "に"), _seg("舞う", "まう"), _seg("桜", "さくら")]
    l2.segments = [_seg("君", "きみ")]
    h.save()
    x = (np.random.default_rng(0).standard_normal(22050 * 7) * 0.05).astype(np.float32)
    sf.write(tmp_path / "s.wav", x, 22050)
    S.add_audio(h, tmp_path / "s.wav", "original")
    S.run_align(h)
    return h


# ---------------------------------------------------------------- pure helpers


def test_ass_color_and_time():
    assert A.ass_color("#2F80ED") == "&H00ED802F"
    assert A.ass_color("#000000", 50) == "&H80000000"
    assert A.ass_time(61234) == "0:01:01.23"


def test_okurigana_split_keeps_whole_units():
    seg = _seg("舞う", "まう")
    pieces = A._split_affixes(seg)
    assert [(s, [u.reading for u in us]) for s, us in pieces] == [("舞", ["ま"]), ("う", ["う"])]
    # prefix kana and a reading that cannot be split on a unit boundary
    assert [s for s, _ in A._split_affixes(_seg("お茶", "おちゃ"))] == ["お", "茶"]
    assert [s for s, _ in A._split_affixes(_seg("真新", "まっさら"))] == ["真新"]


def _line():
    return Line(text="窓に舞う桜", segments=[_seg("窓", "まど"), _seg("に", "に"), _seg("舞う", "まう"), _seg("桜", "さくら")])


def _times(line, t0=1000, step=200):
    out, t = {}, t0
    for u in line.units():
        out[u.id] = (t, t + step)
        t += step
    return out


def test_chunks_ruby_targets_and_scripts():
    line = _line()
    st = KaraokeStyle()
    ch = A.build_chunks(line, _times(line), st, {})
    assert [c.base_text for c in ch] == ["窓", "に", "舞", "う", "桜"]
    assert [c.ruby_text for c in ch] == ["まど", "", "ま", "", "さくら"]
    st.ruby.script = "katakana"
    assert [c.ruby_text for c in A.build_chunks(line, _times(line), st, {})] == ["マド", "", "マ", "", "サクラ"]
    st.ruby.target = "all"  # kana now get katakana ruby too
    assert [c.ruby_text for c in A.build_chunks(line, _times(line), st, {})] == ["マド", "ニ", "マ", "ウ", "サクラ"]
    st.ruby.script, st.ruby.target = "hiragana", "all"  # hiragana over hiragana is suppressed
    assert [c.ruby_text for c in A.build_chunks(line, _times(line), st, {})] == ["まど", "", "ま", "", "さくら"]
    st.ruby.enabled = False
    assert all(not c.ruby for c in A.build_chunks(line, _times(line), st, {}))


def test_karaoke_tags_follow_unit_times_exactly():
    parts = [A.Part("ま", 1000, 1210), A.Part("ど", 1300, 1400)]
    tags = A._karaoke(parts, 500, "kf")
    assert tags == "{\\k50}{\\kf21}ま{\\k9}{\\kf10}ど"
    cs = sum(int(x) for x in re.findall(r"\\kf?(\d+)", tags))
    assert cs * 10 == 1400 - 500


def test_untimed_parts_never_invent_duration():
    line = Line(text="あ、い", segments=[_seg("あ", "あ"), _seg("、", None), _seg("い", "い")])
    a, i = line.segments[0].units[0].id, line.segments[2].units[0].id
    ch = A.build_chunks(line, {a: (1000, 1200), i: (1500, 1700)}, KaraokeStyle(), {})
    comma = ch[1].base[0]
    assert comma.start == comma.end == 1200  # zero-length, at the neighbour boundary


def _laid(starts):
    return [A.LaidLine(line=Line(text=str(s)), chunks=[], start=s, end=s + 2000) for s in starts]


def test_schedule_two_slots_early_show_without_overlap():
    st = KaraokeStyle()
    lines = _laid([10000, 13000, 16000, 19000])
    A.schedule(lines, st)
    assert [ll.slot for ll in lines] == [0, 1, 0, 1]
    assert lines[1].show_from == 13000 - 4000  # slot free: shown early (capped at 4 s)
    for a, b in ((lines[0], lines[2]), (lines[1], lines[3])):
        assert a.show_to <= b.show_from  # same slot never overlaps
    for ll in lines:
        assert ll.start - ll.show_from >= 200


def test_schedule_without_early_show_uses_lead_in():
    st = KaraokeStyle()
    st.timing.early_show = False
    lines = _laid([10000, 30000])
    A.schedule(lines, st)
    assert lines[1].show_from == 30000 - st.timing.lead_in_ms


# ---------------------------------------------------------------- whole ASS


def test_build_ass_from_alignment(tmp_path):
    h = _project(tmp_path)
    text, warnings = S.karaoke_ass(h)
    assert "PlayResX: 1920" in text and "[Events]" in text
    dialogues = [l for l in text.splitlines() if l.startswith("Dialogue")]
    ruby = [l for l in dialogues if ",KRuby," in l]
    assert any(l.endswith("{\\kf20}ま{\\kf20}ど") for l in ruby)
    assert any("{\\kf20}き{\\kf20}み" in l for l in ruby)
    main = [l for l in dialogues if ",KMain," in l]
    assert any(l.endswith("窓") for l in main) and any(l.endswith("う") for l in main)
    # 2 lines alternate: first left half, second right half
    xs = [float(re.search(r"\\pos\(([\d.]+),", l).group(1)) for l in main]
    assert min(xs) < 960 < max(xs)
    # export route gives the same file
    out = S.export(h, "karaoke-ass")
    assert out.filename == "karaoke.ass" and out.content == text


def test_style_is_saved_and_validated(tmp_path):
    h = _project(tmp_path)
    st = h.project.karaoke.model_dump()
    st["layout"]["lines"] = 1
    st["text"]["color_sung"] = "#FF0000"
    S.set_karaoke_style(h, st)
    assert S.open_dir(h.dir).project.karaoke.layout.lines == 1
    st["layout"]["lines"] = 9
    with pytest.raises(S.ServiceError):
        S.set_karaoke_style(h, st)


def test_presets_keep_layout_and_change_look():
    from kara_align.karaoke.presets import make_preset, preset_list

    base = KaraokeStyle()
    base.layout.lines = 1
    s = make_preset("sakura", keep=base)
    assert s.layout.lines == 1 and s.text.color_sung == "#FF5C8A" and s.preset == "sakura"
    assert {p["name"] for p in preset_list()} >= {"classic", "fresh", "sakura", "minimal"}


# ---------------------------------------------------------------- rendering


@needs_ffmpeg
def test_measurement_matches_libass_rendering(tmp_path):
    """Our layout widths must agree with what libass draws."""
    from PIL import Image

    from kara_align.karaoke.render import preview_png

    fam = default_family()
    size = 88
    text = "窓に舞う桜きみと歩いた"
    ass = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 2\n\n[V4+ Styles]\n"
           "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
           "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
           "MarginR, MarginV, Encoding\n"
           f"Style: T,{fam},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n\n"
           "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
           f"Dialogue: 0,0:00:00.00,0:00:05.00,T,,0,0,0,,{{\\pos(100,300)}}{text}\n")
    img = np.asarray(Image.open(io.BytesIO(preview_png(ass, 1000, (1920, 1080)))).convert("L"))
    cols = np.where(img.max(axis=0) > 60)[0]
    ink = cols.max() - cols.min()
    predicted = Measurer(fam, True, size).width(text)
    assert abs(ink - predicted) / predicted < 0.05, (ink, predicted)


@needs_ffmpeg
def test_preview_and_burn(tmp_path):
    import subprocess

    from PIL import Image

    h = _project(tmp_path)
    png = S.karaoke_preview(h, 1500)
    img = Image.open(io.BytesIO(png))
    assert img.size == (1920, 1080)
    arr = np.asarray(img.convert("RGB")).astype(int)
    assert arr[:540].max() < 30  # top half black (subtitles at the bottom)
    sung = (arr[..., 2] > 150) & (arr[..., 0] < 120)  # blue = already sung
    assert sung.sum() > 50
    before = S.karaoke_preview(h, 900)  # nothing sung yet
    arr0 = np.asarray(Image.open(io.BytesIO(before)).convert("RGB")).astype(int)
    assert ((arr0[..., 2] > 150) & (arr0[..., 0] < 120)).sum() < sung.sum()

    out = S.karaoke_burn(h, background="black", audio="original")
    path = h.dir / "exports" / out["filename"]
    info = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                           "-show_entries", "format=duration", "-of", "json", str(path)], capture_output=True, text=True)
    assert '"video"' in info.stdout and '"audio"' in info.stdout and '"width": 1920' in info.stdout


class _FixedMeasurer:
    """1 lyric char = 100 px, 1 ruby char = 45 px."""

    def __init__(self, per_char):
        self.per_char = per_char

    def width(self, text):
        return len(text) * self.per_char


def test_ruby_overhangs_kana_but_never_another_reading():
    main, ruby = _FixedMeasurer(100), _FixedMeasurer(45)
    mk = lambda base, rb="": A.Chunk([A.Part(base, 0, 1)], [A.Part(rb, 0, 1)] if rb else [])
    # 新(あたら) between kana: 135 px of ruby over 100 px may overhang the kana
    w = A.chunk_widths([mk("は"), mk("新", "あたら"), mk("しい")], main, ruby, 45, "widen")
    assert w[1] == 100
    # two kanji with readings side by side: widen so readings do not collide
    w = A.chunk_widths([mk("新", "あたら"), mk("制", "せいふく")], main, ruby, 45, "widen")
    assert w[0] > 100 and w[1] > 100
    # at the start of a line the ruby may overhang into the margin
    assert A.chunk_widths([mk("新", "あたら"), mk("しい")], main, ruby, 45, "widen")[0] == 100
    # overflow never widens
    assert A.chunk_widths([mk("制", "せいふく")], main, ruby, 45, "overflow") == [100]


def _main_x(text):
    xs = [float(re.search(r"\\pos\(([\d.]+),", l).group(1)) for l in text.splitlines()
          if l.startswith("Dialogue") and ",KMain," in l]
    return min(xs), max(xs)


def test_alternate_indent_moves_short_lines_toward_centre(tmp_path):
    h = _project(tmp_path)
    st = h.project.karaoke.model_copy(deep=True)
    st.layout.alternate_indent = 0
    lo0, hi0 = _main_x(A.build_ass(h.project, h.project.result(), st)[0])
    st.layout.alternate_indent = 240
    lo1, hi1 = _main_x(A.build_ass(h.project, h.project.result(), st)[0])
    assert lo1 - lo0 == pytest.approx(240, abs=0.2)  # upper (left) line moved right
    assert hi0 - hi1 == pytest.approx(240, abs=0.2)  # lower (right) line moved left
    st.layout.arrangement = "center"  # indent only applies to alternating lines
    lo2, _ = _main_x(A.build_ass(h.project, h.project.result(), st)[0])
    st.layout.alternate_indent = 0
    assert _main_x(A.build_ass(h.project, h.project.result(), st)[0])[0] == lo2


def test_long_line_slides_back_instead_of_shrinking(tmp_path):
    h = _project(tmp_path)
    st = h.project.karaoke.model_copy(deep=True)
    st.layout.alternate_indent = 5000  # far more than any line has room for
    text, warnings = A.build_ass(h.project, h.project.result(), st)
    assert not any("缩小" in w for w in warnings)  # indent never causes shrinking
    lo, hi = _main_x(text)
    assert 140 < lo and hi < 1920 - 140  # still inside the margins
