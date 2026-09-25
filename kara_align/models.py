"""Versioned public data model.

Three independent objects are kept apart (design §3):

* ``LyricsDoc``      – editable lyrics document (Line -> Segment -> Unit)
* ``AudioAsset``     – reusable audio asset (original / vocals / instrumental / mix)
* ``AlignmentResult``– traceable alignment result

All public times are **integer milliseconds counted from the start of the
original audio**, intervals are half open ``[start_ms, end_ms)``.  Internal
code keeps sample / frame coordinates and only rounds when producing these
objects (see :mod:`kara_align.timebase`).

Missing or failed times are ``None`` accompanied by a ``reason``; nothing in
this module ever invents evenly distributed fake times.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1

# format identifiers written into every top-level JSON document
FMT_PROJECT = "kara-align/project"
FMT_ALIGNMENT = "kara-align/alignment"
FMT_PREPARED = "kara-align/prepared"
FMT_READING_PATCH = "kara-align/reading-patch"


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stable_hash(obj: Any, n: int = 16) -> str:
    """Deterministic content hash of a JSON-able object."""
    data = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:n]


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=False)


# ---------------------------------------------------------------------------
# Lyrics document
# ---------------------------------------------------------------------------

Lang = Literal["ja", "zh", "en", "other"]
ReadingSource = Literal["rule", "manual", "ai", "import", "none"]
LineKind = Literal["lyric", "translation", "romanization", "meta", "blank"]
SourceOrigin = Literal["paste", "upload", "netease", "qq", "project", "manual"]


class Unit(_Base):
    """A pronunciation unit that should receive a time interval.

    ``surface`` is the part of the original text the unit maps to when that
    mapping is 1:1 (e.g. kana ``と``); for multi-unit kanji readings
    (``君`` -> ``き`` / ``み``) the unit surface is empty and the owning
    segment holds the surface.
    """

    id: str = Field(default_factory=lambda: new_id("u"))
    reading: str  # kana for ja, pinyin for zh, word for en
    surface: str = ""
    # extra phonetic flags kept from the reading: sokuon / hatsuon / long vowel
    flags: list[str] = Field(default_factory=list)


class Segment(_Base):
    id: str = Field(default_factory=lambda: new_id("s"))
    surface: str
    reading: Optional[str] = None
    lang: Lang = "ja"
    units: list[Unit] = Field(default_factory=list)
    reading_source: ReadingSource = "none"
    confirmed: bool = False  # manually confirmed; AI / rules must not overwrite
    uncertain: bool = False
    candidates: list[str] = Field(default_factory=list)  # alternative readings
    note: str = ""


class LineSource(_Base):
    """Provenance of a line instance."""

    origin: SourceOrigin = "paste"
    source_id: Optional[str] = None  # SourceSnapshot.id
    raw_index: Optional[int] = None  # line index inside the raw text
    raw_text: Optional[str] = None
    tag_index: int = 0  # which of multiple time tags produced this instance
    merged_from: list[str] = Field(default_factory=list)
    split_from: Optional[str] = None


class LineAnchor(_Base):
    """A manually locked absolute anchor (original audio time).

    It does not move with later global shifts.
    """

    abs_ms: int
    hard: bool = True
    tolerance_ms: int = 80
    note: str = ""


class Line(_Base):
    id: str = Field(default_factory=lambda: new_id("L"))
    text: str
    kind: LineKind = "lyric"
    sing: bool = True  # participates in alignment
    segments: list[Segment] = Field(default_factory=list)
    # raw LRC line start as written in the file (before embedded offset)
    imported_start_ms: Optional[int] = None
    imported_end_ms: Optional[int] = None  # only when explicitly given (e.g. next tag / enhanced LRC)
    anchor: Optional[LineAnchor] = None
    translation: Optional[str] = None
    romanization: Optional[str] = None
    voice: str = "main"  # independent lyric stream id for real simultaneous parts
    confirmed: bool = False
    source: LineSource = Field(default_factory=LineSource)

    def units(self) -> list[Unit]:
        return [u for s in self.segments for u in s.units]


class LyricsMeta(_Base):
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    duration_ms: Optional[int] = None
    extra: dict[str, str] = Field(default_factory=dict)


class LyricsDoc(_Base):
    format: str = "kara-align/lyrics"
    version: int = SCHEMA_VERSION
    language: Lang = "ja"
    meta: LyricsMeta = Field(default_factory=LyricsMeta)
    lines: list[Line] = Field(default_factory=list)
    # value of the ``[offset:...]`` tag exactly as written, if any
    embedded_offset_raw: Optional[str] = None
    # normalized shift in ms that must be ADDED to imported_start_ms
    # (LRC convention: positive [offset] shows lyrics earlier -> shift = -offset)
    embedded_shift_ms: int = 0
    embedded_offset_note: str = ""

    def line(self, line_id: str) -> Line:
        for ln in self.lines:
            if ln.id == line_id:
                return ln
        raise KeyError(line_id)

    def sung_lines(self) -> list[Line]:
        return [ln for ln in self.lines if ln.sing and ln.kind == "lyric"]

    def text_revision(self) -> str:
        """Identity of the lyrics *text* (ids + texts + sing flags)."""
        return stable_hash([(ln.id, ln.text, ln.sing, ln.kind) for ln in self.lines])

    def reading_revision(self) -> str:
        """Identity of the text plus all readings / unit groupings."""
        return stable_hash(
            [
                (ln.id, ln.text, ln.sing, ln.kind,
                 [(s.surface, s.reading, [(u.id, u.reading) for u in s.units]) for s in ln.segments])
                for ln in self.lines
            ]
        )


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


class CalibrationCheck(_Base):
    """A mark on another line used to verify a single global shift."""

    line_id: str
    marked_ms: int
    residual_ms: int  # marked - effective (with the current shift)


class Calibration(_Base):
    """LRC first-onset calibration.

    base_i      = imported_start_i + embedded_shift_ms
    effective_i = base_i + user_shift_ms
    marking line k at marked_ms sets user_shift = marked_ms - base_k (never accumulated)
    """

    user_shift_ms: int = 0
    confirmed: bool = False  # includes an explicit "zero offset is correct"
    reference_line_id: Optional[str] = None
    marked_ms: Optional[int] = None
    checks: list[CalibrationCheck] = Field(default_factory=list)
    history: list[dict[str, Any]] = Field(default_factory=list)  # for undo / audit


# ---------------------------------------------------------------------------
# Audio assets
# ---------------------------------------------------------------------------

AudioRole = Literal["original", "vocals", "instrumental", "mix"]


class AudioSource(_Base):
    kind: Literal["upload", "separation", "import", "mix"] = "upload"
    filename: Optional[str] = None
    model: Optional[str] = None  # separation model file / id
    model_version: Optional[str] = None
    config: dict[str, Any] = Field(default_factory=dict)
    parent_sha256: Optional[str] = None
    notes: list[str] = Field(default_factory=list)


class AudioAsset(_Base):
    id: str = Field(default_factory=lambda: new_id("a"))
    role: AudioRole
    sha256: str
    path: Optional[str] = None  # relative to the project directory, or None when missing
    duration_ms: int
    sample_rate: int
    channels: int
    num_samples: int
    # sample 0 of this file corresponds to this time on the original timeline
    origin_offset_samples: int = 0
    sync_checked: bool = False
    sync_report: Optional[dict[str, Any]] = None
    source: AudioSource = Field(default_factory=AudioSource)


# ---------------------------------------------------------------------------
# Alignment configuration and results
# ---------------------------------------------------------------------------

AlignMode = Literal["plain", "lrc"]


class DecodeConfig(_Base):
    left_margin_ms: int = 1500
    right_margin_ms: int = 1500
    soft_sigma_ms: int = 400
    soft_lambda: float = 4.0
    huber_delta: float = 1.0
    hard_tolerance_ms: int = 80
    joint_context_lines: int = 1
    tight_gap_ms: int = 400  # neighbour lines closer than this are aligned jointly
    band_frames: Optional[int] = None
    # pauses *inside* a line cost this much per second, so a line cannot stretch
    # across an interlude for free (between-line pauses stay free)
    line_gap_cost: float = 1.0
    # with a vocal stem: extra cost per second of an in-line pause where the stem
    # is silent, and per second of singing placed where the stem is silent
    rest_gap_cost: float = 4.0
    rest_token_cost: float = 25.0
    # an LRC end marker (a timed blank line) bounds the search this far after it
    end_marker_margin_ms: int = 6000


class CheckConfig(_Base):
    min_unit_ms: int = 40
    max_unit_ms: int = 6000
    anchor_deviation_ms: int = 700
    edge_crowd_ms: int = 120
    stability_tolerance_ms: int = 150
    min_coverage: float = 0.999
    max_line_gap_ms: int = 4000  # a longer pause between two units of one line is suspicious


class RetryConfig(_Base):
    enabled: bool = True
    max_candidates_per_line: int = 4
    max_total_candidates: int = 60


class TailConfig(_Base):
    strategy: Literal["off", "trim", "energy"] = "off"
    max_extend_ms: int = 800
    max_trim_ms: int = 600
    energy_floor_db: float = -35.0


class AlignConfig(_Base):
    backend: str = "mms-ja"
    model_id: Optional[str] = None  # override weights (e.g. HF repo id)
    model_revision: Optional[str] = None
    device: str = "auto"
    audio_role: Literal["original", "vocals"] = "original"
    chunk_s: float = 20.0
    context_s: float = 3.0
    decode: DecodeConfig = Field(default_factory=DecodeConfig)
    checks: CheckConfig = Field(default_factory=CheckConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    tail: TailConfig = Field(default_factory=TailConfig)


class BackendInfo(_Base):
    name: str
    model_id: str
    model_revision: Optional[str] = None
    license: Optional[str] = None
    profile: str  # transliteration profile used to build tokens
    sample_rate: int
    frame_hop_samples: Optional[int] = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ManualEdit(_Base):
    start_ms: Optional[int]
    end_ms: Optional[int]
    locked: bool = True
    at: str = Field(default_factory=utcnow)
    note: str = ""


class TailAdjustment(_Base):
    original_end_ms: Optional[int]
    new_end_ms: Optional[int]
    method: str
    reason: str


UnitStatus = Literal["ok", "failed", "unaligned", "skipped"]


class UnitTiming(_Base):
    unit_id: str
    line_id: str
    segment_id: str
    reading: str
    # final times (after tail correction and manual overrides)
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None
    status: UnitStatus = "ok"
    reason: Optional[str] = None
    # raw model prediction, never overwritten
    model_start_ms: Optional[int] = None
    model_end_ms: Optional[int] = None
    tail: Optional[TailAdjustment] = None
    manual: Optional[ManualEdit] = None
    manual_history: list[ManualEdit] = Field(default_factory=list)
    # normalized acoustic score (mean logp per frame); NOT a probability
    acoustic_score: Optional[float] = None
    flags: list[str] = Field(default_factory=list)

    @property
    def locked(self) -> bool:
        return bool(self.manual and self.manual.locked)


class Issue(_Base):
    code: str
    severity: Literal["info", "warning", "error"] = "warning"
    line_id: Optional[str] = None
    unit_id: Optional[str] = None
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class LineTiming(_Base):
    line_id: str
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None
    status: UnitStatus = "ok"
    reason: Optional[str] = None
    anchor_ms: Optional[int] = None  # effective anchor used (lrc mode)
    anchor_kind: Optional[Literal["soft", "hard"]] = None
    anchor_residual_ms: Optional[int] = None
    window_ms: Optional[tuple[int, int]] = None
    context_line_ids: list[str] = Field(default_factory=list)
    audio_role: Optional[str] = None
    candidate: Optional[str] = None  # which retry candidate produced the committed result
    flags: list[str] = Field(default_factory=list)


class Candidate(_Base):
    """Alternative result for a line kept for manual listening (not committed)."""

    id: str = Field(default_factory=lambda: new_id("c"))
    line_id: str
    label: str
    units: list[UnitTiming]
    summary: dict[str, Any] = Field(default_factory=dict)


class InputSnapshot(_Base):
    mode: AlignMode
    lyrics_text_revision: str
    lyrics_reading_revision: str
    calibration_hash: Optional[str] = None
    audio_asset_id: str
    audio_sha256: str
    audio_role: str
    line_ids: list[str]
    config_hash: str


class Coverage(_Base):
    full: bool = True
    line_ids: list[str] = Field(default_factory=list)
    from_ms: Optional[int] = None
    to_ms: Optional[int] = None


class AlignmentResult(_Base):
    format: str = FMT_ALIGNMENT
    version: int = SCHEMA_VERSION
    id: str = Field(default_factory=lambda: new_id("r"))
    created: str = Field(default_factory=utcnow)
    time_unit: str = "ms, integer, from original audio start, [start, end)"
    mode: AlignMode
    backend: BackendInfo
    config: AlignConfig
    snapshot: InputSnapshot
    coverage: Coverage = Field(default_factory=Coverage)
    lines: list[LineTiming] = Field(default_factory=list)
    units: list[UnitTiming] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    stale: bool = False
    stale_reason: Optional[str] = None
    parent_result_id: Optional[str] = None  # for local reruns
    stats: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


class SourceSnapshot(_Base):
    id: str = Field(default_factory=lambda: new_id("src"))
    origin: SourceOrigin
    kind: Literal["lyrics", "lrc", "translation", "romanization", "readings", "project"] = "lyrics"
    filename: Optional[str] = None
    url: Optional[str] = None
    platform_song_id: Optional[str] = None
    text: str
    sha256: str = ""
    fetched_meta: dict[str, Any] = Field(default_factory=dict)
    created: str = Field(default_factory=utcnow)


class AiRoundtrip(_Base):
    id: str = Field(default_factory=lambda: new_id("ai"))
    created: str = Field(default_factory=utcnow)
    snapshot_id: str  # lyrics snapshot identifier embedded in the prompt
    text_revision: str
    reading_revision: str
    line_ids: list[str]
    prompt: str
    response_raw: Optional[str] = None
    status: Literal["prompted", "validated", "applied", "rejected"] = "prompted"
    report: dict[str, Any] = Field(default_factory=dict)
    applied_at: Optional[str] = None


class MixSettings(_Base):
    vocal_keep_pct: float = 100.0
    instrumental_pct: float = 100.0
    master: float = 1.0
    limiter: Literal["none", "normalize_peak"] = "normalize_peak"


class VideoAsset(_Base):
    """A video uploaded as the original; its audio track became the original asset."""

    id: str = Field(default_factory=lambda: new_id("v"))
    sha256: str
    path: Optional[str] = None  # relative to the project directory, None when missing
    filename: Optional[str] = None
    container: str  # file extension, e.g. ".mp4"
    duration_ms: int
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    # original audio stream start relative to the file start (restored when muxing)
    audio_offset_s: float = 0.0
    # sha256 of the audio extracted from it (= the original asset it produced)
    audio_sha256: str


# ---------------------------------------------------------------------------
# Karaoke subtitle style (ASS). Pixel values are defined for a 1080p frame and
# scaled to the actual resolution.
# ---------------------------------------------------------------------------


class KaraokeText(_Base):
    font: str = ""  # font family; "" = best available Japanese font
    size: int = 88
    bold: bool = True
    color_unsung: str = "#FFFFFF"
    color_sung: str = "#2F80ED"
    outline_color: str = "#0B1F3A"
    outline: float = 4.5
    shadow: float = 2.0
    shadow_color: str = "#000000"
    shadow_opacity: int = 45  # %


class KaraokeRuby(_Base):
    enabled: bool = True
    script: Literal["hiragana", "katakana", "romaji"] = "hiragana"
    target: Literal["kanji", "all"] = "kanji"
    size_pct: int = 45  # of the lyric size
    gap: int = 2  # px between ruby and lyric
    fit: Literal["widen", "overflow"] = "widen"
    follow_colors: bool = True
    font: str = ""  # "" = same as the lyric font
    color_unsung: str = "#FFFFFF"
    color_sung: str = "#2F80ED"
    outline_color: str = "#0B1F3A"
    outline: float = 3.0


class KaraokeLayout(_Base):
    position: Literal["bottom", "top"] = "bottom"
    lines: int = Field(default=2, ge=1, le=3)
    arrangement: Literal["alternate", "center"] = "alternate"
    margin_v: int = 70  # px from the top / bottom edge
    line_spacing: int = 26  # px between stacked lines
    margin_h: int = 140  # px left and right (the widest a line may get)
    # alternating lines: extra inset toward the centre for lines that fit, so two
    # short lines are not pinned to opposite edges (long lines use the full width)
    alternate_indent: int = 240
    shrink_long_lines: bool = True  # scale down lines wider than the frame
    show_translation: bool = False
    # opposite: one line at the other edge of the frame (top when lyrics are at the
    # bottom); block: one line just outside the lyric block; line: under each lyric line
    translation_position: Literal["opposite", "block", "line"] = "opposite"
    translation_size_pct: int = Field(default=60, ge=20, le=100)


class KaraokeTiming(_Base):
    lead_in_ms: int = 1000  # line appears at least this long before its first syllable
    hold_ms: int = 500  # and stays after its last one
    # show the next line as soon as its slot is free (at most early_max_ms ahead)
    early_show: bool = True
    early_max_ms: int = 4000
    highlight: Literal["sweep", "instant"] = "sweep"  # \kf or \k
    # show (and highlight) the lyrics this much before they are sung; 0 = off.
    # Applies to every subtitle / LRC export, never to the alignment data itself.
    advance_ms: int = Field(default=0, ge=0, le=2000)


class KaraokeOutput(_Base):
    # "reduced vocals" audio for burn-in: vocals at this %, instrumental at 100 %
    # (independent of the Export page's mix, which comes later in the flow)
    vocal_keep_pct: float = Field(default=20.0, ge=0.0, le=100.0)


class KaraokeStyle(_Base):
    version: int = 1
    preset: str = "classic"
    layout: KaraokeLayout = Field(default_factory=KaraokeLayout)
    text: KaraokeText = Field(default_factory=KaraokeText)
    ruby: KaraokeRuby = Field(default_factory=KaraokeRuby)
    timing: KaraokeTiming = Field(default_factory=KaraokeTiming)
    output: KaraokeOutput = Field(default_factory=KaraokeOutput)


class Project(_Base):
    format: str = FMT_PROJECT
    version: int = SCHEMA_VERSION
    id: str = Field(default_factory=lambda: new_id("p"))
    name: str = "untitled"
    created: str = Field(default_factory=utcnow)
    updated: str = Field(default_factory=utcnow)
    mode: AlignMode = "plain"
    lyrics: LyricsDoc = Field(default_factory=LyricsDoc)
    sources: list[SourceSnapshot] = Field(default_factory=list)
    calibration: Calibration = Field(default_factory=Calibration)
    ai_roundtrips: list[AiRoundtrip] = Field(default_factory=list)
    config: AlignConfig = Field(default_factory=AlignConfig)
    audio: list[AudioAsset] = Field(default_factory=list)
    results: list[AlignmentResult] = Field(default_factory=list)
    active_result_id: Optional[str] = None
    mix: MixSettings = Field(default_factory=MixSettings)
    video: Optional[VideoAsset] = None
    karaoke: KaraokeStyle = Field(default_factory=KaraokeStyle)

    def asset(self, role: str) -> Optional[AudioAsset]:
        for a in self.audio:
            if a.role == role:
                return a
        return None

    def result(self, result_id: Optional[str] = None) -> Optional[AlignmentResult]:
        rid = result_id or self.active_result_id
        for r in self.results:
            if r.id == rid:
                return r
        return None
