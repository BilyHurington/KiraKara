"""Simple mode: one task per song, run start to finish in a queue.

A task turns (video or audio, music link or pasted lyrics, mode) into a karaoke
video with the app settings::

    import → lyrics → AI readings → separation → LRC offset → align → video

Each stage reuses the service functions of the detailed mode, so a finished
(or failed) task is an ordinary project that can be opened and refined there.
Tasks run one at a time; heavy stages share :data:`HEAVY_LOCK` with the
detailed mode's jobs.  The queue is saved in ``<workspace>/.tasks/tasks.json``;
after a restart, queued tasks continue and an interrupted one can be retried
from the stage where it stopped.

Stages that only improve the result (AI readings, separation, the LRC offset)
never fail a task: they are skipped with a warning shown on the task.
"""

from __future__ import annotations

import json
import re
import shutil
import statistics
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from pydantic import Field

from . import service as S
from . import settings as app_settings
from .interfaces import CancelToken, Cancelled
from .models import _Base, new_id, utcnow
from .project.jobs import run_heavy
from .project.store import atomic_write_text

TaskStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "interrupted"]
StageStatus = Literal["pending", "running", "done", "skipped", "failed"]

STAGES: list[tuple[str, str, float]] = [  # key, label, share of the progress bar
    ("import", "导入视频", 0.05),
    ("lyrics", "获取歌词", 0.03),
    ("readings", "AI 注音", 0.12),
    ("separate", "人声分离", 0.35),
    ("calibrate", "LRC 校准", 0.08),
    ("align", "对齐", 0.17),
    ("export", "生成视频", 0.20),
]
_LABEL = {k: label for k, label, _ in STAGES}
_WEIGHT = {k: w for k, _, w in STAGES}


class Stage(_Base):
    key: str
    label: str
    status: StageStatus = "pending"
    progress: float = 0.0
    message: str = ""


class PipelineTask(_Base):
    id: str = Field(default_factory=lambda: new_id("t"))
    created: str = Field(default_factory=utcnow)
    finished: Optional[str] = None
    name: str = ""
    mode: Literal["plain", "lrc"] = "lrc"
    media_filename: str = ""
    lyrics_kind: Literal["link", "text"] = "text"
    lyrics_input: str = ""
    status: TaskStatus = "queued"
    project_id: Optional[str] = None
    stages: list[Stage] = Field(default_factory=list)
    progress: float = 0.0
    message: str = ""
    error: Optional[str] = None
    detail: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)
    outputs: dict[str, Any] = Field(default_factory=dict)

    def stage(self, key: str) -> Stage:
        return next(s for s in self.stages if s.key == key)


def is_music_link(text: str) -> bool:
    """A pasted music link / share text, as opposed to lyrics."""
    from .lyrics.fetch.links import _EXPLICIT, extract_urls

    t = text.strip()
    if _EXPLICIT.match(t):
        return True
    lines = [ln for ln in t.splitlines() if ln.strip()]
    return bool(extract_urls(t)) and len(lines) <= 3 and not re.search(r"^\s*\[\d+:\d+", t, re.M)


# ------------------------------------------------------------------------------------------ queue


