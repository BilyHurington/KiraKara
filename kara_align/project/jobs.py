"""Minimal in-process job manager with real status, progress and cancel.

Jobs run in worker threads.  A job only publishes its output when it
succeeds; cancelled or failed jobs never produce a completed result.
"""

from __future__ import annotations

import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

from ..interfaces import CancelToken, Cancelled
from ..models import new_id, utcnow

JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]

# model inference, separation and encoding: one at a time across the whole app
# (heavy jobs here and the simple-mode task queue both take it)
HEAVY_LOCK = threading.Lock()
_holder = ""  # what holds HEAVY_LOCK now, for the waiting message


def run_heavy(fn: Callable[[], Any], wait_message: Optional[Callable[[str], None]] = None,
              cancel: Optional[CancelToken] = None, holder: str = "") -> Any:
    """Run ``fn`` holding :data:`HEAVY_LOCK`, reporting what it waits for meanwhile."""
    global _holder
    while not HEAVY_LOCK.acquire(timeout=0.5):
        if wait_message is not None:
            wait_message(f"等待{_holder}完成…" if _holder else "等待其他任务完成…")
        if cancel is not None:
            cancel.check()
    _holder = holder
    try:
        return fn()
    finally:
        _holder = ""
        HEAVY_LOCK.release()


@dataclass
class Job:
    id: str
    kind: str
    project_id: Optional[str]
    status: JobStatus = "queued"
    progress: float = 0.0
    message: str = ""
    error: Optional[str] = None
    detail: Optional[str] = None
    output: Any = None
    created: str = field(default_factory=utcnow)
    finished: Optional[str] = None
    cancel_token: CancelToken = field(default_factory=CancelToken, repr=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "project_id": self.project_id, "status": self.status,
            "progress": round(self.progress, 4), "message": self.message, "error": self.error,
            "created": self.created, "finished": self.finished,
            "output": self.output if isinstance(self.output, (dict, str, int, float, type(None))) else None,
        }


_KIND = {"align": "对齐", "separate": "人声分离", "burn": "字幕烧录", "ai": "AI 注音", "video": "视频导出",
         "calibrate": "自动匹配偏移", "mix": "混音导出"}


class JobManager:
    """Two pools: ``heavy`` (model inference / separation, serialized) and ``light``."""

    def __init__(self, heavy_workers: int = 1, light_workers: int = 2) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._heavy = ThreadPoolExecutor(max_workers=heavy_workers, thread_name_prefix="kara-heavy")
        self._light = ThreadPoolExecutor(max_workers=light_workers, thread_name_prefix="kara-light")

    def submit(self, kind: str, fn: Callable[[Job], Any], *, project_id: Optional[str] = None,
               heavy: bool = True, on_success: Optional[Callable[[Job, Any], Any]] = None) -> Job:
        job = Job(id=new_id("job"), kind=kind, project_id=project_id)
        with self._lock:
            self._jobs[job.id] = job

        def run() -> None:
            if job.cancel_token.cancelled:
                self._finish(job, "cancelled", message="已取消")
                return
            job.status = "running"
            try:
                if heavy:
                    def wait(msg: str) -> None:
                        job.message = msg
                    out = run_heavy(lambda: fn(job), wait, job.cancel_token, holder=f"详细模式的{_KIND.get(kind, kind)}")
                else:
                    out = fn(job)
                job.cancel_token.check()
                if on_success is not None:
                    out = on_success(job, out)
                job.output = out
                self._finish(job, "succeeded", message="完成")
            except Cancelled:
                self._finish(job, "cancelled", message="已取消")
            except Exception as e:  # report the real failure reason
                job.error = f"{type(e).__name__}: {e}"
                job.detail = traceback.format_exc(limit=8)
                self._finish(job, "failed", message="失败")

        (self._heavy if heavy else self._light).submit(run)
        return job

    def _finish(self, job: Job, status: JobStatus, message: str) -> None:
        job.status = status
        job.message = message
        job.finished = utcnow()
        if status == "succeeded":
            job.progress = 1.0

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def list(self, project_id: Optional[str] = None) -> list[Job]:
        jobs = list(self._jobs.values())
        if project_id:
            jobs = [j for j in jobs if j.project_id == project_id]
        return sorted(jobs, key=lambda j: j.created, reverse=True)

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job or job.status in ("succeeded", "failed", "cancelled"):
            return False
        job.cancel_token.cancel()
        if job.status == "queued":
            job.status = "cancelled"
            job.message = "已取消"
            job.finished = utcnow()
        return True

    def shutdown(self) -> None:
        for j in self._jobs.values():
            j.cancel_token.cancel()
        self._heavy.shutdown(wait=False, cancel_futures=True)
        self._light.shutdown(wait=False, cancel_futures=True)


def progress_setter(job: Job) -> Callable[[float, str], None]:
    def set_progress(frac: float, message: str = "") -> None:
        job.progress = max(0.0, min(1.0, float(frac)))
        if message:
            job.message = message
        job.cancel_token.check()
    return set_progress
