"""Is there a newer MiliKara?  The latest GitHub release's manifest.json (the file the portable
package's updater reads), checked at most every few hours; nothing else is sent.

A portable package (a folder with its own Python and version.json) is updated with its 更新.bat /
更新.command; an installation from source with git pull.
"""

import sys
import threading
import time
from pathlib import Path
from typing import Optional

from . import __version__

REPO = "BilyHurington/MiliKara"
MANIFEST_URL = f"https://github.com/{REPO}/releases/latest/download/manifest.json"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
FRESH_S, RETRY_S = 6 * 3600, 30 * 60

_lock = threading.Lock()
_cache: dict = {}


def vkey(v: str) -> tuple:
    import re

    return tuple(int(x) for x in re.findall(r"\d+", v.split("+")[0])[:4]) or (0,)


def portable_root(executable: Optional[str] = None) -> Optional[Path]:
    """The folder of a portable package this Python belongs to (…/python/python.exe or
    …/python/bin/python3 next to version.json or a launcher), else None."""
    exe = Path(executable or sys.executable)
    for root in (exe.parent.parent, exe.parent.parent.parent):
        if exe.parent.name in ("python", "bin") and (root / "python").is_dir() and any(
                (root / n).exists() for n in ("version.json", "MiliKara.bat", "MiliKara.command", "KiraKara.bat", "KiraKara.command")):
            return root
    return None


def _fetch() -> str:
    import httpx

    r = httpx.get(MANIFEST_URL, timeout=8, follow_redirects=True, headers={"User-Agent": f"MiliKara/{__version__}"})
    r.raise_for_status()
    return str(r.json()["version"])


def check(force: bool = False) -> dict:
    """{current, latest, newer, portable, updater, url, error, checked}; ``latest`` None when unknown."""
    with _lock:
        now = time.time()
        fresh = _cache and now - _cache["checked"] < (FRESH_S if _cache.get("latest") else RETRY_S)
        if force or not fresh:
            try:
                _cache.update(latest=_fetch(), error=None, checked=now)
            except Exception as e:  # offline, GitHub unreachable, no release yet
                _cache.update(latest=_cache.get("latest"), error=str(e) or type(e).__name__, checked=now)
        latest = _cache.get("latest")
        root = portable_root()
        updater = None
        if root is not None:
            updater = "更新.bat" if (root / "python" / "python.exe").exists() else "更新.command"
        return {"current": __version__, "latest": latest, "newer": bool(latest) and vkey(latest) > vkey(__version__),
                "portable": root is not None, "updater": updater, "url": RELEASES_URL,
                "error": _cache.get("error"), "checked": _cache.get("checked")}