class TaskQueue:
    """Runs tasks one after another in a worker thread; state is saved to disk."""

    def __init__(self, ws: "S.Workspace") -> None:
        self.ws = ws
        self.dir = Path(ws.root) / ".tasks"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._wake = threading.Condition(self._lock)
        self._cancel: dict[str, CancelToken] = {}
        self.tasks: list[PipelineTask] = self._load()
        self._thread = threading.Thread(target=self._worker, name="kara-tasks", daemon=True)
        self._stop = False
        self._thread.start()

    # ---- persistence
    def _file(self) -> Path:
        return self.dir / "tasks.json"

    def _load(self) -> list[PipelineTask]:
        try:
            raw = json.loads(self._file().read_text(encoding="utf-8"))
            tasks = [PipelineTask.model_validate(t) for t in raw]
        except FileNotFoundError:
            return []
        except Exception:
            return []
        for t in tasks:
            if t.status == "running":  # the app stopped while it ran
                t.status = "interrupted"
                t.message = "应用重启时中断，可以重试"
                for s in t.stages:
                    if s.status == "running":
                        s.status = "pending"
        return tasks

    def _save(self) -> None:
        with self._lock:
            data = [t.model_dump(mode="json") for t in self.tasks]
        atomic_write_text(self._file(), json.dumps(data, ensure_ascii=False))

    def media_path(self, task: PipelineTask) -> Path:
        return self.dir / task.id / task.media_filename

    # ---- public API
    def list(self) -> list[dict]:
        with self._lock:
            return [t.model_dump(mode="json") for t in reversed(self.tasks)]

    def get(self, task_id: str) -> PipelineTask:
        with self._lock:
            for t in self.tasks:
                if t.id == task_id:
                    return t
        raise KeyError(task_id)

    def add(self, *, media: Path, filename: str, lyrics: str, mode: str, name: str = "") -> PipelineTask:
        lyrics = lyrics.strip()
        if not lyrics:
            raise S.ServiceError("请粘贴音乐链接或歌词")
        if mode not in ("plain", "lrc"):
            raise S.ServiceError("模式只能是 plain 或 lrc")
        safe = Path(filename).name or "media"
        t = PipelineTask(name=name.strip(), mode=mode, media_filename=safe,  # type: ignore[arg-type]
                         lyrics_kind="link" if is_music_link(lyrics) else "text", lyrics_input=lyrics,
                         stages=[Stage(key=k, label=label) for k, label, _ in STAGES])
        if not t.name and t.lyrics_kind == "text":
            t.name = Path(safe).stem
        dest = self.dir / t.id
        dest.mkdir(parents=True, exist_ok=True)
        shutil.move(str(media), dest / safe)
        with self._lock:
            self.tasks.append(t)
            self._wake.notify_all()
        self._save()
        return t

    def cancel(self, task_id: str) -> PipelineTask:
        t = self.get(task_id)
        with self._lock:
            if t.status == "queued":
                t.status, t.message, t.finished = "cancelled", "已取消", utcnow()
            elif t.status == "running" and task_id in self._cancel:
                self._cancel[task_id].cancel()
                t.message = "正在取消…"
        self._save()
        return t

    def retry(self, task_id: str) -> PipelineTask:
        t = self.get(task_id)
        with self._lock:
            if t.status not in ("failed", "cancelled", "interrupted"):
                raise S.ServiceError("只有失败、取消或中断的任务可以重试")
            for s in t.stages:
                if s.status in ("failed", "running", "skipped"):  # optional stages get another chance
                    s.status, s.progress, s.message = "pending", 0.0, ""
            t.status, t.error, t.detail, t.message, t.finished = "queued", None, None, "等待开始", None
            self._wake.notify_all()
        self._save()
        return t

    def remove(self, task_id: str) -> None:
        t = self.get(task_id)
        if t.status == "running":
            raise S.ServiceError("任务正在运行，请先取消")
        with self._lock:
            self.tasks = [x for x in self.tasks if x.id != task_id]
        shutil.rmtree(self.dir / task_id, ignore_errors=True)  # staged upload only; the project stays
        self._save()

    def shutdown(self) -> None:
        with self._lock:
            self._stop = True
            for c in self._cancel.values():
                c.cancel()
            self._wake.notify_all()

    # ---- worker
    def _next(self) -> Optional[PipelineTask]:
        return next((t for t in self.tasks if t.status == "queued"), None)

    def _worker(self) -> None:
        while True:
            with self._lock:
                while not self._stop and self._next() is None:
                    self._wake.wait(timeout=5)
                if self._stop:
                    return
                task = self._next()
                assert task is not None
                task.status, task.message = "running", "开始"
                token = CancelToken()
                self._cancel[task.id] = token
            self._save()
            try:
                run_task(self, task, token)
                task.status, task.message = "succeeded", "完成"
                task.progress = 1.0
            except Cancelled:
                task.status, task.message = "cancelled", "已取消"
                self._mark_running_stage(task, "pending")
            except Exception as e:  # report the real reason on the task
                task.status = "failed"
                task.error = str(e) if isinstance(e, S.ServiceError) else f"{type(e).__name__}: {e}"
                task.detail = traceback.format_exc(limit=8)
                task.message = "失败"
                self._mark_running_stage(task, "failed", task.error)
            finally:
                task.finished = utcnow()
                with self._lock:
                    self._cancel.pop(task.id, None)
                self._save()

    @staticmethod
    def _mark_running_stage(task: PipelineTask, status: StageStatus, message: str = "") -> None:
        for s in task.stages:
            if s.status == "running":
                s.status = status
                if message:
                    s.message = message


