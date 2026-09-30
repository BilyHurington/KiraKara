"""Singers (多人演唱分色): who sings which part, kept through edits; drawn in each singer's colours."""

import io
import re

import numpy as np
import pytest
from fastapi.testclient import TestClient

from kara_align import service as S
from kara_align.karaoke import ass as A
from kara_align.lyrics import singers as SG
from kara_align.lyrics.pairing import merge_lines, split_line
from kara_align.models import KaraokeSinger, KaraokeStyle, Line, LyricsDoc, SingerSpan

from .test_karaoke import _home, _project, needs_ffmpeg  # noqa: F401  (the fixture is used by name)


def _spans(line):
    return [(s.start, s.end, s.singers) for s in line.singer_spans]


# ---------------------------------------------------------------- data


def test_loading_is_lenient_and_numbers_are_kept_in_range():
    ln = Line.model_validate({"text": "あいう", "singers": [2, 2, 0, 10, "3", True, 1],
                              "singer_spans": [{"start": 0, "end": 2, "singers": [3, 3, 99]}, {"start": 2, "end": 1},
                                               "x"]})
    assert ln.singers == [2, 1]
    assert _spans(ln) == [(0, 2, [3])]
    st = KaraokeStyle.model_validate({"singers": {"members": [{"name": "A", "color": "#f00"}] * 12, "mix": "bad"}})
    assert len(st.singers.members) == 9 and st.singers.members[0].color == "#ff0000" and st.singers.mix == "split"
    with pytest.raises(Exception):  # a style sent to be saved is refused instead
        KaraokeStyle.model_validate({"singers": {"members": [{"color": "#f00"}] * 10}}, context={"strict": True})


def test_normalize_joins_clips_and_drops_what_the_line_says_anyway():
    ln = Line(text="abcdef", singers=[1], singer_spans=[
        SingerSpan(start=4, end=9, singers=[2]), SingerSpan(start=0, end=2, singers=[1]),
        SingerSpan(start=2, end=3, singers=[1, 2]), SingerSpan(start=3, end=4, singers=[1, 2])])
    SG.normalize(ln)
    assert _spans(ln) == [(2, 4, [1, 2]), (4, 6, [2])]
    assert SG.effective(ln) == [(1,), (1,), (1, 2), (1, 2), (2,), (2,)]
    assert SG.range_singers(SG.effective(ln), 1, 4) == (1, 2)
    assert SG.range_singers(SG.effective(ln), 0, 2) == (1,)


def test_text_edit_keeps_each_characters_singers():
    ln = Line(text="窓に舞う桜", singers=[1], singer_spans=[SingerSpan(start=4, end=5, singers=[2])])
    old = ln.text
    ln.text = "窓の外に舞う桜"
    SG.remap(ln, old)
    assert _spans(ln) == [(6, 7, [2])]


def test_merge_and_split_carry_the_singers():
    doc = LyricsDoc(lines=[Line(text="あい", singers=[1]), Line(text="うえ", singers=[2]),
                           Line(text="おか", singers=[1, 2], singer_spans=[SingerSpan(start=1, end=2, singers=[3])])])
    merged = merge_lines(doc, [doc.lines[0].id, doc.lines[1].id])
    m = merged.lines[0]
    assert m.text == "あいうえ" and m.singers == [1] and _spans(m) == [(2, 4, [2])]
    parts = split_line(doc, doc.lines[2].id, 1)
    left, right = parts.lines[2], parts.lines[3]
    assert (left.text, left.singers, _spans(left)) == ("お", [1, 2], [])
    assert (right.text, right.singers, _spans(right)) == ("か", [1, 2], [(0, 1, [3])])


def test_removing_a_singer_moves_the_later_ones_up():
    doc = LyricsDoc(lines=[Line(text="あい", singers=[1, 3], singer_spans=[SingerSpan(start=0, end=1, singers=[2])]),
                           Line(text="う", singers=[2])])
    assert SG.shift_numbers(doc, 2) == 2
    # singer 2 of the part leaves (the part is then sung by 1 … no, by nobody else: back to the line's own)
    assert doc.lines[0].singers == [1, 2] and _spans(doc.lines[0]) == [(0, 1, [])]
    assert doc.lines[1].singers == []


def test_markers_need_a_name_used_twice():
    doc = LyricsDoc(lines=[Line(text="A：あいう"), Line(text="B：かきく"), Line(text="A&B：さしす"),
                           Line(text="（Hey!）たちつ"), Line(text="全员：なにぬ"), Line(text="はひふ")])
    found = SG.detect_markers(doc)
    assert [m.names for m in found] == [["A"], ["B"], ["A", "B"], ["全员"]]  # not "(Hey!)": once only
    assert SG.marker_names(found) == ["A", "B"]
    assert not SG.detect_markers(LyricsDoc(lines=[Line(text="（Hey!）たちつ"), Line(text="はひふ")]))


# ---------------------------------------------------------------- service


