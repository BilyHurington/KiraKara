"""Simple mode: one task per song, run start to finish in a queue.

A task turns (video or audio, music link or pasted lyrics, mode) into a karaoke
video with the app settings::

    import → lyrics → LRC offset (confirmed by the user) → AI readings → separation → align → video

Each stage reuses the service functions of the detailed mode, so a finished
(or failed) task is an ordinary project that can be opened and refined there.
Tasks run one at a time; heavy stages share :data:`HEAVY_LOCK` with the
detailed mode's jobs.  The queue is saved in ``<workspace>/.tasks/tasks.json``;
after a restart, queued tasks continue and an interrupted one can be retried
from the stage where it stopped.

Stages that only improve the result (AI readings, separation) never fail a
task: they are skipped with a warning shown on the task.

The first stages (import, lyrics, offset) are quick and run at once in a
separate preparation lane, even while another task holds the heavy worker.
In LRC mode the task then stops for the user to mark where the first line is
sung (the audio of a video is often not the recording the LRC was timed on),
so everything that needs a person happens right after the task is added; the
rest (AI readings, separation, alignment, video) runs unattended in order.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from pydantic import Field

from . import service as S
from . import settings as app_settings
from .interfaces import CancelToken, Cancelled
from .models import KaraokeStyle, _Base, new_id, utcnow
from .project.jobs import run_heavy
from .project.store import atomic_write_text

TaskStatus = Literal["preparing", "queued", "running", "waiting", "succeeded", "failed", "cancelled", "interrupted"]
StageStatus = Literal["pending", "running", "waiting", "done", "skipped", "failed"]


class WaitForUser(Exception):
    """A stage needs a decision from the user; the task waits without failing."""

STAGES: list[tuple[str, str, float]] = [  # key, label, share of the progress bar
    ("import", "导入视频", 0.05),
    ("lyrics", "获取歌词", 0.03),
    ("calibrate", "确认偏移", 0.02),
    ("readings", "AI 注音", 0.12),
    ("separate", "人声分离", 0.38),
    ("align", "对齐", 0.20),
    ("export", "生成视频", 0.20),
]
PREP_STAGES = ("import", "lyrics", "calibrate")  # quick; run as soon as the task is added
_LABEL = {k: label for k, label, _ in STAGES}
_WEIGHT = {k: w for k, _, w in STAGES}


class Stage(_Base):
    key: str
    label: str
    status: StageStatus = "pending"
    progress: float = 0.0
    message: str = ""


class TaskVideo(_Base):
    """The video settings of one task, fixed when it is added."""

    auto_export: bool = True
    video_audio: Literal["original", "mix", "none"] = "original"
    vocal_keep_pct: float = 20.0
    quality: Literal["standard", "high"] = "standard"


class TaskProcessing(_Base):
    """How the task is processed, fixed when it is added (AI readings, vocal separation)."""

    ai_provider: Optional[str] = None  # None: tasks from before this was recorded (today's setting)
    ai_model: str = ""
    ai_readings: bool = True
    separate: bool = True
    separation_preset: str = "melband-roformer"
    separation_device: Literal["auto", "cpu"] = "auto"


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
    # LRC offset to confirm: the suggestion shown to the user (see stage_calibrate)
    calibration: Optional[dict[str, Any]] = None
    calibration_confirmed: bool = False
    # subtitle style and video settings, fixed when the task is added (queued tasks never pick up
    # later changes to the settings); None on tasks from before this existed
    karaoke: Optional[KaraokeStyle] = None
    video: Optional[TaskVideo] = None
    processing: Optional[TaskProcessing] = None
    style_label: str = ""
    style_colors: list[str] = Field(default_factory=list)
    style_applied: bool = False
    warning_stage: dict[str, str] = Field(default_factory=dict)  # warning text -> stage that raised it
    current_stage: str = ""
    project_deleted: bool = False  # the project was deleted in the detailed mode

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
        self._save_lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._cancel: dict[str, CancelToken] = {}
        self.tasks: list[PipelineTask] = self._load()
        from concurrent.futures import ThreadPoolExecutor

        self._prep_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kara-prep")
        self._thread = threading.Thread(target=self._worker, name="kara-tasks", daemon=True)
        self._stop = False
        self._thread.start()
        for t in self.tasks:
            if t.status == "preparing":
                self._prep_pool.submit(self._prepare, t)

    # ---- persistence
    def _file(self) -> Path:
        return self.dir / "tasks.json"

    def _load(self) -> list[PipelineTask]:
        try:
            raw = json.loads(self._file().read_text(encoding="utf-8"))
            tasks = [PipelineTask.model_validate(t) for t in raw]
        except FileNotFoundError:
            return []
        except Exception:  # unreadable (e.g. written by another version): keep it for inspection, start empty
            try:
                self._file().replace(self._file().with_suffix(".broken.json"))
            except OSError:
                pass
            return []
        for t in tasks:
            if t.status == "running" and _first_open_stage(t) not in PREP_STAGES:  # stopped while it ran
                t.status = "interrupted"
                t.message = "应用重启时中断，可以重试"
                for s in t.stages:
                    if s.status == "running":
                        s.status = "pending"
            elif t.status == "running":  # was still preparing: simply prepare again
                t.status = "preparing"
                for s in t.stages:
                    if s.status == "running":
                        s.status = "pending"
        return tasks

    def _save(self) -> None:
        # one saver at a time, each writing what is current then: an older snapshot never lands last
        with self._save_lock:
            with self._lock:
                data = [t.model_dump(mode="json") for t in self.tasks]
            atomic_write_text(self._file(), json.dumps(data, ensure_ascii=False))

    def media_path(self, task: PipelineTask) -> Path:
        return self.dir / task.id / task.media_filename

    # ---- public API
    def list(self) -> list[dict]:
        with self._lock:
            tasks = list(reversed(self.tasks))
        out = []
        for t in tasks:
            d = t.model_dump(mode="json")
            if t.status == "waiting" and t.calibration and t.project_id:
                try:  # the project's current offset, if one was set meanwhile (detailed mode)
                    shift = self.ws.get(t.project_id).project.calibration.user_shift_ms
                except Exception:
                    shift = 0
                d["calibration"]["current_ms"] = t.calibration["lrc_ms"] + shift if shift else None
            out.append(d)
        return out

    def forget_project(self, pid: str) -> None:
        """The project was deleted: its tasks keep their history but lose links to it."""
        with self._lock:
            for t in self.tasks:
                if t.project_id == pid:
                    t.project_deleted = True
                    t.outputs = {}
                    t.message = "项目已删除"
        self._save()

    def get(self, task_id: str) -> PipelineTask:
        with self._lock:
            for t in self.tasks:
                if t.id == task_id:
                    return t
        raise KeyError(task_id)

    def add(self, *, media: Path, filename: str, lyrics: str, mode: str, name: str = "",
            style: Optional[dict] = None) -> PipelineTask:
        """``style``: the task's subtitle choices (TaskStyleOptions); None = the last ones used."""
        lyrics = lyrics.strip()
        if not lyrics:
            raise S.ServiceError("请粘贴音乐链接或歌词")
        if mode not in ("plain", "lrc"):
            raise S.ServiceError("模式只能是 plain 或 lrc")
        cfg = app_settings.load()
        try:
            opts = app_settings.TaskStyleOptions.model_validate(style) if style is not None else cfg.simple.task_style
        except Exception as e:
            raise S.ServiceError(f"字幕样式选项无效：{e}") from e
        karaoke, label, colors = resolve_task_style(cfg.simple, opts)
        video = TaskVideo(auto_export=cfg.simple.auto_export, video_audio=opts.video_audio or cfg.simple.video_audio,
                          vocal_keep_pct=cfg.simple.vocal_keep_pct if opts.vocal_keep_pct is None else opts.vocal_keep_pct,
                          quality=cfg.simple.quality)
        karaoke.output.vocal_keep_pct = video.vocal_keep_pct
        safe = Path(filename).name or "media"
        t = PipelineTask(name=name.strip(), mode=mode, media_filename=safe,  # type: ignore[arg-type]
                         lyrics_kind="link" if is_music_link(lyrics) else "text", lyrics_input=lyrics,
                         stages=[Stage(key=k, label=label) for k, label, _ in STAGES],
                         karaoke=karaoke, video=video, style_label=label, style_colors=colors,
                         processing=TaskProcessing(ai_provider=cfg.ai.provider, ai_model=cfg.ai.model,
                                                   ai_readings=cfg.simple.ai_readings, separate=cfg.simple.separate,
                                                   separation_preset=cfg.simple.separation_preset,
                                                   separation_device=cfg.simple.separation_device))
        if style is not None:  # the next task starts from these choices
            app_settings.update({"simple": {"task_style": opts.model_dump(mode="json")}})
        if not t.name and t.lyrics_kind == "text":
            t.name = Path(safe).stem
        dest = self.dir / t.id
        dest.mkdir(parents=True, exist_ok=True)
        shutil.move(str(media), dest / safe)
        t.status, t.message = "preparing", "读取视频和歌词"
        with self._lock:
            self.tasks.append(t)
        self._save()
        self._prep_pool.submit(self._prepare, t)
        return t

    def active_for_project(self, pid: str) -> Optional[PipelineTask]:
        """The unfinished task working on a project, if any (the detailed mode must not run heavy
        jobs on it meanwhile)."""
        with self._lock:
            return next((t for t in self.tasks if t.project_id == pid
                         and t.status in ("preparing", "waiting", "queued", "running")), None)

    def _prepare(self, task: PipelineTask) -> None:
        """Quick stages right away; then wait for the user (LRC) or join the queue."""
        token = CancelToken()
        with self._lock:
            if task.status != "preparing":
                return
            self._cancel[task.id] = token
        self._run(task, token, PREP_STAGES, done_status="queued")
        with self._lock:
            self._wake.notify_all()

    def cancel(self, task_id: str) -> PipelineTask:
        t = self.get(task_id)
        with self._lock:
            token = self._cancel.get(task_id)
            if token is not None:  # picked up by a worker (even if it has not marked it running yet)
                token.cancel()
                t.message = "正在取消…"
            if t.status in ("queued", "waiting") or (t.status == "preparing" and token is None):
                t.status, t.message, t.finished = "cancelled", "已取消", utcnow()
                for s in t.stages:
                    if s.status == "waiting":
                        s.status = "pending"
        self._save()
        return t

    def retry(self, task_id: str) -> PipelineTask:
        t = self.get(task_id)
        with self._lock:
            if t.status not in ("failed", "cancelled", "interrupted"):
                raise S.ServiceError("只有失败、取消或中断的任务可以重试")
            if t.project_deleted:
                raise S.ServiceError("这个任务的项目已被删除，无法重试；请重新添加任务")
            again = set()
            for s in t.stages:
                if s.status in ("failed", "running", "skipped", "waiting"):  # optional stages get another chance
                    s.status, s.progress, s.message = "pending", 0.0, ""
                    again.add(s.key)
            # warnings from the stages that run again are dropped (they are raised again if still true)
            t.warnings = [w for w in t.warnings if t.warning_stage.get(w) not in again]
            t.warning_stage = {w: k for w, k in t.warning_stage.items() if w in t.warnings}
            prep = _first_open_stage(t) in PREP_STAGES
            t.status = "preparing" if prep else "queued"
            t.error, t.detail, t.message, t.finished = None, None, "等待开始", None
            self._wake.notify_all()
        self._save()
        if prep:
            self._prep_pool.submit(self._prepare, t)
        return t

    def confirm_calibration(self, task_id: str, *, marked_ms: Optional[int] = None, plain: bool = False) -> PipelineTask:
        """The user confirmed where the first line starts (or chose not to use the LRC times)."""
        t = self.get(task_id)
        with self._lock:  # a cancel must not slip in between the check and the change
            if t.status != "waiting" or not t.calibration or not t.project_id:
                raise S.ServiceError("这个任务现在不需要确认偏移")
            h = self.ws.get(t.project_id)
            st = t.stage("calibrate")
            if plain:
                S.update_settings(h, mode="plain")
                t.mode = "plain"
                st.message = "改用普通模式"
            else:
                if marked_ms is None:
                    raise S.ServiceError("请标记第一句开始唱的位置")
                line_id = t.calibration["line_id"]
                if not any(ln.id == line_id for ln in h.project.lyrics.lines):
                    # the lyrics were edited in the detailed mode meanwhile: ask about the new first line
                    t.calibration = calibration_request(h)
                    stale = True
                else:
                    stale = False
                    try:
                        S.calibration_op(h, "mark", line_id=line_id, marked_ms=int(marked_ms))
                    except ValueError as e:
                        raise S.ServiceError(str(e)) from e
                    shift = h.project.calibration.user_shift_ms
                    st.message = f"偏移 {shift:+d} ms（已确认）"
                    t.calibration["confirmed_ms"] = int(marked_ms)
            if plain or not stale:
                st.status, st.progress = "done", 1.0
                t.calibration_confirmed = True
                t.status, t.message = "queued", "等待继续"
                self._wake.notify_all()
        self._save()
        if not plain and stale:
            raise S.ServiceError("歌词在详细模式中改过：已重新选出要确认的第一句，请重新标记后确认")
        return t

    def remove(self, task_id: str) -> None:
        t = self.get(task_id)
        if t.status == "running" or (t.status == "preparing" and task_id in self._cancel):
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
        self._prep_pool.shutdown(wait=False, cancel_futures=True)

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
                token = CancelToken()
                self._cancel[task.id] = token
                task.status = "running"  # taken: a cancel from now on goes through the token
            self._run(task, token, None, done_status="succeeded")

    def _run(self, task: PipelineTask, token: CancelToken, keys: Optional[tuple[str, ...]], *,
             done_status: TaskStatus) -> None:
        with self._lock:
            dropped = task.status == "cancelled" or token.cancelled  # cancelled while being picked up
            if dropped:
                self._cancel.pop(task.id, None)
                task.status, task.message = "cancelled", "已取消"
                task.finished = task.finished or utcnow()
            else:
                task.status, task.message = ("preparing" if keys else "running"), "开始"
        self._save()  # never while holding self._lock (the save lock is always taken first)
        if dropped:
            return
        try:
            run_task(self, task, token, keys)
            with self._lock:
                if token.cancelled:  # cancelled right as the last stage finished
                    raise Cancelled()
                task.status = done_status
                task.message = "完成" if done_status == "succeeded" else "排队中"
                if done_status == "succeeded":
                    task.progress = 1.0
        except WaitForUser as w:
            task.status, task.message = "waiting", str(w)
            for s in task.stages:
                if s.status == "running":
                    s.status, s.message = "waiting", str(w)
        except Cancelled:
            if self._stop:  # the server is shutting down: not the user's cancel
                self._mark_running_stage(task, "pending")
                if keys:  # was preparing: prepared again on the next start
                    task.status, task.message = "preparing", "读取视频和歌词"
                else:
                    task.status, task.message = "interrupted", "服务关闭时中断，可以重试"
            else:
                task.status, task.message = "cancelled", "已取消"
                self._mark_running_stage(task, "pending")
        except Exception as e:  # report the real reason on the task
            task.status = "failed"
            task.error = str(e) if isinstance(e, S.ServiceError) else f"{type(e).__name__}: {e}"
            task.detail = traceback.format_exc(limit=8)
            task.message = "失败"
            self._mark_running_stage(task, "failed", task.error)
        finally:
            if task.status in ("succeeded", "failed", "cancelled"):
                task.finished = utcnow()
            with self._lock:
                if self._cancel.get(task.id) is token:  # a retry may already have registered a new one
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