# ------------------------------------------------------------------------------------------ stages


def run_task(q: TaskQueue, task: PipelineTask, cancel: CancelToken) -> None:
    cfg = app_settings.load()
    last_save = [0.0]

    def save(force: bool = False) -> None:
        now = time.time()
        if force or now - last_save[0] > 1.0:
            last_save[0] = now
            q._save()

    def overall() -> None:
        done = sum(_WEIGHT[s.key] * (1.0 if s.status in ("done", "skipped") else s.progress) for s in task.stages)
        task.progress = round(min(0.999, done / sum(_WEIGHT.values())), 4)

    for key, _label, _w in STAGES:
        st = task.stage(key)
        if st.status in ("done", "skipped"):
            continue
        cancel.check()
        st.status, st.progress, st.message = "running", 0.0, ""
        task.message = st.label
        overall()
        save(True)

        def progress(frac: float, message: str = "", st=st) -> None:
            st.progress = max(0.0, min(1.0, float(frac)))
            if message:
                st.message = message
                task.message = f"{st.label} · {message}"
            overall()
            save()
            cancel.check()

        outcome = STAGE_FUNCS[key](q, task, cfg, cancel, progress)
        st.status = "skipped" if outcome == "skipped" else "done"
        st.progress = 1.0
        # keep a result note ("48 行", "整体偏移 -350 ms"), drop the last progress text
        st.message = outcome if isinstance(outcome, str) and outcome not in ("skipped", "done") else ""
        overall()
        save(True)


def _handle(q: TaskQueue, task: PipelineTask) -> "S.ProjectHandle":
    if not task.project_id:
        raise S.ServiceError("任务还没有项目")
    return q.ws.get(task.project_id)


def _warn(task: PipelineTask, text: str) -> None:
    if text not in task.warnings:
        task.warnings.append(text)


def stage_import(q, task, cfg, cancel, progress):
    if task.project_id:
        try:
            h = q.ws.get(task.project_id)
            if h.project.asset("original") is not None:
                return "done"
        except Exception:
            task.project_id = None
    src = q.media_path(task)
    if not src.exists():
        raise S.ServiceError("上传的文件已不存在，请重新添加任务")
    h = q.ws.get(task.project_id) if task.project_id else q.ws.create(task.name or Path(src).stem, task.mode)
    task.project_id = h.project.id
    progress(0.2, "读取视频 / 音频")
    from .audio.io import validate_upload

    with open(src, "rb") as f:
        validate_upload(src.name, f.read(64), src.stat().st_size, 8 * 1024**3)
    S.add_media(h, src, "original", filename=task.media_filename)
    with h.lock:
        h.project.mode = task.mode
        h.save()
    return "done"


