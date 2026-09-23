"""Lyrics input: text/LRC parsing, track pairing and platform lookups."""

from .lrc import (
    LrcEntry,
    ParsedLrc,
    format_lrc,
    format_lrc_tag,
    format_lrc_time,
    has_valid_times,
    parse_lrc,
)
from .pairing import PairPreview, apply_pairs, merge_lines, pair_track, split_line
from .parse import (
    LyricsFormatError,
    LyricsModeError,
    ParseResult,
    detect_format,
    detect_language,
    parse_lyrics_text,
)

__all__ = [
    "LrcEntry", "LyricsFormatError", "LyricsModeError", "PairPreview", "ParseResult", "ParsedLrc",
    "apply_pairs", "detect_format", "detect_language", "format_lrc", "format_lrc_tag", "format_lrc_time",
    "has_valid_times", "merge_lines", "pair_track", "parse_lrc", "parse_lyrics_text", "split_line",
]
