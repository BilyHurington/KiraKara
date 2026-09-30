"""Disk space: what the projects, the cache and leftovers take, and cleaning them up.

A project's folder holds its media (the song's audio, the video), the separated stems, a background,
the exported files and the project file.  Anything else is a leftover and never needed again:

- files in ``assets/`` no project entry points to (audio replaced, stems separated again);
- a task's staged upload once the task has imported it (the project keeps its own copy);
- project folders without a project file (a deletion that could not finish, a crash);
- ``.deleted-*`` folders (a deletion that could not remove everything).

The cache (decoded playback audio, waveforms, the model's per-frame output, separation work) is made
again when needed.  Deleting a project also drops the cache files of its audio no other project uses.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Iterable, Optional

from .project import store

# files younger than this are left alone: an upload or import may still be putting them in place
SETTLE_S = 600
STEMS = ("vocals", "instrumental")
CACHE_KINDS = ("playback", "peaks", "emissions", "separation")


def size_of(path: Path) -> int:
    """Bytes of a file, or of everything under a folder (links are not followed)."""
    try:
        if path.is_symlink():
            return 0
        if path.is_file():
            return path.stat().st_size
    except OSError:
        return 0
    total = 0
    for root, dirs, files in os.walk(path, onerror=lambda e: None):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def _old(path: Path, now: float) -> bool:
    try:
        return now - path.stat().st_mtime > SETTLE_S
    except OSError:
        return False


def _remove(path: Path) -> int:
    """Delete a file or folder; the bytes freed (a file in use, e.g. on Windows, stays)."""
    n = size_of(path)
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    return n - (size_of(path) if path.exists() else 0)


# --------------------------------------------------------------------------- projects


def referenced(project) -> set[str]:
    """The files of a project's folder that its entries point to (relative paths)."""
    refs = {a.path for a in project.audio if a.path}
    for extra in (project.video, getattr(project, "background", None)):
        if extra is not None and extra.path:
            refs.add(extra.path)
    return refs


def unused_assets(h, now: Optional[float] = None) -> list[Path]:
    now = time.time() if now is None else now
    d = h.dir / "assets"
    if not d.is_dir():
        return []
    refs = referenced(h.project)
    return [f for f in d.iterdir() if f"assets/{f.name}" not in refs and _old(f, now)]


def project_usage(h) -> dict:
    """The parts of one project's folder, in bytes."""
    p = h.project
    part = {"media": 0, "stems": 0, "background": 0, "exports": 0, "unused": 0, "other": 0}
    counted: set[str] = set()

    def add(key: str, rel: Optional[str]) -> None:
        if rel and rel not in counted:
            counted.add(rel)
            part[key] += size_of(h.dir / rel)

    for a in p.audio:
        add("stems" if a.role in STEMS else "media", a.path)
    if p.video is not None:
        add("media", p.video.path)
    if getattr(p, "background", None) is not None:
        add("background", p.background.path)
    exports = []
    ex = h.dir / "exports"
    if ex.is_dir():
        for f in ex.iterdir():
            if f.is_file() and not f.name.startswith("."):
                exports.append({"filename": f.name, "size": f.stat().st_size, "modified": f.stat().st_mtime})
        part["exports"] = size_of(ex)
    part["unused"] = sum(size_of(f) for f in unused_assets(h))
    total = size_of(h.dir)
    part["other"] = max(0, total - sum(part.values()))
    exports.sort(key=lambda e: e["modified"], reverse=True)
    return {"size": total, "parts": part, "exports": exports,
            "stems": any(a.role in STEMS for a in p.audio)}


def drop_stems(h) -> int:
    """Remove the separated stems (made again by separating); the caller holds no lock."""
    with h.lock:
        gone = [a for a in h.project.audio if a.role in STEMS]
        if not gone:
            return 0
        h.project.audio = [a for a in h.project.audio if a.role not in STEMS]
        h.save()
        keep = referenced(h.project)
    return sum(_remove(h.dir / a.path) for a in gone if a.path and a.path not in keep and (h.dir / a.path).exists())