def stage_lyrics(q, task, cfg, cancel, progress):
    h = _handle(q, task)
    if task.lyrics_kind == "link":
        progress(0.2, "从音乐平台获取歌词")
        try:
            got = S.fetch_link(task.lyrics_input)
        except Exception as e:
            raise S.ServiceError(f"无法从链接获取歌词：{e}") from e
        if got["kind"] != "song":
            raise S.ServiceError("这是专辑或歌单链接，请粘贴单曲链接")
        song = got["song"]
        pv = S.parse_from_song(h, song["platform"], song["song_id"])
        if pv.get("error") and task.mode == "lrc":
            # the platform only has lyrics without times: continue in plain mode
            _warn(task, f"{pv['error']}；已改用普通模式")
            S.update_settings(h, mode="plain")
            task.mode = "plain"
            pv = S.parse_from_song(h, song["platform"], song["song_id"])
        title = song.get("title") or ""
        artists = song.get("artists") or []
        if not task.name and title:
            task.name = title + (f" - {', '.join(artists)}" if artists else "")
            S.update_settings(h, name=task.name)
    else:
        pv = S.parse_lyrics(h, task.lyrics_input, origin="paste")
        if pv.get("error") and task.mode == "lrc":
            _warn(task, "粘贴的歌词没有时间标签；已改用普通模式")
            S.update_settings(h, mode="plain")
            task.mode = "plain"
            pv = S.parse_lyrics(h, task.lyrics_input, origin="paste", mode="plain")
    if pv.get("error") or not pv.get("preview_id"):
        raise S.ServiceError(pv.get("error") or "无法解析歌词")
    for w in pv.get("warnings") or []:
        _warn(task, w)
    progress(0.7, "整理歌词与读音")
    S.apply_lyrics(h, pv["preview_id"])
    if not h.project.lyrics.sung_lines():
        raise S.ServiceError("歌词里没有可以演唱的行")
    return f"{len(h.project.lyrics.sung_lines())} 行"


def stage_readings(q, task, cfg, cancel, progress):
    if not cfg.simple.ai_readings or cfg.ai.provider == "none":
        return "skipped"
    h = _handle(q, task)
    try:
        out = S.ai_auto(h, None, cfg=cfg.ai, cancel=cancel, progress=progress)
        summary = S.ai_apply(h, out["report_id"], None)
    except Cancelled:
        raise
    except Exception as e:  # rule readings are still usable
        _warn(task, f"AI 注音失败，使用规则读音：{e}")
        return "skipped"
    rep = out["report"]
    bad = [lr["line_id"] for lr in rep.get("lines", []) if lr.get("status") != "ok"]
    if bad:
        _warn(task, f"AI 注音有 {len(bad)} 行未采用（保留规则读音）")
    applied = summary.get("applied", []) if isinstance(summary, dict) else []
    return f"更新 {len(applied)} 行"


def stage_separate(q, task, cfg, cancel, progress):
    h = _handle(q, task)
    if not cfg.simple.separate:
        return "skipped"
    try:
        from .audio.separation import ensure_available

        ensure_available()
    except Exception:
        _warn(task, "未安装人声分离组件：使用原曲对齐，也无法生成降低人声的视频")
        return "skipped"
    try:
        run_heavy(lambda: S.run_separation(h, cfg.simple.separation_preset, cancel=cancel, progress=progress,
                                           device=cfg.simple.separation_device),
                  lambda m: progress(0.0, m), cancel)
    except Cancelled:
        raise
    except Exception as e:
        _warn(task, f"人声分离失败，使用原曲对齐：{e}")
        return "skipped"
    return "done"


def _audio_role(h) -> str:
    return "vocals" if h.project.asset("vocals") is not None else "original"


def stage_calibrate(q, task, cfg, cancel, progress):
    h = _handle(q, task)
    if h.project.mode != "lrc":
        return "skipped"
    try:
        info = run_heavy(lambda: estimate_lrc_shift(h, cancel=cancel, progress=progress),
                         lambda m: progress(0.0, m), cancel)
    except Cancelled:
        raise
    except Exception as e:
        _warn(task, f"LRC 自动校准失败，按原始时间对齐：{e}")
        return "skipped"
    if info["agree"] < 0.5:
        _warn(task, f"LRC 时间与音频对不上（只有 {info['agree']:.0%} 的行一致），可能不是同一版本；已改用普通模式")
        S.update_settings(h, mode="plain")
        return "改用普通模式"
    return f"整体偏移 {info['shift_ms']:+d} ms"


