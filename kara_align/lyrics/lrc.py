"""LRC parsing and formatting.

Every time tag produces its own line *instance*: a chorus written once with
three tags, or written three times, always yields three instances.  Nothing
is de-duplicated by text.

``[offset:N]`` follows the common LRC convention: a positive value makes the
lyrics appear *earlier*.  It is normalized exactly once, at import time, into
``embedded_shift_ms = -N`` which is *added* to raw line starts.  The raw tag
value is always kept so users can verify the interpretation.  A value that is
not a finite number of at most :data:`MAX_OFFSET_MS` is ignored with a warning.

Time tags: ``[mm:ss]``, ``[mm:ss.x]`` … ``[mm:ss.xxxxxx]`` (fractions longer than
milliseconds are rounded to ms) and ``[mm:ss:xx]``.  Seconds of 60 or more
(``[00:75.00]``) are read as that many seconds with a warning.

Enhanced LRC word tags (``<mm:ss.xx>`` inside a line) are removed from the
text; the aligner computes its own unit times, so they are not used as unit
times.  Two things are kept: a line written with word tags only (no ``[..]``
line tag) starts at its first word tag, and a trailing word tag with no text
after it (``…み<00:01.60>``) is the line's end (``imported_end_ms``).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

# [mm:ss], [mm:ss.x], [mm:ss.xx], [mm:ss.xxx] (up to 6 fraction digits), [mm:ss:xx]
_TIME_TAG = re.compile(r"\[(\d{1,4}):(\d{1,2})(?:[.:](\d{1,6}))?\]")
_WORD_TAG = re.compile(r"<(\d{1,4}):(\d{1,2})(?:[.:](\d{1,6}))?>")
_META_TAG = re.compile(r"^\[([A-Za-z#][A-Za-z0-9_#-]*)\s*:(.*)\]\s*$")
_LEADING_TAGS = re.compile(r"^(?:\s*\[\d{1,4}:\d{1,2}(?:[.:]\d{1,6})?\])+")
# an [offset] beyond this is not a real offset (and ±inf / nan are not numbers)
MAX_OFFSET_MS = 3_600_000

OFFSET_NOTE = (
    "[offset] 按 LRC 惯例解释：正值表示歌词提前显示。"
    "导入时只规范化一次：embedded_shift_ms = -offset，加到每行原始句首上。"
)


def _tag_ms(mm: str, ss: str, frac: Optional[str]) -> int:
    ms = int(mm) * 60_000 + int(ss) * 1000
    if frac:
        # "5" -> 500 ms, "05" -> 50 ms, "005" -> 5 ms, "0055" -> 6 ms (rounded half up)
        if len(frac) <= 3:
            ms += int(frac) * (10 ** (3 - len(frac)))
        else:
            ms += (int(frac) * 1000 + 10 ** len(frac) // 2) // 10 ** len(frac)
    return ms


@dataclass
class WordTag:
    time_ms: int
    text: str


@dataclass
class LrcEntry:
    """One timed line instance."""

    time_ms: int
    text: str
    raw_index: int
    tag_index: int = 0
    raw_text: str = ""
    word_tags: list[WordTag] = field(default_factory=list)
    # end of the line when a trailing word tag marks it (enhanced LRC), else None
    end_ms: Optional[int] = None


@dataclass
class UntimedLine:
    raw_index: int
    text: str


@dataclass
class ParsedLrc:
    entries: list[LrcEntry] = field(default_factory=list)  # sorted by time (stable)
    untimed: list[UntimedLine] = field(default_factory=list)
    meta: dict[str, str] = field(default_factory=dict)
    offset_raw: Optional[str] = None
    offset_ms: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def embedded_shift_ms(self) -> int:
        return -self.offset_ms


def _strip_word_tags(text: str) -> tuple[str, list[WordTag]]:
    tags: list[WordTag] = []
    matches = list(_WORD_TAG.finditer(text))
    if not matches:
        return text, tags
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        tags.append(WordTag(_tag_ms(*m.groups()), text[m.end():end]))
    return _WORD_TAG.sub("", text), tags


def _word_end(words: list[WordTag], start_ms: int) -> Optional[int]:
    """A trailing word tag with no text after it marks where the line ends."""
    if len(words) >= 2 and not words[-1].text.strip() and words[-1].time_ms > start_ms:
        return words[-1].time_ms
    return None


def _parse_offset(value: str, out: ParsedLrc) -> None:
    out.offset_raw = value
    try:
        v = float(value)
    except ValueError:
        v = None
    if v is None or not math.isfinite(v) or abs(v) > MAX_OFFSET_MS:
        out.warnings.append(f"无效的 [offset:{value}]，已忽略")
        out.offset_ms = 0
        return
    out.offset_ms = int(v)


def parse_lrc(text: str, keep_word_tags: bool = True) -> ParsedLrc:
    """Parse LRC text; see module docstring for the semantics."""
    out = ParsedLrc()
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    timed: list[LrcEntry] = []
    big_seconds = 0
    for idx, raw in enumerate(text.split("\n")):
        line = raw.strip()
        if not line:
            continue
        lead = _LEADING_TAGS.match(line)
        if lead:
            tags = _TIME_TAG.findall(lead.group(0))
            big_seconds += sum(1 for t in tags if int(t[1]) >= 60)
            body = line[lead.end():].strip()
            body, words = _strip_word_tags(body)
            body = body.strip()
            for ti, tag in enumerate(tags):
                start = _tag_ms(*tag)
                timed.append(LrcEntry(start, body, idx, ti, raw,
                                      list(words) if keep_word_tags else [], _word_end(words, start)))
            continue
        meta = _META_TAG.match(line)
        if meta:
            key, value = meta.group(1).lower(), meta.group(2).strip()
            out.meta[key] = value
            if key == "offset":
                _parse_offset(value, out)
            continue
        body, words = _strip_word_tags(line)
        if words and _WORD_TAG.match(line):
            # enhanced LRC written without a line tag: the line starts at its first word tag
            start = words[0].time_ms
            timed.append(LrcEntry(start, body.strip(), idx, 0, raw, list(words) if keep_word_tags else [],
                                  _word_end(words, start)))
            continue
        out.untimed.append(UntimedLine(idx, body.strip()))
    if big_seconds:
        out.warnings.append(f"{big_seconds} 个时间标签的秒数不小于 60（如 [00:75.00]），已按实际秒数换算（= 01:15.00）；"
                            "请确认这些行的时间")
    # stable sort: equal times keep raw order
    out.entries = sorted(timed, key=lambda e: e.time_ms)
    return out


def has_valid_times(parsed: ParsedLrc) -> bool:
    """True when at least one non-blank line carries a time tag."""
    return any(e.text for e in parsed.entries)


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------


def format_lrc_time(ms: int, precision: str = "cs") -> str:
    """Format a time tag body.

    ``precision="cs"`` writes ``mm:ss.xx``; ms are rounded half-up to the
    nearest centisecond (``12345 -> 00:12.35``, ``12344 -> 00:12.34``).
    ``precision="ms"`` writes ``mm:ss.xxx`` exactly.  Negative times are an
    error; callers must fix them rather than clamp silently.
    """
    if ms is None or ms < 0:
        raise ValueError(f"无法写入负的或缺失的 LRC 时间：{ms!r}")
    if precision == "ms":
        m, rest = divmod(int(ms), 60_000)
        s, frac = divmod(rest, 1000)
        return f"{m:02d}:{s:02d}.{frac:03d}"
    if precision != "cs":
        raise ValueError(f"未知的时间精度 {precision!r}")
    cs = (int(ms) + 5) // 10
    m, rest = divmod(cs, 6000)
    s, frac = divmod(rest, 100)
    return f"{m:02d}:{s:02d}.{frac:02d}"


def format_lrc_tag(ms: int, precision: str = "cs", word: bool = False) -> str:
    body = format_lrc_time(ms, precision)
    return f"<{body}>" if word else f"[{body}]"


def format_lrc(
    lines: Iterable[tuple[Optional[int], str]],
    meta: Optional[dict[str, str]] = None,
    precision: str = "cs",
) -> str:
    """Write LRC from ``(start_ms, text)`` pairs.

    Lines without a time are written without a tag (never given a fake time).
    No ``[offset]`` tag is written: callers pass effective absolute times.
    """
    out: list[str] = []
    for key, value in (meta or {}).items():
        if key.lower() == "offset":
            continue
        out.append(f"[{key}:{value}]")
    for ms, text in lines:
        out.append(text if ms is None else f"{format_lrc_tag(ms, precision)}{text}")
    return "\n".join(out) + "\n"
