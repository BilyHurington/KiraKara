"""Local WebUI server (see docs/api.md).  All logic lives in :mod:`kara_align.service`."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__
from .. import service as S
from ..project.jobs import Job, JobManager, progress_setter
from ..project.store import ProjectError

STATIC_DIR = Path(__file__).parent / "static"
MAX_TEXT_BYTES = 5 * 1024 * 1024
MAX_AUDIO_BYTES = 8 * 1024 * 1024 * 1024  # videos can be large


# ---------------------------------------------------------------------------
# request bodies
# ---------------------------------------------------------------------------


class CreateBody(BaseModel):
    name: str = "untitled"
    mode: str = "plain"


class PatchProjectBody(BaseModel):
    name: Optional[str] = None
    mode: Optional[str] = None
    config: Optional[dict] = None
    mix: Optional[dict] = None


class TextBody(BaseModel):
    text: str
    origin: str = "paste"
    filename: Optional[str] = None
    kind: Optional[str] = None


class PreviewBody(BaseModel):
    preview_id: str


class TrackApplyBody(BaseModel):
    kind: str
    pairs: list[dict]


class LinkBody(BaseModel):
    text: str


class SongBody(BaseModel):
    platform: str
    song_id: str


class LinePatch(BaseModel):
    text: Optional[str] = None
    sing: Optional[bool] = None
    kind: Optional[str] = None
    translation: Optional[str] = None
    voice: Optional[str] = None


class LineIdsBody(BaseModel):
    line_ids: Optional[list[str]] = None


class SplitBody(BaseModel):
    at: int


class AnchorBody(BaseModel):
    abs_ms: Optional[int] = None
    hard: bool = True
    tolerance_ms: int = 80


class PrepareBody(BaseModel):
    overwrite_rule: bool = True


class SegmentBody(BaseModel):
    reading: str
    units: Optional[list[str]] = None
    confirm: bool = True


class ReportApplyBody(BaseModel):
    report_id: str
    line_ids: Optional[list[str]] = None


class SeparateBody(BaseModel):
    preset: str = "melband-roformer"
    device: str = "auto"


class MarkBody(BaseModel):
    line_id: str
    marked_ms: int


class ShiftBody(BaseModel):
    user_shift_ms: int


class AlignBody(BaseModel):
    line_ids: Optional[list[str]] = None
    audio_role: Optional[str] = None
    config: Optional[dict] = None


class UnitBody(BaseModel):
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None
    locked: bool = True


class LockBody(BaseModel):
    locked: bool


class RestoreBody(BaseModel):
    manual: Optional[dict] = None


class AdoptBody(BaseModel):
    from_result_id: Optional[str] = None
    candidate_id: Optional[str] = None
    line_ids: list[str] = []


# ---------------------------------------------------------------------------


def create_app(root: Optional[Path] = None, jobs: Optional[JobManager] = None) -> FastAPI:
    app = FastAPI(title="Kara Align", version=__version__)
    ws = S.Workspace(root)
    jm = jobs or JobManager()
    from ..pipeline import TaskQueue

    tq = TaskQueue(ws)
    app.state.workspace = ws
    app.state.jobs = jm
    app.state.tasks = tq

    @app.exception_handler(S.ServiceError)
    async def _service_error(_req: Request, exc: S.ServiceError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(ProjectError)
    async def _project_error(_req: Request, exc: ProjectError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    def handle(pid: str) -> S.ProjectHandle:
        try:
            return ws.get(pid)
        except ProjectError as e:
            raise HTTPException(404, str(e)) from e

    def view(h: S.ProjectHandle, **extra: Any) -> dict:
        v = S.project_view(h)
        v.update(extra)
        return v

    def guard(fn, *args, **kw):
        """Map module-level validation errors to 400 with a readable message."""
        try:
            return fn(*args, **kw)
        except (S.ServiceError, ProjectError, HTTPException):
            raise
        except (ValueError, KeyError) as e:
            raise HTTPException(400, str(e)) from e

    # ------------------------------------------------------------------ info / jobs

    @app.get("/api/info")
    def info():
        from ..align.backends import list_backends
        from ..audio.separation import preset_dicts
        from ..project.exports import EXPORT_FORMATS

        try:
            from ..audio.separation import ensure_available

            ensure_available()
            sep_ok = True
        except Exception:
            sep_ok = False
        return {
            "version": __version__,
            "backends": list_backends(),
            "separation_presets": preset_dicts(),
            "separation_available": sep_ok,
            "export_formats": {k: {"filename": v[0], "description": v[2]} for k, v in EXPORT_FORMATS.items()},
        }

    # ------------------------------------------------------------------ app settings / AI providers

    @app.get("/api/settings")
    def get_settings():
        from .. import settings as app_settings

        return app_settings.public(app_settings.load())

    @app.put("/api/settings")
    def put_settings(body: dict):
        from .. import settings as app_settings

        try:
            return app_settings.public(app_settings.update(body or {}))
        except ValueError as e:
            raise HTTPException(400, f"设置无效：{e}") from e

    @app.get("/api/ai/providers")
    def ai_providers(refresh: int = 0):
        from ..reading.llm import detect_all

        return detect_all(refresh=bool(refresh))

    @app.post("/api/ai/test")
    def ai_test(body: dict):
        """Send a tiny message with the given (or saved) AI settings."""
        from .. import settings as app_settings
        from ..reading.llm import LlmError, ask

        saved = app_settings.load().ai
        patch = {k: v for k, v in (body or {}).items() if k != "api_key" or v}
        cfg = app_settings.AiSettings.model_validate({**saved.model_dump(), **patch, "timeout_s": 120})
        try:
            r = ask(cfg, "只回复两个字母：OK")
        except LlmError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "reply": r.text.strip()[:200], "model": r.model, "elapsed_s": r.elapsed_s,
                "cost_usd": r.cost_usd}

    # ------------------------------------------------------------------ simple mode: task queue

    def task_or_404(task_id: str):
        try:
            return tq.get(task_id)
        except KeyError:
            raise HTTPException(404, "没有该任务") from None

    @app.get("/api/tasks")
    def list_tasks():
        return tq.list()

    @app.post("/api/tasks")
    async def add_task(file: UploadFile = File(...), lyrics: str = Form(...), mode: str = Form("lrc"),
                       name: str = Form("")):
        from ..audio.io import AudioError, validate_upload

        _check_text(lyrics)
        fname = Path(file.filename or "media").name
        td = tempfile.mkdtemp(prefix="kara-task-")
        try:
            tmp = Path(td) / fname
            size = await _save_upload(file, tmp, MAX_AUDIO_BYTES)
            try:
                with open(tmp, "rb") as f:
                    validate_upload(fname, f.read(64), size, MAX_AUDIO_BYTES)
            except AudioError as e:
                raise HTTPException(400, str(e)) from e
            t = tq.add(media=tmp, filename=fname, lyrics=lyrics, mode=mode, name=name)
        finally:
            shutil.rmtree(td, ignore_errors=True)
        return t.model_dump(mode="json")

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel_task(task_id: str):
        task_or_404(task_id)
        return tq.cancel(task_id).model_dump(mode="json")

    @app.post("/api/tasks/{task_id}/retry")
    def retry_task(task_id: str):
        task_or_404(task_id)
        return tq.retry(task_id).model_dump(mode="json")

    @app.delete("/api/tasks/{task_id}")
    def delete_task(task_id: str):
        task_or_404(task_id)
        tq.remove(task_id)
        return {"ok": True}

    def job_or_404(job_id: str) -> Job:
        job = jm.get(job_id)
        if job is None:
            raise HTTPException(404, "没有该任务")
        return job

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return job_or_404(job_id).to_dict()

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        jm.cancel(job_id)
        return job_or_404(job_id).to_dict()

    @app.get("/api/projects/{pid}/jobs")
    def project_jobs(pid: str):
        return [j.to_dict() for j in jm.list(pid)]

    # ------------------------------------------------------------------ projects

    @app.get("/api/projects")
    def list_projects():
        return ws.list()

    @app.post("/api/projects")
    def create_project(body: CreateBody):
        if body.mode not in ("plain", "lrc"):
            raise HTTPException(400, "模式只能是 plain 或 lrc")
        return view(ws.create(body.name, body.mode))

    @app.get("/api/projects/{pid}")
    def get_project(pid: str):
        return view(handle(pid))

    @app.patch("/api/projects/{pid}")
    def patch_project(pid: str, body: PatchProjectBody):
        h = handle(pid)
        guard(S.update_settings, h, name=body.name, mode=body.mode, config=body.config, mix=body.mix)
        return view(h)

    @app.post("/api/projects/import")
    async def import_project(file: UploadFile = File(...)):
        name = file.filename or "project.json"
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / ("upload.zip" if name.endswith(".zip") else "project.json")
            await _save_upload(file, tmp, 4 * 1024**3)
            h = ws.import_file(tmp, name)
        return view(h)

    @app.get("/api/projects/{pid}/package")
    def package(pid: str, include_audio: int = 1):
        from ..project.store import export_package

        h = handle(pid)
        out = h.dir / "exports" / f"{h.project.id}.kara.zip"
        export_package(h.project, h.dir, out, include_audio=bool(include_audio))
        return FileResponse(out, filename=out.name, media_type="application/zip")

    # ------------------------------------------------------------------ lyrics

    @app.post("/api/projects/{pid}/lyrics/parse")
    def lyrics_parse(pid: str, body: TextBody):
        _check_text(body.text)
        return S.parse_lyrics(handle(pid), body.text, origin=body.origin, filename=body.filename)

    @app.post("/api/projects/{pid}/lyrics/apply")
    def lyrics_apply(pid: str, body: PreviewBody):
        h = handle(pid)
        messages = S.apply_lyrics(h, body.preview_id)
        return view(h, messages=messages)

    @app.post("/api/projects/{pid}/lyrics/track/preview")
    def track_preview(pid: str, body: TextBody):
        _check_text(body.text)
        kind = body.kind or "translation"
        return guard(S.preview_track, handle(pid), body.text, kind)

    @app.post("/api/projects/{pid}/lyrics/track/apply")
    def track_apply(pid: str, body: TrackApplyBody):
        h = handle(pid)
        guard(S.apply_track, h, body.kind, body.pairs)
        return view(h)

    @app.post("/api/lyrics/link")
    def lyrics_link(body: LinkBody):
        from ..lyrics.fetch.types import FetchError

        try:
            return S.fetch_link(body.text)
        except FetchError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/lyrics/song")
    def lyrics_song(body: SongBody):
        from ..lyrics.fetch.types import FetchError

        try:
            return S.fetch_song(body.platform, body.song_id)
        except FetchError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/projects/{pid}/lyrics/from-song")
    def lyrics_from_song(pid: str, body: SongBody):
        from ..lyrics.fetch.types import FetchError

        try:
            return S.parse_from_song(handle(pid), body.platform, body.song_id)
        except FetchError as e:
            raise HTTPException(400, str(e)) from e

    # ------------------------------------------------------------------ lines / readings

    @app.patch("/api/projects/{pid}/lines/{line_id}")
    def patch_line(pid: str, line_id: str, body: LinePatch):
        h = handle(pid)
        guard(S.update_line, h, line_id, **body.model_dump(exclude_none=True))
        return view(h)

    @app.post("/api/projects/{pid}/lines/merge")
    def merge(pid: str, body: LineIdsBody):
        h = handle(pid)
        guard(S.merge_lines, h, body.line_ids or [])
        return view(h)

    @app.post("/api/projects/{pid}/lines/{line_id}/split")
    def split(pid: str, line_id: str, body: SplitBody):
        h = handle(pid)
        guard(S.split_line, h, line_id, body.at)
        return view(h)

    @app.put("/api/projects/{pid}/lines/{line_id}/anchor")
    def anchor(pid: str, line_id: str, body: AnchorBody):
        h = handle(pid)
        guard(S.set_line_anchor, h, line_id, body.abs_ms, body.hard, body.tolerance_ms)
        return view(h)

    @app.post("/api/projects/{pid}/readings/prepare")
    def readings_prepare(pid: str, body: PrepareBody):
        h = handle(pid)
        report = S.prepare_readings(h, body.overwrite_rule)
        return view(h, report=report)

    @app.put("/api/projects/{pid}/lines/{line_id}/segments/{segment_id}")
    def segment_reading(pid: str, line_id: str, segment_id: str, body: SegmentBody):
        h = handle(pid)
        guard(S.set_segment_reading, h, line_id, segment_id, body.reading, body.units, body.confirm)
        return view(h)

    @app.post("/api/projects/{pid}/ai/prompt")
    def ai_prompt(pid: str, body: LineIdsBody):
        return guard(S.ai_prompt, handle(pid), body.line_ids)

    @app.post("/api/projects/{pid}/ai/validate")
    def ai_validate(pid: str, body: TextBody):
        _check_text(body.text)
        return S.ai_validate(handle(pid), body.text)

    @app.post("/api/projects/{pid}/ai/auto")
    def ai_auto(pid: str, body: LineIdsBody):
        from .. import settings as app_settings

        h = handle(pid)
        cfg = app_settings.load().ai
        if cfg.provider == "none":
            raise HTTPException(400, "还没有设置 AI：请在“设置”中选择 Claude Code、Codex 或 API")

        def run(job: Job):
            return S.ai_auto(h, body.line_ids, cfg=cfg, cancel=job.cancel_token, progress=progress_setter(job))

        return jm.submit("ai", run, project_id=pid, heavy=False).to_dict()

    @app.post("/api/projects/{pid}/ai/apply")
    def ai_apply(pid: str, body: ReportApplyBody):
        h = handle(pid)
        summary = S.ai_apply(h, body.report_id, body.line_ids)
        return view(h, summary=summary)

    # ------------------------------------------------------------------ audio

    @app.post("/api/projects/{pid}/audio")
    async def upload_audio(pid: str, file: UploadFile = File(...), role: str = Form("original")):
        from ..audio.io import AudioError, validate_upload

        h = handle(pid)
        name = Path(file.filename or "audio").name
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / name
            size = await _save_upload(file, tmp, MAX_AUDIO_BYTES)
            try:
                with open(tmp, "rb") as f:
                    validate_upload(name, f.read(64), size, MAX_AUDIO_BYTES)
                S.add_media(h, tmp, role, filename=name, source_kind="upload" if role == "original" else "import")
            except AudioError as e:
                raise HTTPException(400, str(e)) from e
        return view(h)

    @app.get("/api/projects/{pid}/audio/{asset_id}/playback.wav")
    def playback(pid: str, asset_id: str):
        h = handle(pid)
        asset = S.get_asset(h, asset_id)
        path = S.playback_wav(h, asset)
        return FileResponse(path, media_type="audio/wav", filename=f"{asset.role}.wav")

    @app.get("/api/projects/{pid}/audio/{asset_id}/peaks")
    def peaks(pid: str, asset_id: str, per_second: int = 200):
        h = handle(pid)
        return S.peaks(h, S.get_asset(h, asset_id), per_second)

    @app.post("/api/projects/{pid}/separate")
    def separate(pid: str, body: SeparateBody):
        h = handle(pid)
        if h.project.asset("original") is None:
            raise HTTPException(400, "请先上传原曲")

        def run(job: Job):
            return S.run_separation(h, body.preset, cancel=job.cancel_token, progress=progress_setter(job),
                                    device=body.device)

        return jm.submit("separate", run, project_id=pid).to_dict()

    @app.post("/api/projects/{pid}/mix/preview-gain")
    def mix_gain(pid: str, body: dict):
        return S.mix_bus_gain(handle(pid), body or {})

    @app.post("/api/projects/{pid}/mix/export")
    def mix_export(pid: str, body: dict):
        h = handle(pid)
        if h.project.asset("vocals") is None or h.project.asset("instrumental") is None:
            raise HTTPException(400, "导出混音需要人声和伴奏两条分轨；只有原曲时无法单独降低人声")

        def run(job: Job):
            out = S.export_mix(h, body or {})
            return {"filename": out["filename"], "report": out["report"],
                    "url": f"/api/projects/{pid}/exports/{out['filename']}"}

        return jm.submit("mix", run, project_id=pid, heavy=False).to_dict()

    # ------------------------------------------------------------------ karaoke subtitles

    @app.get("/api/fonts")
    def fonts():
        from ..karaoke.fonts import default_family, families

        return {"default": default_family(), "families": families()}

    @app.get("/api/karaoke/presets")
    def karaoke_presets():
        from ..karaoke.presets import preset_list

        return preset_list()

    @app.get("/api/projects/{pid}/karaoke")
    def get_karaoke(pid: str):
        return handle(pid).project.karaoke.model_dump(mode="json")

    @app.put("/api/projects/{pid}/karaoke")
    def put_karaoke(pid: str, body: dict):
        h = handle(pid)
        S.set_karaoke_style(h, body)
        return h.project.karaoke.model_dump(mode="json")

    @app.post("/api/projects/{pid}/karaoke/preview")
    def karaoke_preview(pid: str, body: dict):
        from ..karaoke.render import RenderError

        try:
            png = S.karaoke_preview(handle(pid), int(body.get("t_ms", 0)), body.get("style"),
                                    background=body.get("background", "auto"))
        except RenderError as e:
            raise HTTPException(400, str(e)) from e
        return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/api/projects/{pid}/karaoke/burn")
    def karaoke_burn(pid: str, body: dict):
        h = handle(pid)
        if h.project.result() is None:
            raise HTTPException(400, "还没有对齐结果：请先完成对齐")
        audio = body.get("audio", "original")
        if audio not in ("original", "mix", "none"):
            raise HTTPException(400, "audio 只能是 original / mix / none")
        pct = body.get("vocal_keep_pct")
        if pct is not None and (not isinstance(pct, (int, float)) or not 0 <= pct <= 100):
            raise HTTPException(400, "vocal_keep_pct 必须是 0–100 之间的数字")

        def run(job: Job):
            out = S.karaoke_burn(h, background=body.get("background", "auto"), audio=audio,
                                 quality=body.get("quality", "standard"), vocal_keep_pct=pct,
                                 cancel=job.cancel_token,
                                 progress=progress_setter(job))
            return {"filename": out["filename"], "warnings": out["warnings"],
                    "url": f"/api/projects/{pid}/exports/{out['filename']}"}

        return jm.submit("burn", run, project_id=pid).to_dict()

    @app.post("/api/projects/{pid}/video/export")
    def video_export(pid: str, body: dict):
        h = handle(pid)
        if h.project.video is None:
            raise HTTPException(400, "项目中没有视频：请在“音频与歌词”中上传视频作为原曲")
        if h.project.asset("vocals") is None or h.project.asset("instrumental") is None:
            raise HTTPException(400, "降低人声需要人声和伴奏两条分轨，请先进行人声分离")

        def run(job: Job):
            job.message = "混音并合成视频"
            out = S.export_video(h, body or {})
            return {"filename": out["filename"], "report": out["report"],
                    "url": f"/api/projects/{pid}/exports/{out['filename']}"}

        return jm.submit("video", run, project_id=pid, heavy=False).to_dict()

    @app.get("/api/projects/{pid}/exports/{filename}")
    def exported_file(pid: str, filename: str):
        h = handle(pid)
        path = (h.dir / "exports" / Path(filename).name)
        if not path.exists():
            raise HTTPException(404, "文件不存在")
        return FileResponse(path, filename=path.name)

    # ------------------------------------------------------------------ calibration

    @app.post("/api/projects/{pid}/calibration/mark")
    def cal_mark(pid: str, body: MarkBody):
        h = handle(pid)
        guard(S.calibration_op, h, "mark", line_id=body.line_id, marked_ms=body.marked_ms)
        return view(h)

    @app.post("/api/projects/{pid}/calibration/shift")
    def cal_shift(pid: str, body: ShiftBody):
        h = handle(pid)
        guard(S.calibration_op, h, "shift", user_shift_ms=body.user_shift_ms)
        return view(h)

    @app.post("/api/projects/{pid}/calibration/confirm-zero")
    def cal_zero(pid: str):
        h = handle(pid)
        guard(S.calibration_op, h, "confirm-zero")
        return view(h)

    @app.post("/api/projects/{pid}/calibration/check")
    def cal_check(pid: str, body: MarkBody):
        h = handle(pid)
        guard(S.calibration_op, h, "check", line_id=body.line_id, marked_ms=body.marked_ms)
        return view(h)

    @app.post("/api/projects/{pid}/calibration/undo")
    def cal_undo(pid: str):
        h = handle(pid)
        guard(S.calibration_op, h, "undo")
        return view(h)

    # ------------------------------------------------------------------ alignment / results

    @app.post("/api/projects/{pid}/align")
    def align(pid: str, body: AlignBody):
        h = handle(pid)

        def run(job: Job):
            r = S.run_align(h, line_ids=body.line_ids, audio_role=body.audio_role, config=body.config,
                            cancel=job.cancel_token, progress=progress_setter(job))
            return {"result_id": r.id}

        return jm.submit("align", run, project_id=pid).to_dict()

    @app.get("/api/projects/{pid}/results/{rid}")
    def get_result(pid: str, rid: str):
        return S.get_result(handle(pid), rid).model_dump(mode="json")

    @app.post("/api/projects/{pid}/results/import")
    def import_result(pid: str, body: TextBody):
        _check_text(body.text)
        h = handle(pid)
        r = S.import_result_json(h, body.text)
        return view(h, result_id=r.id)

    @app.post("/api/projects/{pid}/results/{rid}/activate")
    def activate(pid: str, rid: str):
        h = handle(pid)
        S.activate_result(h, rid)
        return view(h)

    def unit_op(pid: str, rid: str, fn, *args, **kw):
        from ..project.edits import EditError

        h = handle(pid)
        with h.lock:
            r = S.get_result(h, rid)
            try:
                ut = fn(r, *args, **kw)
            except EditError as e:
                raise HTTPException(400, str(e)) from e
            h.save()
            return ut.model_dump(mode="json")

    @app.put("/api/projects/{pid}/results/{rid}/units/{uid}")
    def set_unit(pid: str, rid: str, uid: str, body: UnitBody):
        from ..project.edits import set_manual

        h = handle(pid)
        orig = h.project.asset("original")
        return unit_op(pid, rid, set_manual, uid, body.start_ms, body.end_ms, locked=body.locked,
                       duration_ms=orig.duration_ms if orig else None)

    @app.delete("/api/projects/{pid}/results/{rid}/units/{uid}/manual")
    def clear_unit(pid: str, rid: str, uid: str):
        from ..project.edits import clear_manual

        return unit_op(pid, rid, clear_manual, uid)

    @app.post("/api/projects/{pid}/results/{rid}/units/{uid}/lock")
    def lock_unit(pid: str, rid: str, uid: str, body: LockBody):
        from ..project.edits import set_lock

        return unit_op(pid, rid, set_lock, uid, body.locked)

    @app.post("/api/projects/{pid}/results/{rid}/units/{uid}/restore")
    def restore_unit(pid: str, rid: str, uid: str, body: RestoreBody):
        from ..project.edits import restore_manual

        return unit_op(pid, rid, restore_manual, uid, body.manual)

    @app.post("/api/projects/{pid}/results/{rid}/adopt")
    def adopt(pid: str, rid: str, body: AdoptBody):
        r = S.adopt_lines(handle(pid), rid, body.line_ids, from_result_id=body.from_result_id,
                          candidate_id=body.candidate_id)
        return r.model_dump(mode="json")

    # ------------------------------------------------------------------ export

    @app.get("/api/projects/{pid}/export/{fmt}")
    def export(pid: str, fmt: str, result_id: Optional[str] = None, download: int = 0):
        out = S.export(handle(pid), fmt, result_id)
        if download:
            headers = {"Content-Disposition": f'attachment; filename="{out.filename}"'}
            if out.warnings:
                headers["X-Export-Warnings"] = str(len(out.warnings))
            return Response(out.content.encode("utf-8"), media_type=f"{out.media_type}; charset=utf-8",
                            headers=headers)
        return {"filename": out.filename, "media_type": out.media_type, "content": out.content,
                "warnings": out.warnings}

    # ------------------------------------------------------------------ static UI

    @app.middleware("http")
    async def _no_stale_ui(request: Request, call_next):
        # UI files are small and local: always revalidate so an upgrade is picked up
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app


def _check_text(text: str) -> None:
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise HTTPException(413, "文本过大")


async def _save_upload(file: UploadFile, dest: Path, max_bytes: int) -> int:
    size = 0
    with open(dest, "wb") as f:
        while True:
            chunk = await file.read(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise HTTPException(413, "上传文件过大")
            f.write(chunk)
    return size
