"""The song's cover as the picture behind the subtitles (audio only, lyrics from a music link)."""

import io

import pytest
from PIL import Image

from kara_align import service as S
from kara_align.karaoke import cover as C
from kara_align.lyrics.fetch.netease import _cover
from kara_align.lyrics.fetch.safe_http import is_allowed_host
from kara_align.models import SourceSnapshot

from .test_karaoke import _project


def _jpeg(color=(240, 180, 20), size=(600, 600)) -> bytes:
    im = Image.new("RGB", size, color)
    for x in range(size[0] // 3):  # something sharp in it
        for y in range(size[1] // 3):
            im.putpixel((x, y), (20, 40, 200))
    out = io.BytesIO()
    im.save(out, "JPEG")
    return out.getvalue()


def test_the_picture():
    im = Image.open(io.BytesIO(C.blurred_cover(_jpeg(), (1920, 1080))))
    assert im.size == (1920, 1080) and im.format == "JPEG"
    side = round(1080 * 0.46)
    cx, cy = (1920 - side) // 2, round(1080 * 0.36 - side / 2)
    # the cover itself, sharp, in the upper middle (its blue corner, its yellow rest)
    b = im.getpixel((cx + 30, cy + 30))
    y = im.getpixel((cx + side - 30, cy + side - 30))
    assert b[2] > 150 and b[0] < 80 and y[0] > 200 and y[1] > 140
    # around it the blurred cover, darker; darkest at the bottom where the lyrics are
    top, bottom = im.getpixel((100, 80)), im.getpixel((100, 1070))
    assert sum(top) < sum(y) and sum(bottom) < sum(top)
    with pytest.raises(C.CoverError):
        C.blurred_cover(b"not an image")


def test_covers_come_from_the_platforms_image_hosts():
    assert is_allowed_host("p2.music.126.net") and is_allowed_host("y.gtimg.cn")
    assert not is_allowed_host("example.com")
    assert _cover({"al": {"picUrl": "http://p1.music.126.net/a/b.jpg"}}) == "https://p1.music.126.net/a/b.jpg?param=1000y1000"
    assert _cover({"al": {}}) is None


def test_cover_background(tmp_path, monkeypatch):
    h = _project(tmp_path)
    with pytest.raises(S.ServiceError, match="没有封面"):
        S.cover_background(h)  # pasted lyrics: no cover
    h.project.sources.append(SourceSnapshot(origin="netease", platform_song_id="123", text="x",
                                            fetched_meta={"cover_url": "https://p1.music.126.net/c.jpg"}))
    asked = []
    monkeypatch.setattr(C, "fetch_cover", lambda url: asked.append(url) or _jpeg())
    bg = S.cover_background(h)
    assert asked == ["https://p1.music.126.net/c.jpg"]
    assert bg.kind == "image" and (bg.width, bg.height) == (1920, 1080) and bg.filename == "歌曲封面（模糊背景）.jpg"
    assert S.picture(h)["source"] == "background"
    assert S.project_view(h)["view"]["cover"] is True
    # lyrics fetched before covers were kept: the platform is asked again
    h.project.sources[-1].fetched_meta = {}
    from kara_align.lyrics import fetch as F
    from kara_align.lyrics.fetch.types import FetchedSong

    monkeypatch.setattr(F, "fetch_song", lambda p, i: FetchedSong(p, i, cover_url="https://y.gtimg.cn/x.jpg"))
    S.cover_background(h)
    assert asked[-1] == "https://y.gtimg.cn/x.jpg"
    assert len(list((h.dir / "assets").glob("*.jpg"))) == 1  # the previous picture is gone