def drop_exports(h, names: Optional[Iterable[str]] = None) -> int:
    """Remove exported files (all of them, or the ones named)."""
    ex = h.dir / "exports"
    if not ex.is_dir():
        return 0
    wanted = None if names is None else {Path(n).name for n in names}
    freed = 0
    for f in list(ex.iterdir()):
        if f.is_file() and not f.name.startswith(".") and (wanted is None or f.name in wanted):
            freed += _remove(f)
    return freed


def drop_replaced(h, old_paths: Iterable[Optional[str]]) -> None:
    """Files an entry pointed to before it was replaced, when nothing points to them any more."""
    keep = referenced(h.project)
    for rel in {p for p in old_paths if p}:
        if rel not in keep:
            try:
                path = store.asset_abspath(h.dir, rel)
                if path is not None and path.is_file():
                    path.unlink()
            except (OSError, store.ProjectError):
                pass


# --------------------------------------------------------------------------- cache


def cache_usage() -> dict:
    root = store.cache_dir()
    parts = {k: size_of(root / k) for k in CACHE_KINDS}
    total = size_of(root)
    parts["other"] = max(0, total - sum(parts.values()))
    return {"size": total, "parts": parts}


def clear_cache() -> int:
    root = store.cache_dir()
    freed = 0
    for child in list(root.iterdir()):
        freed += _remove(child)
    return freed


def drop_cache_of(shas: Iterable[str], still_used: set[str]) -> int:
    """Cache files named after audio no project uses any more (after a project was deleted)."""
    freed = 0
    for sha in set(shas) - still_used:
        if not store.is_sha256(sha):
            continue
        for kind in ("playback", "peaks"):
            for f in store.cache_dir(kind).glob(f"{sha}*"):
                freed += _remove(f)
    return freed


# --------------------------------------------------------------------------- leftovers


def leftovers(ws, tq, busy: set[str], now: Optional[float] = None) -> list[dict]:
    """What can go without losing anything: [{kind, path, size, project_id?}]."""
    now = time.time() if now is None else now
    out: list[dict] = []
    root = Path(ws.root)
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if d.name.startswith(".deleted-") and d.is_dir():
            out.append({"kind": "deleted", "path": d})
        elif store.is_project_id(d.name) and d.is_dir():
            if not (d / store.PROJECT_FILE).exists() and not (d / f"{store.PROJECT_FILE}.bak").exists():
                if _old(d, now) and d.name not in busy:
                    out.append({"kind": "folder", "path": d})
            elif d.name not in busy:
                try:
                    h = ws.get(d.name)
                except Exception:
                    continue
                out += [{"kind": "asset", "path": f, "project_id": d.name} for f in unused_assets(h, now)]
    if not getattr(tq, "passive", False) and tq.dir.is_dir():
        tasks = {t.id: t for t in tq.snapshot()}
        for d in sorted(tq.dir.iterdir()):
            if not d.is_dir():
                continue
            t = tasks.get(d.name)
            imported = t is not None and any(s.key == "import" and s.status == "done" for s in t.stages)
            if (t is None and _old(d, now)) or imported:
                out.append({"kind": "upload", "path": d})
    for item in out:
        item["size"] = size_of(item["path"])
    return out


def clean_leftovers(items: list[dict]) -> int:
    return sum(_remove(i["path"]) for i in items if i["path"].exists())


# --------------------------------------------------------------------------- summary


def models_usage() -> dict:
    try:
        d = store.models_dir()
    except OSError:
        return {"size": 0, "path": None}
    return {"size": size_of(d), "path": str(d)}


def disk() -> dict:
    try:
        u = shutil.disk_usage(store.home_dir() if store.home_dir().exists() else Path.home())
        return {"total": u.total, "free": u.free}
    except OSError:
        return {"total": None, "free": None}
