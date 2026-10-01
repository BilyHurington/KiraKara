"""The song's cover as the picture behind the subtitles ("模糊封面背景"), for songs with audio only.

The cover comes from the music platform the lyrics were fetched from (NetEase / QQ Music give it
with the song).  The picture: the cover blurred and darkened over the whole frame, the cover itself
in the upper middle (above where the lyrics run), and the bottom a little darker for the lyrics.
"""

from __future__ import annotations

import io
import math

COVER_MAX_BYTES = 10 * 1024 * 1024


class CoverError(ValueError):
    pass


def fetch_cover(url: str) -> bytes:
    """The cover image (only from the platforms' image hosts, as the lyrics are fetched)."""
    from ..lyrics.fetch.safe_http import SafeClient
    from ..lyrics.fetch.types import FetchError

    try:
        with SafeClient(max_bytes=COVER_MAX_BYTES) as client:
            r = client.get(url)
    except FetchError as e:
        raise CoverError(f"无法下载封面：{e}") from e
    if r.status_code != 200 or not r.content:
        raise CoverError(f"无法下载封面（HTTP {r.status_code}）")
    return r.content


def blurred_cover(data: bytes, size: tuple[int, int] = (1920, 1080)) -> bytes:
    """A JPEG of ``size``: the cover blurred over the frame with the cover itself in front."""
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

    try:
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except Exception as e:
        raise CoverError("封面不是可以读取的图片") from e
    W, H = size
    # the backdrop: cover filling the frame, blurred, darker, a little more saturated
    s = max(W / im.width, H / im.height) * 1.08
    bg = im.resize((math.ceil(im.width * s), math.ceil(im.height * s)), Image.LANCZOS)
    x0, y0 = (bg.width - W) // 2, (bg.height - H) // 2
    bg = bg.crop((x0, y0, x0 + W, y0 + H)).filter(ImageFilter.GaussianBlur(max(W, H) / 36))
    bg = ImageEnhance.Color(ImageEnhance.Brightness(bg).enhance(0.5)).enhance(1.15)
    # darker towards the bottom, where the lyrics are
    shade = Image.new("L", (1, H))
    for y in range(H):
        t = max(0.0, (y / H - 0.55) / 0.45)
        shade.putpixel((0, y), int(115 * t * t))
    bg = Image.composite(Image.new("RGB", (W, H)), bg, shade.resize((W, H)))
    # the cover: square, rounded, a soft shadow, in the upper middle
    side = round(H * 0.46)
    sq = min(im.width, im.height)
    cover = im.crop(((im.width - sq) // 2, (im.height - sq) // 2, (im.width + sq) // 2, (im.height + sq) // 2))
    cover = cover.resize((side, side), Image.LANCZOS)
    radius = round(side * 0.04)
    mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, side - 1, side - 1), radius, fill=255)
    cx, cy = (W - side) // 2, round(H * 0.36 - side / 2)
    pad = round(side * 0.12)
    shadow = Image.new("L", (side + 2 * pad, side + 2 * pad), 0)
    ImageDraw.Draw(shadow).rounded_rectangle((pad, pad, pad + side, pad + side), radius, fill=150)
    shadow = shadow.filter(ImageFilter.GaussianBlur(pad / 2.5))
    bg.paste(Image.new("RGB", shadow.size), (cx - pad, cy - pad + round(side * 0.03)), shadow)
    bg.paste(cover, (cx, cy), mask)
    out = io.BytesIO()
    bg.save(out, "JPEG", quality=92)
    return out.getvalue()