def estimate_lrc_shift(h: "S.ProjectHandle", *, cancel: Optional[CancelToken] = None,
                       progress: Optional[Callable[[float, str], None]] = None) -> dict:
    """Estimate the LRC's global offset without anyone marking a first onset.

    A plain-mode alignment (on a throw-away copy of the project) gives each
    line's sung start; the global shift is the median of (sung start − LRC
    time).  Also returns the share of lines that agree within 0.7 s, which
    tells whether the LRC fits this recording at all.  The shift is saved as
    the project's calibration.
    """
    import copy

    from .align import calibration as C

    with h.lock:
        tmp = S.ProjectHandle(h.dir, copy.deepcopy(h.project))
    tmp.save = lambda: None  # type: ignore[method-assign]
    tmp.project.mode = "plain"
    tmp.project.results, tmp.project.active_result_id = [], None
    r = S.run_align(tmp, audio_role=_audio_role(h), cancel=cancel, progress=progress)
    starts = {lt.line_id: lt.start_ms for lt in r.lines if lt.start_ms is not None}
    diffs = []
    doc = h.project.lyrics
    for ln in doc.sung_lines():
        b = C.base_ms(doc, ln)
        if b is not None and ln.anchor is None and ln.id in starts:
            diffs.append(starts[ln.id] - b)
    if len(diffs) < 3:
        raise S.ServiceError("带时间的行太少，无法估计偏移")
    shift = int(round(statistics.median(diffs)))
    agree = sum(1 for d in diffs if abs(d - shift) <= 700) / len(diffs)
    if agree >= 0.5:
        with h.lock:
            h.project.calibration = C.set_user_shift(h.project.calibration, shift, h.project.lyrics)
            h.save()
    return {"shift_ms": shift, "agree": agree, "lines": len(diffs)}


def stage_align(q, task, cfg, cancel, progress):
    h = _handle(q, task)
    role = _audio_role(h)
    S.update_settings(h, config={"audio_role": role})

    def align():
        try:
            return S.run_align(h, audio_role=role, cancel=cancel, progress=progress)
        except S.ServiceError as e:
            if h.project.mode != "lrc":
                raise
            # LRC times that cannot be used (e.g. outside the audio): align without them
            _warn(task, f"LRC 时间无法使用（{e}）；已改用普通模式")
            S.update_settings(h, mode="plain")
            return S.run_align(h, audio_role=role, cancel=cancel, progress=progress)

    r = run_heavy(align, lambda m: progress(0.0, m), cancel)
    warns = [i for i in r.issues if i.severity in ("warning", "error") and i.code in ("unit_in_rest", "line_gap")]
    if warns:
        _warn(task, f"有 {len(warns)} 处可能需要人工检查（点开任务在“人工检查”中查看）")
    return f"{len(r.units)} 个发音单元"


def apply_karaoke_settings(h: "S.ProjectHandle", simple: "app_settings.SimpleSettings") -> None:
    """The project gets the simple mode's complete subtitle style."""
    style = simple.karaoke.model_copy(deep=True)
    style.output.vocal_keep_pct = simple.vocal_keep_pct
    S.set_karaoke_style(h, style.model_dump(mode="json"))


def stage_export(q, task, cfg, cancel, progress):
    h = _handle(q, task)
    apply_karaoke_settings(h, cfg.simple)
    if not cfg.simple.auto_export:
        return "skipped"
    audio = cfg.simple.video_audio
    if audio == "mix" and (h.project.asset("vocals") is None or h.project.asset("instrumental") is None):
        _warn(task, "没有人声分轨，视频使用原声")
        audio = "original"
    out = run_heavy(lambda: S.karaoke_burn(h, background="auto", audio=audio, quality=cfg.simple.quality,
                                           vocal_keep_pct=cfg.simple.vocal_keep_pct,
                                           cancel=cancel, progress=progress),
                    lambda m: progress(0.0, m), cancel)
    for w in out.get("warnings") or []:
        if "停顿" in w:
            _warn(task, w)
    task.outputs["video"] = {"filename": out["filename"],
                             "url": f"/api/projects/{h.project.id}/exports/{out['filename']}"}
    return "done"


STAGE_FUNCS: dict[str, Callable[..., Any]] = {
    "import": stage_import, "lyrics": stage_lyrics, "readings": stage_readings, "separate": stage_separate,
    "calibrate": stage_calibrate, "align": stage_align, "export": stage_export,
}