def test_assign_markers_and_remove(tmp_path):
    h = _project(tmp_path)
    l1, l2 = h.project.lyrics.lines
    # the song's lyrics named the singers: 「A：窓に舞う桜」「B：君」, and a line both sing
    S.update_line(h, l1.id, text="Ａ：窓に舞う桜")
    S.update_line(h, l2.id, text="Ａ＆Ｂ：君")
    lines = S.singer_markers(h)["lines"]
    assert [x["names"] for x in lines] == [["Ａ"], ["Ａ", "Ｂ"]]
    msgs = S.apply_singer_markers(h, strip=True)
    assert "Ａ、Ｂ" in msgs[0]
    l1, l2 = h.project.lyrics.lines
    assert (l1.text, l1.singers, l2.text, l2.singers) == ("窓に舞う桜", [1], "君", [1, 2])
    assert [m.name for m in h.project.karaoke.singers.members] == ["Ａ", "Ｂ"]
    assert h.project.karaoke.singers.members[0].color != h.project.karaoke.singers.members[1].color
    assert "".join(s.surface for s in l1.segments) == l1.text

    S.set_line_singers(h, [{"line_id": l1.id, "singers": [2], "spans": [{"start": 0, "end": 1, "singers": [1, 2]}],
                            "text": l1.text}])
    assert _spans(l1) == [(0, 1, [1, 2])]
    with pytest.raises(S.ServiceError):
        S.set_line_singers(h, [{"line_id": l1.id, "singers": [1], "spans": [], "text": "别的字"}])
    with pytest.raises(S.ServiceError):
        S.set_line_singers(h, [{"line_id": l1.id, "singers": [], "spans": [{"start": 0, "end": 99}]}])
    assert S.remove_singer(h, 1) == 2
    assert (l1.singers, _spans(l1), l2.singers) == ([1], [], [1])
    assert [m.name for m in h.project.karaoke.singers.members] == ["Ｂ"]


def test_http_api(tmp_path):
    from kara_align.web.server import create_app

    _project(tmp_path)
    c = TestClient(create_app(tmp_path))
    pid = "proj"  # (a workspace names its projects by folder)
    r = c.put(f"/api/projects/{pid}/karaoke/singers",
              json={"members": [{"name": "A", "color": "#ED35B3"}, {"name": "B", "color": "#2F80ED"}],
                    "mix": "gradient", "direction": "horizontal"})
    assert r.status_code == 200 and r.json()["singers"]["mix"] == "gradient"
    assert c.put(f"/api/projects/{pid}/karaoke/singers", json={"members": [{"color": "red"}]}).status_code == 400
    line = c.get(f"/api/projects/{pid}").json()["project"]["lyrics"]["lines"][0]
    r = c.put(f"/api/projects/{pid}/singers", json={"lines": [{"line_id": line["id"], "singers": [1, 2], "spans": []}]})
    assert r.status_code == 200 and r.json()["project"]["lyrics"]["lines"][0]["singers"] == [1, 2]
    assert r.json()["view"]["singer_markers"] == 0
    cols = c.post("/api/karaoke/singer-colors", json={"members": [{"color": "#2F80ED", "color_sung": "#000000"}]}).json()
    assert cols[0]["sung"] == "#000000" and cols[0]["unsung"].startswith("#")
    r = c.delete(f"/api/projects/{pid}/karaoke/singers/2")
    assert r.status_code == 200 and r.json()["project"]["lyrics"]["lines"][0]["singers"] == [1]
    assert c.delete(f"/api/projects/{pid}/karaoke/singers/5").status_code == 400
    assert c.get(f"/api/projects/{pid}/singers/markers").json()["lines"] == []


# ---------------------------------------------------------------- subtitles


def _two_singers(h, mix="split", direction="vertical", sweep="own"):
    l1, l2 = h.project.lyrics.lines
    l1.singers = [1, 2]
    l1.singer_spans = [SingerSpan(start=0, end=1, singers=[1]), SingerSpan(start=4, end=5, singers=[2])]
    l2.singers = [2]
    st = h.project.karaoke.model_copy(deep=True)
    st.singers.members = [KaraokeSinger(name="Ann", color="#ED35B3"), KaraokeSinger(name="Bo, b", color="#2F80ED")]
    st.singers.mix, st.singers.direction, st.ruby.sweep = mix, direction, sweep
    st.translation.enabled = True
    l1.translation = "窗外"
    h.project.karaoke = st
    h.save()


def _events(text, body):
    return [ln for ln in text.splitlines() if ln.startswith("Dialogue:") and ln.endswith(body)]


def test_no_singers_changes_nothing(tmp_path):
    h = _project(tmp_path)
    before, _ = S.karaoke_ass(h)
    h.project.lyrics.lines[0].singers = [3]  # a number the style has no singer for: its own colours
    after, _ = S.karaoke_ass(h)
    assert before == after and "KMain_" not in after