def _first_open_stage(t: PipelineTask) -> Optional[str]:
    return next((s.key for s in t.stages if s.status not in ("done", "skipped")), None)


def run_task(q: TaskQueue, task: PipelineTask, cancel: CancelToken, keys: Optional[tuple[str, ...]] = None) -> None:
    cfg = app_settings.load()
    if task.processing is not None:  # the choices made when the task was added, not today's settings
        pr = task.processing
        cfg.simple = cfg.simple.model_copy(update=pr.model_dump(exclude={"ai_provider", "ai_model"}))
        if pr.ai_provider is not None:  # keys / URLs stay today's (never copied into the task)
            cfg.ai = cfg.ai.model_copy(update={"provider": pr.ai_provider, "model": pr.ai_model})
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
        if st.status in ("done", "skipped") or (keys is not None and key not in keys):
            continue
        cancel.check()
        st.status, st.progress, st.message = "running", 0.0, ""
        task.message = st.label
        task.current_stage = key
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


def _holder(task: PipelineTask, what: str) -> str:
    return f"极简模式任务「{task.name or task.media_filename}」的{what}"


def _warn(task: PipelineTask, text: str) -> None:
    if text not in task.warnings:
        task.warnings.append(text)
        task.warning_stage[text] = task.current_stage


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
    apply_task_style(h, task)
    # the project keeps its own copy: drop the staged upload (a retry reads the project's assets)
    shutil.rmtree(q.dir / task.id, ignore_errors=True)
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
    n = len(h.project.lyrics.sung_lines())
    paired = pair_translation(h, (pv.get("extra_tracks") or {}).get("translation"))
    if task.karaoke is not None and task.karaoke.translation.enabled and not any(
            (ln.translation or "").strip() for ln in h.project.lyrics.sung_lines()):
        _warn(task, "音乐平台没有提供这首歌的翻译，视频里不会显示翻译" if task.lyrics_kind == "link"
              else "粘贴的歌词没有翻译，视频里不会显示翻译（粘贴网易云 / QQ 音乐链接会自动带上平台的翻译）")
    return f"{n} 行" + (f" · 翻译 {paired} 行" if paired else "")


