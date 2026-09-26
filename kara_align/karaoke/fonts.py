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
import re
import shutil
import subprocess
import sys
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


def fc_escape(family: str) -> str:
    """A family name as a literal in a fontconfig pattern ("-" starts the size, ":" a property,
    "," separates families; "\\" escapes)."""
    return re.sub(r"([\\\-:,=])", r"\\\1", family)


@functools.lru_cache(maxsize=64)
def resolve(family: str, bold: bool) -> tuple[str, int]:
    """Font file + face index libass will use for ``family`` (fontconfig match)."""
    family = family or default_family()
    if _fc("fc-match"):
        pattern = f"{fc_escape(family)}:weight={'bold' if bold else 'regular'}"
        out = subprocess.run(["fc-match", "-f", "%{file}|%{index}", pattern], capture_output=True, text=True)
        if out.returncode == 0 and "|" in out.stdout:
            file, idx = out.stdout.rsplit("|", 1)
            return file, _first_int(idx or "0")
    cands = [f for f in list_faces() if family in f.names] or list(list_faces())
    if not cands:
        raise RuntimeError("找不到可用于日文的字体")
    best = sorted(cands, key=lambda f: f.bold != bold)[0]
    return best.path, best.index


@functools.lru_cache(maxsize=32)
def _charmap(path: str, index: int) -> frozenset[int]:
    from fontTools.ttLib import TTFont

    try:
        return frozenset((TTFont(path, fontNumber=index, lazy=True).getBestCmap() or {}).keys())
    except Exception:
        return frozenset()


def _is_han(cp: int) -> bool:
    return 0x3400 <= cp <= 0x9FFF or 0xF900 <= cp <= 0xFAFF or 0x20000 <= cp <= 0x3FFFF or 0x3000 <= cp <= 0x303F


@functools.lru_cache(maxsize=1)
def _mac_han_fallback() -> Optional[str]:
    """macOS: libass there asks CoreText for a missing glyph, which answers PingFang for Chinese
    characters; that font is hidden from fontconfig, so it is looked up by file."""
    if sys.platform != "darwin":
        return None
    import glob

    for pat in ("/System/Library/Fonts/PingFang.ttc",
                "/System/Library/AssetsV2/com_apple_MobileAsset_Font*/*/AssetData/PingFang.ttc"):
        found = sorted(glob.glob(pat))
        if found:
            return found[0]
    return None


@functools.lru_cache(maxsize=4)
def _pingfang_sc(path: str, bold: bool) -> Optional[int]:
    from fontTools.ttLib import TTCollection

    try:
        fonts = TTCollection(path, lazy=True).fonts
        best = None
        for i, f in enumerate(fonts):
            if (f["name"].getBestFamilyName() or "") == "PingFang SC":
                w = f["OS/2"].usWeightClass
                if best is None or abs(w - (600 if bold else 400)) < best[0]:
                    best = (abs(w - (600 if bold else 400)), i)
        return best[1] if best else None
    except Exception:
        return None


@functools.lru_cache(maxsize=1024)
def _fc_fallback(family: str, bold: bool, cp: int) -> Optional[tuple[str, int]]:
    """The font fontconfig offers for a character ``family`` lacks (what libass uses with fontconfig)."""
    if not _fc("fc-match"):
        return None
    pattern = f"{fc_escape(family)}:charset={cp:x}:weight={'bold' if bold else 'regular'}"
    out = subprocess.run(["fc-match", "-f", "%{file}|%{index}", pattern], capture_output=True, text=True)
    if out.returncode != 0 or "|" not in out.stdout:
        return None
    file, idx = out.stdout.rsplit("|", 1)
    if "lastresort" in Path(file).name.lower():  # placeholder glyphs for everything: not a real fallback
        return None
    return file, _first_int(idx or "0")


class Measurer:
    """Advance widths of text as libass will lay it out, in pixels.

    Characters the font has no glyph for are drawn by libass with a fallback font (at the same
    ASS size, i.e. with that font's own em scale); they are measured with the font libass is
    likely to pick: on macOS PingFang for Chinese characters (CoreText), otherwise the font
    fontconfig matches for the family with that character.  Text is measured as it will be
    written (escape_text: backslashes and braces full-width)."""

    def __init__(self, family: str, bold: bool, size: float, *, _face: Optional[tuple[str, int]] = None):
        from fontTools.ttLib import TTFont
        from PIL import ImageFont

        self.family = family or default_family()
        self.bold = bold
        path, index = _face or resolve(self.family, bold)
        self._face = (path, index)
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
        self._fallbacks: dict[tuple[str, int], "Measurer"] = {}

    def _fallback(self, ch: str) -> Optional["Measurer"]:
        cp = ord(ch)
        for m in self._fallbacks.values():  # a font already used for this text's script first
            if cp in _charmap(*m._face):
                return m
        cands: list[tuple[str, int]] = []
        mac = _mac_han_fallback() if _is_han(cp) else None
        if mac is not None:
            idx = _pingfang_sc(mac, self.bold)
            if idx is not None:
                cands.append((mac, idx))
        fc = _fc_fallback(self.family, self.bold, cp)
        if fc is not None:
            cands.append(fc)
        for face in cands:
            if face != self._face and cp in _charmap(*face):
                try:
                    m = Measurer(self.family, self.bold, self.size, _face=face)
                except Exception:
                    continue
                self._fallbacks[face] = m
                return m
        return None

    def width(self, text: str) -> float:
        if not text:
            return 0.0
        from .ass import escape_text

        text = escape_text(text)
        have = _charmap(*self._face)
        if not have or all(ord(c) in have or c.isspace() for c in text):
            return self._font.getlength(text) * self._k
        # runs of characters the font has / lacks; a lacking run is measured with its fallback font
        total, run, run_font = 0.0, "", self
        for c in text:
            font = self if (ord(c) in have or c.isspace()) else (self._fallback(c) or self)
            if font is not run_font and run:
                total += run_font._font.getlength(run) * run_font._k
                run = ""
            run_font = font
            run += c
        if run:
            total += run_font._font.getlength(run) * run_font._k
        return total