def test_singers_get_their_own_styles_and_bands(tmp_path):
    h = _project(tmp_path)
    _two_singers(h)
    text, _ = S.karaoke_ass(h)
    assert re.search(r"^Style: KMain_1,", text, re.M) and re.search(r"^Style: KTrans_2,", text, re.M)
    # 窓: singer 1 alone (its style, its name as the event's actor; commas left out of the name)
    assert any(",KMain_1,Ann," in e for e in _events(text, "窓"))
    assert any(",KMain_2,Bo b," in e for e in _events(text, "}桜"))
    # に (both): two copies, the upper band singer 1, the lower singer 2, meeting at one y
    ni = [e for e in _events(text, "}に") if ",KMain_" in e]
    assert [e.split(",")[3] for e in ni] == ["KMain_1", "KMain_2"]
    (a0, a1), (b0, b1) = (tuple(map(int, re.search(r"\\clip\(0,(\d+),1920,(\d+)\)", e).groups())) for e in ni)
    assert a0 == 0 and a1 == b0 and b1 == 1080
    # the translation in the line's first singer's colours
    assert any(",KTrans_1," in e for e in _events(text, "窗外"))


def test_following_sweep_bands_partition_each_chunk(tmp_path):
    h = _project(tmp_path)
    _two_singers(h, sweep="base")
    text, _ = S.karaoke_ass(h)
    ni = [e for e in text.splitlines() if ",KMain_" in e and e.endswith("}に")]
    assert len(ni) == 4  # unsung + sung per band
    rects = [re.findall(r"\\clip\((\d+),(\d+),(\d+),(\d+)\)", e) for e in ni]
    # every step of the animation: unsung and sung of a band meet at the same x
    for unsung, sung in (rects[0:2], rects[2:4]):
        assert len(unsung) == len(sung)
        for u, s_ in zip(unsung, sung):
            assert s_[0] == "0" and u[2] == "1920" and u[0] == s_[2] and u[1] == s_[1] and u[3] == s_[3]


def test_band_clip_follows_the_sweep_only_inside_the_band():
    moves = [(0, 1, 100.0), (0, 1000, 300.0), (1000, 1001, 400.0)]
    tags = A.band_clip(moves, (150, 0, 250, 1080), True, 400.0)
    # nothing before the band is reached, then it grows from 150 to 250 between 250 ms and 750 ms
    assert tags.startswith("\\clip(150,0,150,1080)")
    assert "\\t(250,750,\\clip(150,0,250,1080))" in tags
    assert A.band_clip([], (150, 0, 250, 1080), False, 400.0) == "\\clip(250,0,250,1080)"


def test_gradient_and_horizontal_bands():
    cols = {1: {r: "#FF0000" for r in A._ROLES}, 2: {r: "#0000FF" for r in A._ROLES}}
    grad = A.plan_bands((1, 2), cols, "gradient", "vertical", 800, 880, [], 1920, 1080)
    assert len(grad) >= 4 and all(b.blend for b in grad)
    assert grad[0].rect[1] == 0 and grad[-1].rect[3] == 1080
    reds = [int(b.colors["sung"][1:3], 16) for b in grad]
    assert reds == sorted(reds, reverse=True) and reds[0] > reds[-1]
    side = A.plan_bands((1, 2), cols, "split", "horizontal", 0, 0, [(100, 180), (180, 260)], 1920, 1080)
    assert [b.singer for b in side] == [1, 2, 1, 2]
    assert [b.rect[0] for b in side] == [0, 140, 180, 220] and side[-1].rect[2] == 1920
    assert A.plan_bands((7,), cols, "split", "vertical", 0, 0, [], 1920, 1080) == [A.BASE_BAND]


@needs_ffmpeg
def test_parts_sung_together_render_in_both_colours(tmp_path):
    from PIL import Image

    h = _project(tmp_path)
    _two_singers(h)
    arr = np.asarray(Image.open(io.BytesIO(S.karaoke_preview(h, 2250))).convert("RGB")).astype(int)
    pink = (arr[..., 0] > 180) & (arr[..., 2] > 120) & (arr[..., 1] < 110)  # singer 1 sung (#ED35B3)
    blue = (arr[..., 2] > 180) & (arr[..., 0] < 110)  # singer 2 sung (#2F80ED)
    # に舞う (both, sung by now): pink above, blue below
    x0, x1 = 440, 760
    rows_p = np.where(pink[:, x0:x1].any(axis=1))[0]
    rows_b = np.where(blue[:, x0:x1].any(axis=1))[0]
    assert rows_p.size and rows_b.size and np.median(rows_p) < np.median(rows_b)


def test_split_parts_glow_blended(tmp_path):
    h = _project(tmp_path)
    _two_singers(h)
    h.project.karaoke.glow.enabled = True
    text, _ = S.karaoke_ass(h)
    ni = _events(text, "}に")
    fills = [e for e in ni if ",KMain_" in e]
    glows = [e for e in ni if ",KGlow," in e]
    assert len(fills) == 2  # the text: one band each
    assert len(glows) > 4  # its glow: thin strips blending the two (no seam beside the glyphs)
    assert len({re.search(r"\\3c(&H\w+&)", e).group(1) for e in glows}) > 4
    # side by side: the glow is one even blend (strips in every character would stripe it)
    _two_singers(h, direction="horizontal")
    h.project.karaoke.glow.enabled = True
    text, _ = S.karaoke_ass(h)
    ni = _events(text, "}に")
    assert len([e for e in ni if ",KGlow," in e]) == 2 and len([e for e in ni if ",KMain_" in e]) == 2  # one character: two halves
