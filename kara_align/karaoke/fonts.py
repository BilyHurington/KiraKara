"""Fonts for karaoke subtitles: catalog, resolution and text measurement.

Rendering happens in libass (ffmpeg ``subtitles`` filter), which finds fonts
through fontconfig. We resolve family names through fontconfig too
(``fc-list`` / ``fc-match``), so the file we measure with is the file libass
draws with. Without fontconfig, common font folders are scanned.

libass sizes fonts like VSFilter: the ASS font size is the height
``usWinAscent + usWinDescent`` (OS/2), not the em size. :class:`Measurer`
applies the same conversion before measuring advances with Pillow.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# preferred defaults, first match wins (mac, windows, linux)
DEFAULT_FAMILIES = [
    "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Hiragino Kaku Gothic Pro", "Yu Gothic", "Meiryo",
    "Noto Sans CJK JP", "Source Han Sans JP", "Noto Sans JP", "Noto Sans CJK SC", "Source Han Sans",
    "Hiragino Sans GB", "Microsoft YaHei", "MS Gothic",
]
_FONT_DIRS = ["/System/Library/Fonts", "/Library/Fonts", "~/Library/Fonts", "C:/Windows/Fonts",
              "/usr/share/fonts", "/usr/local/share/fonts", "~/.fonts", "~/.local/share/fonts"]


@dataclass(frozen=True)
class FontFace:
    family: str  # name written into the ASS file
    names: tuple[str, ...]  # all family names incl. localized ones
    path: str
    index: int
    bold: bool


def _fc(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


@functools.lru_cache(maxsize=1)
def list_faces() -> tuple[FontFace, ...]:
    """Faces that can render Japanese (kana + kanji)."""
    faces: list[FontFace] = []
    if _fc("fc-list"):
        out = subprocess.run(["fc-list", ":lang=ja", "family", "file", "index", "weight"],
                             capture_output=True, text=True).stdout
        for line in out.splitlines():
            # "<file>: <family,family>:index=0:weight=80"
            try:
                file, rest = line.split(": ", 1)
            except ValueError:
                continue
            parts = rest.split(":")
            names = tuple(n.strip() for n in parts[0].split(",") if n.strip())
            if not names or names[0].startswith("."):
                continue
            props = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
            weight = _first_int(props.get("weight", "80"))
            faces.append(FontFace(names[0], names, file, _first_int(props.get("index", "0")), weight >= 180))
    else:
        faces = _scan_dirs()
    return tuple(faces)


def _first_int(v: str) -> int:
    v = v.strip().strip("[]").split()[0]
    try:
        return int(float(v))
    except ValueError:
        return 0


def _scan_dirs() -> list[FontFace]:
    from fontTools.ttLib import TTCollection, TTFont

    faces = []
    for d in _FONT_DIRS:
        root = Path(os.path.expanduser(d))
        if not root.is_dir():
            continue
        for f in root.rglob("*"):
            if f.suffix.lower() not in (".ttf", ".otf", ".ttc", ".otc"):
                continue
            try:
                fonts = TTCollection(str(f)).fonts if f.suffix.lower() in (".ttc", ".otc") else [TTFont(str(f), lazy=True)]
            except Exception:
                continue
            for i, font in enumerate(fonts):
                try:
                    cmap = font.getBestCmap() or {}
                    if ord("あ") not in cmap or ord("漢") not in cmap:
                        continue
                    name = font["name"]
                    fam = name.getBestFamilyName() or f.stem
                    weight = font["OS/2"].usWeightClass if "OS/2" in font else 400
                    faces.append(FontFace(fam, (fam,), str(f), i, weight >= 600))
                except Exception:
                    continue
    return faces


def families() -> list[dict]:
    """Family list for the UI: [{family, names, bold_available}]."""
    out: dict[str, dict] = {}
    for f in list_faces():
        e = out.setdefault(f.family, {"family": f.family, "names": list(f.names), "bold": False})
        e["bold"] = e["bold"] or f.bold
    return sorted(out.values(), key=lambda e: (e["family"] not in DEFAULT_FAMILIES, e["family"].lower()))


def installed(family: str) -> bool:
    """A font family this machine has (by any of its names).  With no font list at all (no
    fontconfig, nothing scanned) every family is assumed present, as libass will decide."""
    faces = list_faces()
    return not faces or any(family == f.family or family in f.names for f in faces)


def default_family() -> str:
    names = {f.family for f in list_faces()}
    for fam in DEFAULT_FAMILIES:
        if fam in names:
            return fam
    return next(iter(sorted(names)), "Sans")


@functools.lru_cache(maxsize=64)
def resolve(family: str, bold: bool) -> tuple[str, int]:
    """Font file + face index libass will use for ``family`` (fontconfig match)."""
    family = family or default_family()
    if _fc("fc-match"):
        pattern = f"{family}:weight={'bold' if bold else 'regular'}"
        out = subprocess.run(["fc-match", "-f", "%{file}|%{index}", pattern], capture_output=True, text=True)
        if out.returncode == 0 and "|" in out.stdout:
            file, idx = out.stdout.rsplit("|", 1)
            return file, _first_int(idx or "0")
    cands = [f for f in list_faces() if family in f.names] or list(list_faces())
    if not cands:
        raise RuntimeError("找不到可用于日文的字体")
    best = sorted(cands, key=lambda f: f.bold != bold)[0]
    return best.path, best.index


class Measurer:
    """Advance widths of text as libass will lay it out, in pixels."""

    def __init__(self, family: str, bold: bool, size: float):
        from fontTools.ttLib import TTFont
        from PIL import ImageFont

        self.family = family or default_family()
        path, index = resolve(self.family, bold)
        tt = TTFont(path, fontNumber=index, lazy=True)
        upem = tt["head"].unitsPerEm
        os2 = tt["OS/2"] if "OS/2" in tt else None
        win = (os2.usWinAscent + os2.usWinDescent) if os2 else 0
        if not win:
            hhea = tt["hhea"]
            win = hhea.ascent - hhea.descent
        self.em_scale = upem / win  # ASS size → em size (libass: ass_face_set_size)
        self.size = size
        self._font = ImageFont.truetype(path, size=max(1, round(size * self.em_scale * 4)), index=index)
        self._k = 1 / 4  # measure at 4x for sub-pixel accuracy

    def width(self, text: str) -> float:
        if not text:
            return 0.0
        return self._font.getlength(text) * self._k
