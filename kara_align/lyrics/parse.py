"""Unified text parsing for pasted or uploaded lyrics.

Pasting and uploading share this code path; the only difference is the
provenance recorded in :class:`~kara_align.models.SourceSnapshot` and
:class:`~kara_align.models.LineSource`.  Line ids are derived from the output
position (``L0001``...), so parsing identical text twice gives identical ids
and an identical ``text_revision``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Literal, Optional

from ..models import (
    FMT_ALIGNMENT,
    FMT_PREPARED,
    FMT_PROJECT,
    FMT_READING_PATCH,
    Line,
    LineSource,
    LyricsDoc,
    LyricsMeta,
    SourceSnapshot,
)
from .lrc import OFFSET_NOTE, ParsedLrc, has_valid_times, parse_lrc

DetectedFormat = Literal[
    "lrc", "plain", "json-project", "json-prepared", "json-alignment", "json-reading-patch", "unknown"
]

_TIMED_LINE = re.compile(r"^\s*\[\d{1,4}:\d{1,2}(?:[.:]\d{1,3})?\]")
_KANA = re.compile(r"[぀-ヿㇰ-ㇿｦ-ﾟ]")
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_LATIN = re.compile(r"[A-Za-z]")

# credit lines: a short label containing a credit keyword, then a colon
_CREDIT_LABEL = re.compile(r"^\s*([^:：]{1,16}?)\s*[:：]")
_CREDIT_KEYWORDS = re.compile(
    r"作词|作曲|编曲|作詞|編曲|词|詞|曲|制作|製作|监制|監製|混音|缩混|混缩|和声|和聲|录音|錄音|母带|吉他|贝斯|貝斯|"
    r"鼓|弦乐|弦樂|键盘|鍵盤|出品|发行|發行|策划|统筹|配唱|人声|OP|SP|ISRC|"
    r"lyric|composer|compos|music|arrang|written|produc|mix|master|vocal|guitar|bass|drum|record",
    re.IGNORECASE,
)


class LyricsModeError(ValueError):
    """Input is unusable in the requested mode (never silently downgraded)."""


class LyricsFormatError(ValueError):
    """Input is not lyrics text (e.g. a project JSON) or cannot be parsed."""


@dataclass
class ParseResult:
    doc: LyricsDoc
    warnings: list[str] = field(default_factory=list)
    detected: DetectedFormat = "plain"
    snapshot: Optional[SourceSnapshot] = None


# byte-order marks and zero-width characters (music platforms sometimes leave them in lyrics)
_INVISIBLE = dict.fromkeys(map(ord, "\ufeff\u200b\u200c\u200d\u2060"), None)


def normalize_text(text: str) -> str:
    return text.translate(_INVISIBLE).replace("\r\n", "\n").replace("\r", "\n")


def detect_format(text: str, filename: Optional[str] = None) -> DetectedFormat:
    t = normalize_text(text).strip()
    if not t:
        return "unknown"
    if t[0] in "{[" and not _TIMED_LINE.match(t) and not re.match(r"^\[[A-Za-z#]+:", t):
        try:
            data = json.loads(t)
        except ValueError:
            data = None
        if data is not None:
            fmt = data.get("format", "") if isinstance(data, dict) else ""
            for prefix, name in (
                (FMT_PROJECT, "json-project"),
                (FMT_PREPARED, "json-prepared"),
                (FMT_ALIGNMENT, "json-alignment"),
                (FMT_READING_PATCH, "json-reading-patch"),
            ):
                if isinstance(fmt, str) and fmt.startswith(prefix):
                    return name  # type: ignore[return-value]
            return "unknown"
    if any(_TIMED_LINE.match(ln) for ln in t.split("\n")):
        return "lrc"
    if filename and filename.lower().endswith(".lrc"):
        # an .lrc without any time tag is still just text
        return "plain"
    return "plain"


def detect_language(texts: list[str]) -> str:
    joined = "".join(texts)
    if _KANA.search(joined):
        return "ja"
    if _HAN.search(joined):
        return "zh"
    if _LATIN.search(joined):
        return "en"
    return "other"


def is_credit_line(text: str) -> bool:
    m = _CREDIT_LABEL.match(text)
    return bool(m and _CREDIT_KEYWORDS.search(m.group(1)))


def _meta_from_tags(tags: dict[str, str]) -> LyricsMeta:
    meta = LyricsMeta(title=tags.get("ti"), artist=tags.get("ar"), album=tags.get("al"))
    length = tags.get("length")
    if length:
        m = re.match(r"^(\d+):(\d{1,2})(?:[.:](\d{1,3}))?$", length.strip())
        if m:
            frac = m.group(3) or ""
            meta.duration_ms = int(m.group(1)) * 60_000 + int(m.group(2)) * 1000 + (
                int(frac) * 10 ** (3 - len(frac)) if frac else 0
            )
    meta.extra = {k: v for k, v in tags.items() if k not in ("ti", "ar", "al", "offset")}
    return meta


def _make_line(i: int, text: str, source: LineSource, start: Optional[int]) -> Line:
    if not text:
        kind, sing = "blank", False
    elif is_credit_line(text):
        kind, sing = "meta", False
    else:
        kind, sing = "lyric", True
    return Line(id=f"L{i + 1:04d}", text=text, kind=kind, sing=sing,
                imported_start_ms=start, source=source)


def _split_translations(parsed: ParsedLrc) -> dict[tuple[int, int], str]:
    """Bilingual LRC (a Japanese line and its translation under the same time tag, as copied from
    many lyric sites): the line without kana at a shared time is the translation of the other.
    The translation entries are removed from ``parsed``; returns {(raw_index, tag_index) of the
    lyric line: translation}.  Needs at least two such pairs, so one odd line is left alone."""
    by_time: dict[int, list] = {}
    for e in parsed.entries:
        if e.text:
            by_time.setdefault(e.time_ms, []).append(e)
    pairs = []
    for group in by_time.values():
        if len(group) != 2:
            continue
        kana = [bool(_KANA.search(e.text)) for e in group]
        if kana.count(True) != 1:
            continue
        lyric, trans = (group[0], group[1]) if kana[0] else (group[1], group[0])
        if _HAN.search(trans.text) or _LATIN.search(trans.text):
            pairs.append((lyric, trans))
    if len(pairs) < 2:
        return {}
    drop = {id(t) for _, t in pairs}
    parsed.entries = [e for e in parsed.entries if id(e) not in drop]
    return {(lyric.raw_index, lyric.tag_index): trans.text for lyric, trans in pairs}


def _attach_translations(lines: list[Line], translations: dict[tuple[int, int], str], warnings: list[str]) -> None:
    if not translations:
        return
    n = 0
    for ln in lines:
        t = translations.get((ln.source.raw_index, ln.source.tag_index))
        if t is not None:
            ln.translation = t
            n += 1
    warnings.append(f"检测到 {n} 行翻译（与歌词同一时间标签、没有假名的行），已作为翻译，不参与演唱。")


def _lines_from_lrc(parsed: ParsedLrc, keep_times: bool, origin: str, source_id: str) -> list[Line]:
    """Build line instances in time order; untimed lines keep their raw position."""
    items: list[tuple[Optional[int], str, LineSource]] = []
    for e in parsed.entries:
        if not keep_times and not e.text:
            continue
        src = LineSource(origin=origin, source_id=source_id, raw_index=e.raw_index,
                         raw_text=e.raw_text, tag_index=e.tag_index)
        items.append((e.time_ms if keep_times else None, e.text, src))
    for u in parsed.untimed:
        if not u.text:
            continue
        src = LineSource(origin=origin, source_id=source_id, raw_index=u.raw_index, raw_text=u.text)
        # insert after the last first-tag instance written before it in the raw text
        pos = 0
        for j, (_, _, s) in enumerate(items):
            if s.raw_index is not None and s.raw_index < u.raw_index and s.tag_index == 0:
                pos = j + 1
        items.insert(pos, (None, u.text, src))
    return [_make_line(i, text, src, start) for i, (start, text, src) in enumerate(items)]


def parse_lyrics_text(
    text: str,
    *,
    mode: Literal["plain", "lrc"],
    origin: str = "paste",
    filename: Optional[str] = None,
    source_id: Optional[str] = None,
) -> ParseResult:
    """Parse lyrics (plain text or LRC) into a :class:`LyricsDoc`.

    * plain mode + LRC input: only the text is used, times are dropped and a
      warning says so explicitly;
    * lrc mode without any valid time: :class:`LyricsModeError`.
    """
    norm = normalize_text(text)
    detected = detect_format(norm, filename)
    if detected.startswith("json") or (detected == "unknown" and norm.strip()):
        raise LyricsFormatError(
            f"输入看起来是 {detected}，不是歌词文本；请通过对应的项目 / 补丁入口导入。"
        )
    if not norm.strip():
        raise LyricsFormatError("歌词文本为空。")

    snapshot = SourceSnapshot(
        origin=origin, kind="lrc" if detected == "lrc" else "lyrics", filename=filename, text=norm,
        sha256=hashlib.sha256(norm.encode("utf-8")).hexdigest(),
    )
    if source_id:
        snapshot.id = source_id
    warnings: list[str] = []
    doc = LyricsDoc()

    if detected == "lrc":
        parsed = parse_lrc(norm)
        warnings.extend(parsed.warnings)
        doc.meta = _meta_from_tags(parsed.meta)
        doc.embedded_offset_raw = parsed.offset_raw
        doc.embedded_shift_ms = parsed.embedded_shift_ms if parsed.offset_raw is not None else 0
        if parsed.offset_raw is not None:
            doc.embedded_offset_note = OFFSET_NOTE
        if mode == "lrc":
            if not has_valid_times(parsed):
                raise LyricsModeError(
                    "LRC 增强模式需要行时间，但输入中没有找到任何时间。"
                    "请提供带时间的 LRC，或切换到普通模式。"
                )
            translations = _split_translations(parsed)
            doc.lines = _lines_from_lrc(parsed, True, origin, snapshot.id)
            _attach_translations(doc.lines, translations, warnings)
            if parsed.untimed:
                n = sum(1 for u in parsed.untimed if u.text)
                if n:
                    warnings.append(f"{n} 行没有时间标签，已保留但不作为锚点。")
        else:
            translations = _split_translations(parsed)
            doc.lines = _lines_from_lrc(parsed, False, origin, snapshot.id)
            _attach_translations(doc.lines, translations, warnings)
            warnings.append(
                "普通模式：已忽略 LRC 时间标签，只使用歌词正文"
                "（不使用外部时间锚点）。"
            )
    else:
        if mode == "lrc":
            raise LyricsModeError(
                "LRC 增强模式需要带时间的歌词，但输入没有时间标签。"
                "请提供带时间的 LRC，或切换到普通模式。"
            )
        items = []
        for idx, raw in enumerate(norm.split("\n")):
            line = raw.strip()
            if line:
                items.append((idx, line))
        doc.lines = [
            _make_line(i, line, LineSource(origin=origin, source_id=snapshot.id, raw_index=idx, raw_text=raw), None)
            for i, (idx, line) in enumerate(items)
        ]

    n_meta = sum(1 for ln in doc.lines if ln.kind == "meta")
    if n_meta:
        warnings.append(f"{n_meta} 行作者 / 制作信息已排除在对齐之外（可重新加入）。")
    doc.language = detect_language([ln.text for ln in doc.lines if ln.kind == "lyric"])  # type: ignore[assignment]
    return ParseResult(doc=doc, warnings=warnings, detected=detected, snapshot=snapshot)
