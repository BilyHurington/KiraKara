"""Application service: every user operation, shared by the CLI and the WebUI.

Neither interface implements algorithms itself; they call these functions,
which operate on a :class:`ProjectHandle` (project + its directory).
"""

from __future__ import annotations

import copy
import shutil
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from .interfaces import CancelToken, Emission
from .models import (
    AiRoundtrip, AlignConfig, AlignmentResult, AudioAsset, AudioSource, Issue, LineAnchor, LyricsDoc,
    MixSettings, Project, SourceSnapshot, new_id, stable_hash,
)
from .project import store
from .project.store import ProjectError


class ServiceError(ValueError):
    """User-facing error (bad input, missing prerequisite)."""


# ---------------------------------------------------------------------------
# workspace / handles
# ---------------------------------------------------------------------------


@dataclass
class ProjectHandle:
    dir: Path
    project: Project
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    previews: dict[str, Any] = field(default_factory=dict, repr=False)

    def save(self) -> None:
        with self.lock:
            store.save_project(self.project, self.dir)

    @property
    def assets_dir(self) -> Path:
        return self.dir / "assets"


class Workspace:
    """Projects living under one root directory (WebUI); CLI uses open_dir()."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root else store.projects_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self._handles: dict[str, ProjectHandle] = {}
        self._lock = threading.Lock()

    def list(self) -> list[dict]:
        out = []
        for d in sorted(self.root.iterdir()):
            if (d / store.PROJECT_FILE).exists():
                try:
                    h = self.get(d.name)
                except ProjectError:
                    continue
                p = h.project
                out.append({"id": d.name, "name": p.name, "mode": p.mode, "updated": p.updated})
        return sorted(out, key=lambda x: x["updated"], reverse=True)

    def create(self, name: str, mode: str = "plain") -> ProjectHandle:
        p = Project(name=name or "untitled", mode=mode)  # type: ignore[arg-type]
        h = ProjectHandle(self.root / p.id, p)
        h.save()
        with self._lock:
            self._handles[p.id] = h
        return h

    def get(self, pid: str) -> ProjectHandle:
        if not pid or "/" in pid or pid.startswith("."):
            raise ProjectError("非法项目 ID")
        with self._lock:
            h = self._handles.get(pid)
            if h is None:
                d = self.root / pid
                h = ProjectHandle(d, store.load_project(d))
                # the directory name is the project's identity inside a workspace
                h.project.id = pid
                self._handles[pid] = h
            return h

    def import_file(self, path: Path, filename: str) -> ProjectHandle:
        tmp_id = new_id("p")
        dest = self.root / tmp_id
        if filename.endswith(".zip"):
            project = store.import_package(path, dest)
        else:
            project = store.parse_project_json(Path(path).read_text(encoding="utf-8"))
            store.save_project(project, dest)
        # the directory name is the project id
        final = self.root / project.id
        if final.exists():
            project.id = tmp_id
            final = dest
        else:
            dest.rename(final)
        h = ProjectHandle(final, store.load_project(final))
        h.project.id = final.name
        h.save()
        with self._lock:
            self._handles[h.project.id] = h
        return h


def open_dir(project_dir: Path) -> ProjectHandle:
    return ProjectHandle(Path(project_dir), store.load_project(project_dir))


def create_dir(project_dir: Path, name: str, mode: str = "plain") -> ProjectHandle:
    project_dir = Path(project_dir)
    if (project_dir / store.PROJECT_FILE).exists():
        raise ServiceError(f"{project_dir} 已经是项目目录")
    h = ProjectHandle(project_dir, Project(name=name, mode=mode))  # type: ignore[arg-type]
    h.save()
    return h


# ---------------------------------------------------------------------------
# view / staleness
# ---------------------------------------------------------------------------


def config_hash(config: AlignConfig) -> str:
    return stable_hash(config.model_dump(mode="json"))


def current_calibration_hash(p: Project) -> Optional[str]:
    from .align.calibration import calibration_hash

    return calibration_hash(p.lyrics, p.calibration) if p.mode == "lrc" else None


def staleness(p: Project, r: AlignmentResult) -> Optional[str]:
    """Reason why ``r`` no longer matches the current inputs (None = current)."""
    s = r.snapshot
    reasons = []
    if s.mode != p.mode:
        reasons.append("对齐模式已切换")
    if s.lyrics_text_revision != p.lyrics.text_revision():
        reasons.append("歌词文本已修改")
    elif s.lyrics_reading_revision != p.lyrics.reading_revision():
        reasons.append("读音或发音单元已修改")
    if p.mode == "lrc" and s.mode == "lrc" and s.calibration_hash != current_calibration_hash(p):
        reasons.append("校准或锚点已修改")
    asset = next((a for a in p.audio if a.id == s.audio_asset_id), None)
    if asset is None or asset.sha256 != s.audio_sha256:
        reasons.append("对齐所用音频已更换")
    return "；".join(reasons) or None


def refresh_staleness(p: Project) -> None:
    for r in p.results:
        reason = staleness(p, r)
        r.stale = reason is not None
        r.stale_reason = reason


def backend_languages(p: Project) -> list[str]:
    from .align.backends import BACKENDS

    return list(BACKENDS.get(p.config.backend, {}).get("languages", ["ja"]))


def project_view(h: ProjectHandle) -> dict:
    from .align.calibration import check_issues, effective_line_starts, validate_anchors
    from .reading.prepare import capability_warnings

    p = h.project
    with h.lock:
        refresh_staleness(p)
        original = p.asset("original")
        duration = original.duration_ms if original else None
        eff = effective_line_starts(p.lyrics, p.calibration)
        cal_issues: list[Issue] = []
        if p.mode == "lrc":
            cal_issues = validate_anchors(p.lyrics, p.calibration, duration) + check_issues(p.calibration)
        mode_notice = None
        if p.mode == "plain" and any(ln.imported_start_ms is not None for ln in p.lyrics.lines):
            mode_notice = "普通模式：歌词中的 LRC 时间不会作为锚点使用"
        if p.mode == "lrc" and not eff:
            mode_notice = "LRC 增强模式需要带行时间的歌词：请补充时间或切换到普通模式"
        from .project.edits import manual_count

        results = [{
            "id": r.id, "created": r.created, "mode": r.mode, "stale": r.stale, "stale_reason": r.stale_reason,
            "coverage": r.coverage.model_dump(mode="json"), "parent_result_id": r.parent_result_id,
            "n_units": len(r.units), "n_failed": sum(1 for u in r.units if u.status != "ok"),
            "n_issues": len(r.issues), "n_manual": manual_count(r),
            "audio_role": r.snapshot.audio_role, "backend": r.backend.name,
        } for r in p.results]
        audio = {a.role: {"asset_id": a.id, "available": a.path is not None, "duration_ms": a.duration_ms,
                          "sample_rate": a.sample_rate} for a in p.audio}
        try:
            cap = capability_warnings(p.lyrics, backend_languages(p))
        except Exception:
            cap = []
        return {
            "project": p.model_dump(mode="json"),
            "view": {
                "effective_starts": {k: {"ms": v[0], "kind": v[1]} for k, v in eff.items()},
                "calibration_issues": [i.model_dump(mode="json") for i in cal_issues],
                "mode_notice": mode_notice,
                "results": results,
                "capability_warnings": cap,
                "audio": audio,
            },
        }


# ---------------------------------------------------------------------------
# project settings
# ---------------------------------------------------------------------------


def update_settings(h: ProjectHandle, *, name: Optional[str] = None, mode: Optional[str] = None,
                    config: Optional[dict] = None, mix: Optional[dict] = None) -> None:
    """Mode switches keep all inputs and manual edits; results get staleness markers."""
    with h.lock:
        p = h.project
        if name is not None:
            p.name = name
        if mode is not None:
            if mode not in ("plain", "lrc"):
                raise ServiceError("模式只能是 plain 或 lrc")
            p.mode = mode  # type: ignore[assignment]
        if config:
            merged = _deep_merge(p.config.model_dump(mode="json"), config)
            p.config = AlignConfig.model_validate(merged)
        if mix:
            p.mix = MixSettings.model_validate({**p.mix.model_dump(), **mix})
        h.save()


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# lyrics input
# ---------------------------------------------------------------------------


def parse_lyrics(h: ProjectHandle, text: str, *, origin: str = "paste", filename: Optional[str] = None,
                 mode: Optional[str] = None) -> dict:
    """Parse text into a preview; nothing in the project changes until apply."""
    from .lyrics.parse import LyricsFormatError, LyricsModeError, detect_format, parse_lyrics_text

    mode = mode or h.project.mode
    preview_id = new_id("pv")
    detected = detect_format(text, filename)
    if detected == "json-prepared":
        return _parse_prepared(h, text, origin, filename, mode)
    if detected in ("json-project", "json-alignment", "json-reading-patch"):
        where = {"json-project": "请使用“导入项目”", "json-alignment": "请使用“导入结果”",
                 "json-reading-patch": "请在“粘贴 AI 结果”处使用"}[detected]
        return {"preview_id": None, "detected": detected, "warnings": [], "doc": None, "extra_tracks": {},
                "error": f"这是 {detected} 文件，不是歌词；{where}", "route": detected}
    try:
        res = parse_lyrics_text(text, mode=mode, origin=origin, filename=filename)  # type: ignore[arg-type]
    except LyricsModeError as e:
        detected = detect_format(text, filename)
        return {"preview_id": None, "detected": detected, "warnings": [], "error": str(e), "doc": None,
                "extra_tracks": {}}
    except LyricsFormatError as e:
        return {"preview_id": None, "detected": "unknown", "warnings": [], "error": str(e), "doc": None,
                "extra_tracks": {}}
    h.previews[preview_id] = res
    return {"preview_id": preview_id, "detected": res.detected, "warnings": res.warnings, "error": None,
            "doc": res.doc.model_dump(mode="json"), "extra_tracks": {}}


def _parse_prepared(h: ProjectHandle, text: str, origin: str, filename: Optional[str], mode: str) -> dict:
    """``prepared.json`` (lyrics + readings) → preview; times kept only in LRC mode."""
    import hashlib
    import json

    from .lyrics.parse import ParseResult

    data = json.loads(text)
    try:
        doc = LyricsDoc.model_validate({k: data[k] for k in ("language", "meta", "lines", "embedded_offset_raw",
                                                            "embedded_shift_ms", "embedded_offset_note")
                                        if k in data})
    except Exception as e:
        return {"preview_id": None, "detected": "json-prepared", "warnings": [], "doc": None, "extra_tracks": {},
                "error": f"prepared.json 校验失败: {e}"}
    warnings = []
    has_times = any(ln.imported_start_ms is not None for ln in doc.lines)
    if mode == "plain" and has_times:
        for ln in doc.lines:
            ln.imported_start_ms = ln.imported_end_ms = None
        warnings.append("普通模式：只导入正文与读音，已忽略其中的行时间")
    if mode == "lrc" and not has_times:
        return {"preview_id": None, "detected": "json-prepared", "warnings": [], "doc": None, "extra_tracks": {},
                "error": "LRC 增强模式需要带行时间的歌词：请补充时间或切换到普通模式"}
    snap = SourceSnapshot(origin=origin, kind="readings", filename=filename, text=text,  # type: ignore[arg-type]
                          sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())
    for ln in doc.lines:
        ln.source.source_id = snap.id
    res = ParseResult(doc=doc, warnings=warnings, detected="json-prepared", snapshot=snap)  # type: ignore[arg-type]
    preview_id = new_id("pv")
    h.previews[preview_id] = res
    return {"preview_id": preview_id, "detected": "json-prepared", "warnings": warnings, "error": None,
            "doc": doc.model_dump(mode="json"), "extra_tracks": {}}


def import_result_json(h: ProjectHandle, text: str) -> AlignmentResult:
    """Import an ``alignment.json`` (e.g. shared by someone) as a non-active result.

    Only accepted when its units refer to the current lyrics; staleness is
    recomputed so a result made from different inputs is clearly marked.
    """
    try:
        r = AlignmentResult.model_validate_json(text)
    except Exception as e:
        raise ServiceError(f"对齐结果 JSON 校验失败: {e}") from e
    known = {u.id for ln in h.project.lyrics.lines for u in ln.units()}
    unknown = [u.unit_id for u in r.units if u.unit_id not in known]
    if unknown:
        raise ServiceError(f"结果中有 {len(unknown)} 个单元不属于当前歌词（读音分组或歌词不同），无法导入")
    with h.lock:
        if any(x.id == r.id for x in h.project.results):
            r.id = new_id("r")
        r.stats["imported"] = True
        h.project.results.append(r)
        refresh_staleness(h.project)
        h.save()
    return r


def apply_lyrics(h: ProjectHandle, preview_id: str, *, prepare: bool = True) -> list[str]:
    res = h.previews.pop(preview_id, None)
    if res is None:
        raise ServiceError("预览已失效，请重新解析")
    with h.lock:
        p = h.project
        doc: LyricsDoc = res.doc
        if res.snapshot is not None:
            p.sources.append(res.snapshot)
        extra = getattr(res, "extra_tracks", None) or {}
        p.lyrics = doc
        messages: list[str] = []
        if prepare:
            messages += prepare_readings_locked(p)
        # calibration refers to the old lines: keep the shift, drop references that no longer exist
        ids = {ln.id for ln in doc.lines}
        if p.calibration.reference_line_id not in ids:
            p.calibration.reference_line_id = None
        p.calibration.checks = [c for c in p.calibration.checks if c.line_id in ids]
        h.save()
        return messages + [f"可配对的附加歌词轨: {', '.join(extra)}"] if extra else messages


def prepare_readings_locked(p: Project, overwrite_rule: bool = True) -> list[str]:
    from .reading.prepare import prepare_doc

    rep = prepare_doc(p.lyrics, overwrite_rule=overwrite_rule)
    return list(rep.messages)


def prepare_readings(h: ProjectHandle, overwrite_rule: bool = True) -> dict:
    from .reading.prepare import prepare_doc

    with h.lock:
        rep = prepare_doc(h.project.lyrics, overwrite_rule=overwrite_rule)
        h.save()
        return asdict(rep)


def preview_track(h: ProjectHandle, text: str, kind: str) -> dict:
    from .lyrics.pairing import pair_track

    prev = pair_track(h.project.lyrics, text, kind=kind)  # type: ignore[arg-type]
    return {
        "kind": kind,
        "pairs": [asdict(x) for x in prev.pairs],
        "unmatched_line_ids": prev.unmatched_line_ids,
        "unmatched": [t.text for t in prev.unmatched_texts],
    }


def apply_track(h: ProjectHandle, kind: str, pairs: list[dict]) -> None:
    from .lyrics.pairing import apply_pairs

    with h.lock:
        h.project.lyrics = apply_pairs(h.project.lyrics, [(x["line_id"], x["text"]) for x in pairs], kind=kind)  # type: ignore[arg-type]
        h.save()


def fetch_link(text: str) -> dict:
    from .lyrics.fetch import fetch_lyrics_from_link
    from .lyrics.fetch.types import CollectionListing

    out = fetch_lyrics_from_link(text)
    if isinstance(out, CollectionListing):
        return {"kind": "collection", "platform": out.platform, "title": out.title,
                "songs": [asdict(s) for s in out.songs]}
    return {"kind": "song", "song": _song_dict(out)}


def fetch_song(platform: str, song_id: str) -> dict:
    from .lyrics.fetch import fetch_song as _fetch

    return {"kind": "song", "song": _song_dict(_fetch(platform, song_id))}


def _song_dict(song) -> dict:
    d = asdict(song)
    return d


def parse_from_song(h: ProjectHandle, platform: str, song_id: str) -> dict:
    """Fetch lyrics of one song and parse its original track into a preview."""
    from .lyrics.fetch import fetch_song as _fetch
    from .lyrics.parse import LyricsModeError, parse_lyrics_text

    song = _fetch(platform, song_id)
    original = song.tracks.get("original")
    if not original:
        return {"preview_id": None, "detected": "unknown", "warnings": [], "doc": None, "extra_tracks": {},
                "error": "该歌曲没有可用的原文歌词，请手动输入"}
    snap = song.to_snapshot("original")
    try:
        res = parse_lyrics_text(original, mode=h.project.mode, origin=platform, source_id=snap.id)
    except LyricsModeError as e:
        return {"preview_id": None, "detected": "plain", "warnings": [], "doc": None, "error": str(e),
                "extra_tracks": {k: v for k, v in song.tracks.items() if k != "original"}}
    res.snapshot = snap
    doc = res.doc
    doc.meta.title = doc.meta.title or song.title
    doc.meta.artist = doc.meta.artist or ", ".join(song.artists) or None
    doc.meta.album = doc.meta.album or song.album
    doc.meta.duration_ms = doc.meta.duration_ms or song.duration_ms
    extra = {k: v for k, v in song.tracks.items() if k != "original" and v}
    res.extra_tracks = extra  # type: ignore[attr-defined]
    preview_id = new_id("pv")
    h.previews[preview_id] = res
    warnings = list(res.warnings) + list(song.notes)
    if not song.has_timestamps.get("original"):
        warnings.append("平台只提供了无时间歌词；不会生成伪 LRC")
    return {"preview_id": preview_id, "detected": res.detected, "warnings": warnings, "error": None,
            "doc": doc.model_dump(mode="json"), "extra_tracks": extra,
            "song": {k: v for k, v in _song_dict(song).items() if k != "tracks"}}


# ---------------------------------------------------------------------------
# line editing
# ---------------------------------------------------------------------------


def update_line(h: ProjectHandle, line_id: str, **fields: Any) -> None:
    from .reading.prepare import prepare_line

    with h.lock:
        ln = _line(h, line_id)
        text_changed = "text" in fields and fields["text"] is not None and fields["text"] != ln.text
        for k in ("text", "sing", "kind", "translation", "voice"):
            if k in fields and fields[k] is not None:
                setattr(ln, k, fields[k])
        if text_changed:
            prepare_line(ln, h.project.lyrics.language)
        h.save()


def _line(h: ProjectHandle, line_id: str):
    try:
        return h.project.lyrics.line(line_id)
    except KeyError:
        raise ServiceError(f"没有歌词行 {line_id}") from None


def merge_lines(h: ProjectHandle, line_ids: list[str]) -> None:
    from .lyrics.pairing import merge_lines as _merge
    from .reading.prepare import prepare_doc

    with h.lock:
        h.project.lyrics = _merge(h.project.lyrics, line_ids)
        prepare_doc(h.project.lyrics, overwrite_rule=False)
        h.save()


def split_line(h: ProjectHandle, line_id: str, at: int) -> None:
    from .lyrics.pairing import split_line as _split
    from .reading.prepare import prepare_doc

    with h.lock:
        h.project.lyrics = _split(h.project.lyrics, line_id, at)
        prepare_doc(h.project.lyrics, overwrite_rule=False)
        h.save()


def set_line_anchor(h: ProjectHandle, line_id: str, abs_ms: Optional[int], hard: bool = True,
                    tolerance_ms: int = 80) -> None:
    with h.lock:
        ln = _line(h, line_id)
        if abs_ms is None:
            ln.anchor = None
        else:
            if abs_ms < 0:
                raise ServiceError("锚点不能为负")
            orig = h.project.asset("original")
            if orig and abs_ms > orig.duration_ms:
                raise ServiceError("锚点超出音频长度")
            ln.anchor = LineAnchor(abs_ms=int(abs_ms), hard=hard, tolerance_ms=int(tolerance_ms))
        h.save()


def set_segment_reading(h: ProjectHandle, line_id: str, segment_id: str, reading: str,
                        units: Optional[list[str]] = None, confirm: bool = True) -> None:
    from .reading.prepare import set_segment_reading as _set

    with h.lock:
        ln = _line(h, line_id)
        try:
            _set(ln, segment_id, reading, units, source="manual", confirm=confirm)
        except (KeyError, ValueError) as e:
            raise ServiceError(str(e)) from e
        h.save()


# ---------------------------------------------------------------------------
# AI round trip
# ---------------------------------------------------------------------------


def ai_prompt(h: ProjectHandle, line_ids: Optional[list[str]] = None) -> dict:
    from .reading.ai import build_prompt

    with h.lock:
        bundle = build_prompt(h.project.lyrics, line_ids, lang=h.project.lyrics.language)
        h.project.ai_roundtrips.append(bundle.roundtrip)
        h.save()
        return {"prompt": bundle.prompt, "snapshot_id": bundle.snapshot_id, "roundtrip_id": bundle.roundtrip.id}


def ai_validate(h: ProjectHandle, text: str) -> dict:
    from .reading.ai import PatchParseError, extract_json, validate_patch

    try:
        obj = extract_json(text)
    except PatchParseError as e:
        raise ServiceError(f"无法解析 AI 结果: {e}") from e
    with h.lock:
        report = validate_patch(h.project.lyrics, obj, h.project.ai_roundtrips)
        rt = _roundtrip_for(h.project, obj)
        if rt is not None:
            rt.response_raw = text[:500_000]
            rt.status = "validated"
            rt.report = report.to_dict()
        report_id = new_id("rep")
        h.previews[report_id] = (report, rt.id if rt else None)
        h.save()
        return {"report_id": report_id, "report": report.to_dict()}


def _roundtrip_for(p: Project, obj: Any) -> Optional[AiRoundtrip]:
    snap = obj.get("snapshot") if isinstance(obj, dict) else None
    for rt in reversed(p.ai_roundtrips):
        if snap and rt.snapshot_id == snap:
            return rt
    return None


def ai_apply(h: ProjectHandle, report_id: str, line_ids: Optional[list[str]] = None) -> dict:
    from .reading.ai import apply_patch

    item = h.previews.get(report_id)
    if item is None:
        raise ServiceError("校验报告已失效，请重新粘贴 AI 结果")
    report, rt_id = item
    with h.lock:
        new_doc, summary = apply_patch(h.project.lyrics, report, include_line_ids=line_ids)
        h.project.lyrics = new_doc
        for rt in h.project.ai_roundtrips:
            if rt.id == rt_id:
                rt.status = "applied"
                from .models import utcnow

                rt.applied_at = utcnow()
        h.save()
        return summary if isinstance(summary, dict) else {"summary": summary}


# ---------------------------------------------------------------------------
# audio
# ---------------------------------------------------------------------------


def add_audio(h: ProjectHandle, src_path: Path, role: str, filename: Optional[str] = None,
              source_kind: str = "upload") -> AudioAsset:
    """Add original audio or an existing stem (stems get a sync check)."""
    from .audio.io import import_asset

    if role not in ("original", "vocals", "instrumental"):
        raise ServiceError("音轨角色只能是 original / vocals / instrumental")
    src = AudioSource(kind=source_kind, filename=filename or Path(src_path).name)  # type: ignore[arg-type]
    asset = import_asset(src_path, role, h.assets_dir, src, project_dir=h.dir)  # type: ignore[arg-type]
    with h.lock:
        p = h.project
        if role != "original":
            orig = p.asset("original")
            if orig is None:
                asset.source.notes.append("导入时没有原曲，无法检查同步")
            else:
                asset.sync_report = _sync_report(h, orig, asset)
                asset.sync_checked = True
                asset.source.parent_sha256 = orig.sha256
        # a new original invalidates stems derived from another original
        p.audio = [a for a in p.audio if a.role != role]
        if role == "original":
            for a in p.audio:
                if a.source.parent_sha256 and a.source.parent_sha256 != asset.sha256:
                    a.source.notes.append("原曲已更换：此音轨对应旧原曲")
        p.audio.append(asset)
        h.save()
    return asset


def _sync_report(h: ProjectHandle, orig: AudioAsset, stem: AudioAsset) -> dict:
    from .audio.io import load_audio
    from .audio.sync import check_stem_sync

    sr = 16000
    o, _ = load_audio(asset_path(h, orig), target_sr=sr, mono=True)
    s, _ = load_audio(asset_path(h, stem), target_sr=sr, mono=True)
    other = None
    counterpart = h.project.asset("instrumental" if stem.role == "vocals" else "vocals")
    if counterpart is not None and counterpart.path:
        other, _ = load_audio(asset_path(h, counterpart), target_sr=sr, mono=True)
        other = other[0]
    rep = check_stem_sync(o[0], s[0], sr, stem.role, other_stem=other)
    return _jsonable(rep)


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def asset_path(h: ProjectHandle, asset: AudioAsset) -> Path:
    p = store.asset_abspath(h.dir, asset.path)
    if p is None or not p.exists():
        raise ServiceError(f"音频 {asset.role} 缺失，请重新上传（sha256 {asset.sha256[:12]}…）")
    return p


def get_asset(h: ProjectHandle, asset_id: str) -> AudioAsset:
    for a in h.project.audio:
        if a.id == asset_id:
            return a
    raise ServiceError(f"没有音频 {asset_id}")


def playback_wav(h: ProjectHandle, asset: AudioAsset) -> Path:
    """Decoded PCM WAV made by the same decoder the aligner uses (same origin)."""
    from .audio.io import load_audio, write_wav

    out = store.cache_dir("playback") / f"{asset.sha256}.wav"
    if not out.exists():
        data, sr = load_audio(asset_path(h, asset))
        tmp = out.with_suffix(f".{new_id()}.tmp.wav")
        write_wav(tmp, data, sr)
        tmp.replace(out)
    return out


def peaks(h: ProjectHandle, asset: AudioAsset, per_second: int = 200) -> dict:
    from .audio.analysis import waveform_peaks
    from .audio.io import load_audio

    per_second = max(10, min(int(per_second), 2000))
    cache = store.cache_dir("peaks") / f"{asset.sha256}-{per_second}.npz"
    if cache.exists():
        z = np.load(cache)
        mins, maxs, sr = z["mins"], z["maxs"], int(z["sr"])
    else:
        data, sr = load_audio(asset_path(h, asset), mono=True)
        spp = max(1, int(round(sr / per_second)))
        mins, maxs = waveform_peaks(data[0], sr, spp)
        np.savez(cache, mins=mins, maxs=maxs, sr=sr)
    return {"sample_rate": sr, "duration_ms": asset.duration_ms, "per_second": per_second,
            "mins": np.round(mins, 4).tolist(), "maxs": np.round(maxs, 4).tolist()}


def run_separation(h: ProjectHandle, preset: str, cancel: Optional[CancelToken] = None,
                   progress: Optional[Callable[[float, str], None]] = None) -> dict:
    """Separate the original; failure raises (never a silent fallback)."""
    from .audio.io import import_asset
    from .audio.separation import separate

    orig = h.project.asset("original")
    if orig is None:
        raise ServiceError("请先上传原曲")
    src = asset_path(h, orig)
    out_dir = store.cache_dir("separation") / f"{orig.sha256[:16]}-{preset}"
    if out_dir.exists():
        shutil.rmtree(out_dir)  # never trust a possibly partial earlier run
    out_dir.mkdir(parents=True)
    result = separate(src, out_dir, preset, cancel=cancel, progress=progress)
    if cancel is not None:
        cancel.check()
    report = _jsonable(result.report)
    assets = []
    for role, path in (("vocals", result.vocals_path), ("instrumental", result.instrumental_path)):
        source = AudioSource(kind="separation", filename=Path(path).name, model=report.get("model_filename"),
                             model_version=report.get("audio_separator_version"),
                             config={"preset": preset}, parent_sha256=orig.sha256)
        assets.append(import_asset(path, role, h.assets_dir, source, project_dir=h.dir))  # type: ignore[arg-type]
    with h.lock:
        h.project.audio = [a for a in h.project.audio if a.role not in ("vocals", "instrumental")]
        for a in assets:
            a.sync_report = report.get("sync") if isinstance(report.get("sync"), dict) else None
            a.sync_checked = a.sync_report is not None
            h.project.audio.append(a)
        h.save()
    shutil.rmtree(out_dir, ignore_errors=True)
    return {"report": report, "assets": [a.id for a in assets]}


def mix_bus_gain(h: ProjectHandle, settings: dict) -> dict:
    from .audio.io import load_audio
    from .audio.mix import mix_stems

    v, i = h.project.asset("vocals"), h.project.asset("instrumental")
    if v is None or i is None:
        raise ServiceError("人声保留比例需要人声和伴奏两条分轨；只有原曲时无法单独降低人声")
    s = MixSettings.model_validate({**h.project.mix.model_dump(), **settings})
    vd, sr = load_audio(asset_path(h, v))
    idata, sr2 = load_audio(asset_path(h, i), target_sr=sr)
    _, rep = mix_stems(vd, idata, sr, s.vocal_keep_pct, s.instrumental_pct, s.master,
                       limiter="normalize_peak" if s.limiter == "normalize_peak" else "none")
    return {"bus_gain": rep.bus_gain, "peak_before": rep.peak_before}


def export_mix(h: ProjectHandle, settings: dict, out_path: Optional[Path] = None) -> dict:
    from .audio.mix import export_mix_wav

    v, i = h.project.asset("vocals"), h.project.asset("instrumental")
    if v is None or i is None:
        raise ServiceError("导出混音需要人声和伴奏两条分轨；只有原曲时无法单独降低人声")
    s = MixSettings.model_validate({**h.project.mix.model_dump(), **settings})
    orig = h.project.asset("original")
    name = f"mix-v{int(round(s.vocal_keep_pct))}-i{int(round(s.instrumental_pct))}.wav"
    out = Path(out_path) if out_path else h.dir / "exports" / name
    out.parent.mkdir(parents=True, exist_ok=True)
    original_n = None
    if orig is not None and orig.sample_rate == v.sample_rate:
        original_n = orig.num_samples
    rep = export_mix_wav(asset_path(h, v), asset_path(h, i), out, s.vocal_keep_pct, s.instrumental_pct,
                         s.master, limiter="normalize_peak" if s.limiter == "normalize_peak" else "none",
                         original_num_samples=original_n)
    with h.lock:
        h.project.mix = s
        h.save()
    rep_d = _jsonable(asdict(rep)) if hasattr(rep, "__dataclass_fields__") else _jsonable(rep)
    return {"filename": out.name, "path": str(out), "report": rep_d}


# ---------------------------------------------------------------------------
# calibration
# ---------------------------------------------------------------------------


def calibration_op(h: ProjectHandle, op: str, **kw: Any) -> None:
    from .align import calibration as C

    with h.lock:
        p = h.project
        if op == "mark":
            _line(h, kw["line_id"])
            p.calibration = C.mark_first_onset(p.calibration, p.lyrics, kw["line_id"], int(kw["marked_ms"]))
        elif op == "shift":
            p.calibration = C.set_user_shift(p.calibration, int(kw["user_shift_ms"]), p.lyrics)
        elif op == "confirm-zero":
            p.calibration = C.confirm_zero(p.calibration)
        elif op == "check":
            _line(h, kw["line_id"])
            p.calibration = C.add_check(p.calibration, p.lyrics, kw["line_id"], int(kw["marked_ms"]))
        elif op == "undo":
            p.calibration = C.undo(p.calibration)
        else:
            raise ServiceError(f"未知校准操作 {op}")
        h.save()


# ---------------------------------------------------------------------------
# alignment
# ---------------------------------------------------------------------------


def _load_for_backend(h: ProjectHandle, asset: AudioAsset, sr: int) -> np.ndarray:
    from .audio.io import load_audio

    data, _ = load_audio(asset_path(h, asset), target_sr=sr, mono=True)
    return data[0]


def run_align(h: ProjectHandle, *, line_ids: Optional[list[str]] = None, audio_role: Optional[str] = None,
              config: Optional[dict] = None, cancel: Optional[CancelToken] = None,
              progress: Optional[Callable[[float, str], None]] = None, backend=None) -> AlignmentResult:
    """Run an alignment on a snapshot of the current inputs.

    The result is appended only when the run completes.  A full run becomes
    the active result; a local rerun (``line_ids``) is stored as a separate
    partial result with ``parent_result_id`` and never overwrites anything.
    """
    from .align.backends import get_backend
    from .align.emission_cache import EmissionCache, emission_cache_key
    from .align.runner import AlignInputs, run_alignment
    from .audio.analysis import rms_envelope_db
    from .reading.profiles import get_profile

    progress = progress or (lambda f, m="": None)
    with h.lock:
        snap: Project = copy.deepcopy(h.project)
    cfg = snap.config
    if config:
        cfg = AlignConfig.model_validate(_deep_merge(cfg.model_dump(mode="json"), config))
    if audio_role:
        cfg = cfg.model_copy(update={"audio_role": audio_role})
    if not snap.lyrics.sung_lines():
        raise ServiceError("没有参与对齐的歌词行")
    if snap.mode == "lrc":
        from .align.calibration import effective_line_starts, validate_anchors

        if not effective_line_starts(snap.lyrics, snap.calibration):
            raise ServiceError("LRC 增强模式需要有效的行时间；请补充时间或切换到普通模式")
        orig = snap.asset("original")
        errors = [i for i in validate_anchors(snap.lyrics, snap.calibration, orig.duration_ms if orig else None)
                  if i.severity == "error"]
        if errors:
            raise ServiceError("锚点需要修正: " + "; ".join(i.message for i in errors[:5]))
    missing_units = [ln.id for ln in snap.lyrics.sung_lines() if not ln.units()]
    if missing_units:
        from .reading.prepare import prepare_doc

        prepare_doc(snap.lyrics, overwrite_rule=False)

    original = snap.asset("original")
    if original is None:
        raise ServiceError("请先上传原曲")
    roles = ["original"] + (["vocals"] if snap.asset("vocals") is not None else [])
    if cfg.audio_role not in roles:
        raise ServiceError("选择了人声作为对齐输入，但项目中没有人声分轨（请先分离或导入）")

    progress(0.02, "加载模型")
    backend = backend or get_backend(cfg)
    info = backend.info()
    profile = get_profile(info.profile)
    cache = EmissionCache(store.cache_dir("emissions"))
    sr = backend.sample_rate
    emissions: dict[str, Emission] = {}
    assets = {r: snap.asset(r) for r in roles}

    def emission_for(role: str) -> Emission:
        if role in emissions:
            return emissions[role]
        asset = assets[role]
        key = emission_cache_key(asset.sha256, role, asset.origin_offset_samples, info, cfg.chunk_s,
                                 cfg.context_s, f"resample_poly->{sr}")
        em = cache.get(key)
        if em is None:
            progress(0.05, f"声学推理（{role}）")
            if cancel is not None:
                cancel.check()
            audio = _load_for_backend(h, asset, sr)
            origin = int(round(asset.origin_offset_samples * sr / asset.sample_rate))

            def sub_progress(frac: float, msg: str = "") -> None:
                progress(0.05 + 0.6 * frac, msg or f"声学推理（{role}）")

            em = backend.emissions(audio, origin_samples=origin, cancel=cancel, progress=sub_progress)
            if cancel is not None:
                cancel.check()
            cache.put(key, em, info, complete=True)
            em.cache_key = key
        emissions[role] = em
        return em

    envelopes: dict[str, tuple[np.ndarray, float]] = {}

    def energy_for(role: str) -> tuple[np.ndarray, float]:
        if role not in envelopes:
            from .audio.io import load_audio

            asset = assets[role]
            data, asr = load_audio(asset_path(h, asset), mono=True)
            envelopes[role] = (rms_envelope_db(data[0], asr, hop_ms=10.0), 10.0)
        return envelopes[role]

    # acoustic scores for the chosen input first, so progress stays monotonic
    emission_for(cfg.audio_role)
    previous = snap.result()
    inp = AlignInputs(
        lyrics=snap.lyrics, mode=snap.mode, calibration=snap.calibration, config=cfg, backend_info=info,
        tokenize=backend.tokenize, profile=profile, emission_for=emission_for, available_roles=roles,
        audio_assets={r: a for r, a in assets.items() if a is not None}, audio_duration_ms=original.duration_ms,
        energy_for=energy_for, previous=previous, line_ids=line_ids,
    )

    def run_progress(frac: float, msg: str = "") -> None:
        progress(0.65 + 0.35 * frac, msg or "解码")

    result = run_alignment(inp, cancel=cancel, progress=run_progress)
    if cancel is not None:
        cancel.check()
    if line_ids:
        result.parent_result_id = previous.id if previous else None
    with h.lock:
        h.project.results.append(result)
        if not line_ids:
            h.project.active_result_id = result.id
        refresh_staleness(h.project)
        h.save()
    return result


def get_result(h: ProjectHandle, result_id: str) -> AlignmentResult:
    r = h.project.result(result_id)
    if r is None:
        raise ServiceError(f"没有对齐结果 {result_id}")
    refresh_staleness(h.project)
    return r


def activate_result(h: ProjectHandle, result_id: str) -> None:
    with h.lock:
        get_result(h, result_id)
        h.project.active_result_id = result_id
        h.save()


def adopt_lines(h: ProjectHandle, result_id: str, line_ids: list[str], *, from_result_id: Optional[str] = None,
                candidate_id: Optional[str] = None) -> AlignmentResult:
    """Copy non-locked unit times of ``line_ids`` from a rerun / candidate into ``result_id``."""
    with h.lock:
        target = get_result(h, result_id)
        if candidate_id:
            cand = next((c for r in h.project.results for c in r.candidates if c.id == candidate_id), None)
            if cand is None:
                raise ServiceError("没有该候选")
            source_units = cand.units
            line_ids = line_ids or [cand.line_id]
            label = f"candidate:{cand.label}"
        elif from_result_id:
            src = get_result(h, from_result_id)
            source_units = src.units
            label = f"result:{from_result_id}"
        else:
            raise ServiceError("需要 from_result_id 或 candidate_id")
        by_id = {u.unit_id: u for u in source_units if u.line_id in set(line_ids)}
        target_ids = {u.unit_id for u in target.units if u.line_id in set(line_ids)}
        if set(by_id) != target_ids:
            raise ServiceError("单元不一致（读音分组已变化），无法直接采用；请完整重跑")
        adopted = 0
        for i, u in enumerate(target.units):
            if u.unit_id in by_id and not u.locked:
                newu = by_id[u.unit_id].model_copy(deep=True)
                newu.manual, newu.manual_history = u.manual, u.manual_history
                newu.flags = [f for f in newu.flags if f != "adopted"] + ["adopted"]
                target.units[i] = newu
                adopted += 1
        src_lines = {}
        if from_result_id:
            src_lines = {lt.line_id: lt for lt in get_result(h, from_result_id).lines}
        for i, lt in enumerate(target.lines):
            if lt.line_id in line_ids:
                if lt.line_id in src_lines:
                    target.lines[i] = src_lines[lt.line_id].model_copy(deep=True)
                units = [u for u in target.units if u.line_id == lt.line_id]
                st = [u.start_ms for u in units if u.start_ms is not None]
                en = [u.end_ms for u in units if u.end_ms is not None]
                target.lines[i].start_ms = min(st) if st else None
                target.lines[i].end_ms = max(en) if en else None
                target.lines[i].candidate = label
        target.stats["adoptions"] = target.stats.get("adoptions", 0) + adopted
        h.save()
        return target


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def export(h: ProjectHandle, fmt: str, result_id: Optional[str] = None):
    from .project.exports import export as _export

    refresh_staleness(h.project)
    result = get_result(h, result_id) if result_id else h.project.result()
    try:
        return _export(h.project, fmt, result)
    except ValueError as e:
        raise ServiceError(str(e)) from e