def pair_translation(h: "S.ProjectHandle", text: Optional[str]) -> int:
    """Store the platform's translation on the lyric lines (shown only if the
    subtitle style turns translations on).  Returns the number of lines paired."""
    if not text or not text.strip():
        return 0
    try:
        prev = S.preview_track(h, text, "translation")
        pairs = [{"line_id": x["line_id"], "text": x["text"]} for x in prev["pairs"] if x.get("text", "").strip()]
        if pairs:
            S.apply_track(h, "translation", pairs)
        return len(pairs)
    except Exception:  # a translation is a bonus, never a reason to fail
        return 0


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
    if _stems_match(h):
        return "已有分轨"  # kept (a retry, or separated in the detailed mode)
    try:
        from .audio.separation import ensure_available

        ensure_available()
    except Exception:
        _warn(task, "未安装人声分离组件：使用原曲对齐，也无法生成降低人声的视频")
        return "skipped"
    try:
        run_heavy(lambda: S.run_separation(h, cfg.simple.separation_preset, cancel=cancel, progress=progress,
                                           device=cfg.simple.separation_device),
                  lambda m: progress(0.0, m), cancel, holder=_holder(task, "人声分离"))
    except Cancelled:
        raise
    except Exception as e:
        _warn(task, f"人声分离失败，使用原曲对齐：{e}")
        return "skipped"
    return "done"


def _stems_match(h) -> bool:
    """Vocals and instrumental exist and were separated from the current original."""
    orig, voc, inst = (h.project.asset(r) for r in ("original", "vocals", "instrumental"))
    return bool(orig and voc and inst and voc.source.parent_sha256 == orig.sha256
                and inst.source.parent_sha256 == orig.sha256)


def _audio_role(h) -> str:
    return "vocals" if _stems_match(h) else "original"


def stage_calibrate(q, task, cfg, cancel, progress):
    """LRC mode: wait for the user to mark where the first timed line is sung."""
    h = _handle(q, task)
    if h.project.mode != "lrc":
        return "skipped"
    if task.calibration_confirmed:
        return task.stage("calibrate").message or "已确认"
    try:
        task.calibration = calibration_request(h)
    except S.ServiceError as e:
        _warn(task, f"LRC 时间无法使用（{e}）；已改用普通模式")
        S.update_settings(h, mode="plain")
        task.mode = "plain"
        return "改用普通模式"
    raise WaitForUser("等待确认开头位置")


def calibration_request(h: "S.ProjectHandle") -> dict:
    """What the confirmation dialog needs: the first timed line, its LRC time,
    a line from the middle to check the result by ear, and the audio to play.
    (No automatic guess here: the vocals are not separated yet.)"""
    from .align import calibration as C

    doc = h.project.lyrics
    timed = [ln for ln in doc.sung_lines() if C.base_ms(doc, ln) is not None and ln.anchor is None]
    if not timed:
        raise S.ServiceError("歌词没有可用的行时间")
    ref = timed[0]
    mid = timed[len(timed) // 2] if len(timed) > 2 else None
    audio = h.project.asset("original")
    return {
        "line_id": ref.id, "line_text": ref.text, "lrc_ms": C.base_ms(doc, ref),
        "lines": [{"id": ln.id, "text": ln.text, "lrc_ms": C.base_ms(doc, ln)} for ln in timed[:3]],
        "check_line": {"id": mid.id, "text": mid.text, "lrc_ms": C.base_ms(doc, mid)} if mid else None,
        "asset_id": audio.id if audio else None, "duration_ms": audio.duration_ms if audio else None,
    }


def _align(q: TaskQueue, task: PipelineTask, h: "S.ProjectHandle", cancel: CancelToken, progress):
    role = _audio_role(h)  # passed to this alignment only; the project's own setting is left alone

    def align():
        try:
            return S.run_align(h, audio_role=role, cancel=cancel, progress=progress)
        except S.LrcTimesError as e:
            if h.project.mode != "lrc":
                raise
            # only unusable LRC times fall back to plain mode; any other error fails the task
            _warn(task, f"LRC 时间无法使用（{e}）；已改用普通模式")
            S.update_settings(h, mode="plain")
            task.mode = "plain"
            return S.run_align(h, audio_role=role, cancel=cancel, progress=progress)

    r = run_heavy(align, lambda m: progress(0.0, m), cancel, holder=_holder(task, "对齐"))
    warns = [i for i in r.issues if i.severity in ("warning", "error") and i.code in ("unit_in_rest", "line_gap")]
    if warns:
        _warn(task, f"有 {len(warns)} 处可能需要人工检查（点开任务在“人工检查”中查看）")
    return r


def stage_align(q, task, cfg, cancel, progress):
    h = _handle(q, task)
    r = h.project.result()
    wanted = h.project.asset(_audio_role(h))
    if r is not None and S.staleness(h.project, r) is None and wanted is not None \
            and r.snapshot.audio_asset_id == wanted.id:
        # a current alignment of the same audio (a retry, or aligned in the detailed mode): keep it and its edits
        return f"已有对齐结果 · {len(r.units)} 个发音单元"
    r = _align(q, task, h, cancel, progress)
    return f"{len(r.units)} 个发音单元"


def resolve_task_style(simple: "app_settings.SimpleSettings",
                       opts: "app_settings.TaskStyleOptions") -> tuple[KaraokeStyle, str, list[str]]:
    """The complete subtitle style for a task's choices: (style, short label, colours for the list)."""
    from .karaoke.styles import StyleError, get_style
    from .karaoke.themes import TEMPLATES, hex_to_rgb, theme_style

    base = simple.karaoke
    if opts.source == "template":
        for c in filter(None, (opts.color, opts.secondary)):
            try:
                hex_to_rgb(c)
            except ValueError as e:
                raise S.ServiceError(str(e)) from e
        style = theme_style(opts.template, opts.color, base, opts.secondary or None)
        label, colors = TEMPLATES[opts.template], [opts.color] + ([opts.secondary] if opts.secondary else [])
    elif opts.source == "saved":
        try:
            style = get_style(opts.saved_id)
        except StyleError as e:
            raise S.ServiceError("选择的预设已不存在，请重新选择字幕样式") from e
        label, colors = style.preset or "预设", [style.text.color_sung]
    else:
        style = base.model_copy(deep=True)
        label, colors = "默认样式", [style.text.color_sung]
    if opts.translation is not None:
        style.translation.enabled = opts.translation
    if opts.song_info is not None:
        style.info.enabled = opts.song_info
    if opts.ruby == "off":
        style.ruby.enabled = False
    elif opts.ruby != "style":
        style.ruby.enabled, style.ruby.script = True, opts.ruby
    if opts.ruby_target and style.ruby.enabled:
        style.ruby.target = opts.ruby_target
    return style, label, colors


def apply_task_style(h: "S.ProjectHandle", task: PipelineTask) -> None:
    """Give the project the task's own subtitle style, once (later edits in the detailed mode stay)."""
    if task.karaoke is not None and not task.style_applied:
        S.set_karaoke_style(h, task.karaoke.model_dump(mode="json"))
        task.style_applied = True


def apply_karaoke_settings(h: "S.ProjectHandle", simple: "app_settings.SimpleSettings") -> None:
    """The project gets the simple mode's complete subtitle style."""
    style = simple.karaoke.model_copy(deep=True)
    style.output.vocal_keep_pct = simple.vocal_keep_pct
    S.set_karaoke_style(h, style.model_dump(mode="json"))


def stage_export(q, task, cfg, cancel, progress):
    h = _handle(q, task)
    if task.karaoke is not None:  # the task's own style (and any edits made to the project since)
        apply_task_style(h, task)
        video = task.video or TaskVideo()
    else:  # a task from before styles were bound to tasks: the settings' style, once
        if not task.style_applied:
            apply_karaoke_settings(h, cfg.simple)
            task.style_applied = True
        video = TaskVideo(auto_export=cfg.simple.auto_export, video_audio=cfg.simple.video_audio,
                          vocal_keep_pct=cfg.simple.vocal_keep_pct, quality=cfg.simple.quality)
    if not video.auto_export:
        return "skipped"
    r = h.project.result()
    if r is None or S.staleness(h.project, r) is not None:  # computed now, not the saved flag
        # e.g. a retry re-ran the AI readings, or the lyrics were edited in the detailed mode:
        # the video must not be burned from an alignment of different lyrics
        progress(0.0, "对齐结果已过期，重新对齐")
        _warn(task, "歌词或读音在对齐后有变化，已重新对齐后再生成视频")
        _align(q, task, h, cancel, lambda f, m="": progress(0.0, m))
    audio = video.video_audio
    if audio == "mix" and (h.project.asset("vocals") is None or h.project.asset("instrumental") is None):
        _warn(task, "没有人声分轨，视频使用原声")
        audio = "original"
    out = run_heavy(lambda: S.karaoke_burn(h, background="auto", audio=audio, quality=video.quality,
                                           vocal_keep_pct=video.vocal_keep_pct, tag=task.id[-6:],
                                           cancel=cancel, progress=progress),
                    lambda m: progress(0.0, m), cancel, holder=_holder(task, "生成视频"))
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
