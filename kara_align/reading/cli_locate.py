"""Where the Claude Code / Codex command-line programs are, and how to start them.

A CLI can be found in four places, tried in this order when the setting is "auto":

* ``path``   – the command on this computer's PATH (an npm / brew / installer install);
* ``app``    – the copy a desktop app carries: the Claude app keeps Claude Code in its data folder
               (``…/Claude/claude-code/<version>/``), the ChatGPT / Codex app ships ``codex-cli`` in
               its resources; both log in with the same account as the app;
* ``wsl``    – Windows only: installed inside a WSL distribution (where many Windows users run them);
* ``custom`` – a program path typed by the user (on Windows a path starting with "/" is inside WSL).

A WSL program is started through ``wsl.exe`` with the user's own login shell loaded first (so a
command installed with nvm / in ``~/.local/bin`` is found), its output fenced by markers (a shell
that prints something when it starts does not spoil it), its run time bounded by ``timeout`` inside
WSL and its pid kept so a cancel can stop it there too (ending ``wsl.exe`` alone may leave it running).
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

BINARIES = {"claude": "claude", "codex": "codex"}
OUT_MARK = "<<<MILIKARA-OUT>>>"
LAST_MARK = "<<<MILIKARA-LAST>>>"
OUT_FILE = "{out}"  # an argument standing for the file a CLI writes its last message to

_wsl_cache: tuple[float, dict[str, list["Location"]]] | None = None


@dataclass
class Location:
    source: str  # path | app | wsl | custom
    program: str  # a path on this computer, or the program's path inside WSL
    distro: str = ""  # the WSL distribution ("" = the default one)
    version: str = ""

    @property
    def where(self) -> str:
        """The setting value choosing this location."""
        return f"wsl:{self.distro}" if self.source == "wsl" else self.source

    @property
    def label(self) -> str:
        return {"path": "命令行（PATH）", "app": "桌面应用自带", "custom": "自定义路径"}.get(
            self.source, f"WSL（{self.distro or '默认'}）")

    def to_dict(self) -> dict:
        return {**asdict(self), "where": self.where, "label": self.label}


# ------------------------------------------------------------------ finding


def _version_key(name: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", name)) or (0,)


def app_candidates(provider: str) -> list[Path]:
    """Where a desktop app keeps its copy of the CLI, newest first."""
    home = Path.home()
    out: list[Path] = []
    if sys.platform == "darwin":
        if provider == "claude":
            base = home / "Library" / "Application Support" / "Claude" / "claude-code"
            for v in sorted(base.glob("*"), key=lambda p: _version_key(p.name), reverse=True):
                out += [v / "claude.app" / "Contents" / "MacOS" / "claude", v / "claude"]
        else:
            for apps in (Path("/Applications"), home / "Applications"):
                for app in ("ChatGPT.app", "Codex.app"):
                    out.append(apps / app / "Contents" / "Resources" / "codex-cli" / "bin" / "codex")
    elif os.name == "nt":
        appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        if provider == "claude":
            base = appdata / "Claude" / "claude-code"
            for v in sorted(base.glob("*"), key=lambda p: _version_key(p.name), reverse=True):
                out += [v / "claude.exe", *sorted(v.glob("*/claude.exe")), *sorted(v.glob("*/*/claude.exe"))]
        else:
            for name in ("ChatGPT", "Codex", "OpenAI Codex"):
                out.append(local / "Programs" / name / "resources" / "codex-cli" / "bin" / "codex.exe")
            program_files = Path(os.environ.get("ProgramFiles") or "C:/Program Files")
            try:  # Microsoft Store apps (the folder may not be readable)
                out += sorted(program_files.glob("WindowsApps/OpenAI.*/app/resources/codex-cli/bin/codex.exe"),
                              reverse=True)
            except OSError:
                pass
    return out


def version_of(argv: list[str]) -> str:
    from ..procs import command

    try:
        v = subprocess.run(command([*argv, "--version"]), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    text = (v.stdout or v.stderr).strip()
    return text.splitlines()[0].strip() if text else ""


def _runs(path: Path) -> bool:
    try:
        return path.is_file() and (os.name == "nt" or os.access(path, os.X_OK))
    except OSError:
        return False


def find_path(provider: str) -> Optional[Location]:
    p = shutil.which(BINARIES[provider])
    return Location("path", p) if p else None


def find_app(provider: str) -> Optional[Location]:
    for p in app_candidates(provider):
        if _runs(p):
            return Location("app", str(p))
    return None


# ------------------------------------------------------------------ WSL


def wsl_exe() -> Optional[str]:
    if os.name != "nt":
        return None
    return shutil.which("wsl.exe") or shutil.which("wsl")


def _wsl_env() -> dict:
    return {**os.environ, "WSL_UTF8": "1", "NO_COLOR": "1"}


def _decode(b: bytes) -> str:
    """wsl.exe's own messages are UTF-16 unless WSL_UTF8 is honoured (older WSL)."""
    if b.count(b"\x00") > len(b) // 4:
        return b.decode("utf-16-le", "replace")
    return b.decode("utf-8", "replace")


def wsl_distros() -> list[str]:
    exe = wsl_exe()
    if not exe:
        return []
    try:
        r = subprocess.run([exe, "-l", "-q"], capture_output=True, timeout=30, env=_wsl_env())
    except (OSError, subprocess.SubprocessError):
        return []
    names = [ln.strip().strip("\x00").strip() for ln in _decode(r.stdout).splitlines()]
    return [n for n in names if n and not n.lower().startswith("docker-desktop")]


# the user's login shell, interactive, read once for its PATH (nvm and friends live in ~/.bashrc / ~/.zshrc);
# its stdin is /dev/null so it never eats the prompt
_LOAD_PATH = (
    's=$(getent passwd "$(id -un)" 2>/dev/null | cut -d: -f7); [ -x "$s" ] || s=/bin/sh; '
    'P=$("$s" -lic \'printf "\\n__MKP__%s\\n" "$PATH"\' </dev/null 2>/dev/null | sed -n "s/^__MKP__//p" | tail -n 1); '
    '[ -n "$P" ] && export PATH="$P"; '
)


def wsl_argv(distro: str, script: str) -> list[str]:
    head = [wsl_exe() or "wsl.exe"]
    if distro:
        head += ["-d", distro]
    return [*head, "-e", "sh", "-c", _LOAD_PATH + script]


_PROBE = ('for c in claude codex; do p=$(command -v "$c" 2>/dev/null) || continue; '
          'v=$("$p" --version 2>/dev/null | head -n 1); printf "%s\\t%s\\t%s\\n" "$c" "$p" "$v"; done')


def parse_probe(text: str, distro: str) -> dict[str, Location]:
    out: dict[str, Location] = {}
    for ln in text.splitlines():
        parts = ln.rstrip("\r").split("\t")
        if len(parts) >= 2 and parts[0] in BINARIES and parts[1].startswith("/"):
            out[parts[0]] = Location("wsl", parts[1], distro, parts[2].strip() if len(parts) > 2 else "")
    return out


def find_wsl(refresh: bool = False) -> dict[str, list[Location]]:
    """{provider: [locations]} over every WSL distribution (cached 10 min: starting WSL takes seconds)."""
    global _wsl_cache
    if not wsl_exe():
        return {}
    now = time.time()
    if not refresh and _wsl_cache is not None and now - _wsl_cache[0] < 600:
        return _wsl_cache[1]
    found: dict[str, list[Location]] = {p: [] for p in BINARIES}
    for d in wsl_distros():
        try:
            r = subprocess.run(wsl_argv(d, _PROBE), capture_output=True, timeout=60, env=_wsl_env())
        except (OSError, subprocess.SubprocessError):
            continue
        for p, loc in parse_probe(r.stdout.decode("utf-8", "replace"), d).items():
            found[p].append(loc)
    _wsl_cache = (now, found)
    return found


# ------------------------------------------------------------------ choosing


def locations(provider: str, custom: str = "", *, refresh: bool = False) -> list[Location]:
    """Every place the CLI was found (with versions), in the order "auto" tries them."""
    out: list[Location] = []
    for loc in (find_path(provider), find_app(provider)):
        if loc is not None:
            loc.version = version_of([loc.program])
            out.append(loc)
    out += find_wsl(refresh).get(provider, [])
    c = custom_location(custom)
    if c is not None:
        out.append(c)
    return out


def custom_location(path: str) -> Optional[Location]:
    path = (path or "").strip().strip('"')
    if not path:
        return None
    if os.name == "nt" and path.startswith("/"):
        loc = Location("custom", path, distro="")
        return loc if wsl_exe() else None
    p = Path(path).expanduser()
    if not _runs(p):
        return None
    return Location("custom", str(p), version=version_of([str(p)]))


def resolve(provider: str, where: str = "auto", custom: str = "") -> tuple[Optional[Location], str]:
    """The location a call uses, or None and why."""
    name = BINARIES[provider]
    if where == "custom":
        loc = custom_location(custom)
        return (loc, "") if loc else (None, f"自定义路径「{custom or '（未填写）'}」不存在或不能运行")
    if where == "path":
        loc = find_path(provider)
        return (loc, "") if loc else (None, f"PATH 里没有 {name} 命令")
    if where == "app":
        loc = find_app(provider)
        return (loc, "") if loc else (None, f"没有找到桌面应用自带的 {name}")
    if where.startswith("wsl:"):
        distro = where[4:]
        if not wsl_exe():
            return None, "这台电脑没有 WSL"
        hit = next((x for x in find_wsl().get(provider, []) if x.distro == distro), None)
        if hit is None:
            hit = next((x for x in find_wsl(refresh=True).get(provider, []) if x.distro == distro), None)
        return (hit, "") if hit else (None, f"WSL（{distro or '默认'}）里没有找到 {name}")
    for finder in (find_path, find_app):
        loc = finder(provider)
        if loc is not None:
            return loc, ""
    wsl = find_wsl().get(provider, [])
    if wsl:
        return wsl[0], ""
    where_hint = "，也可以在 WSL 里安装" if os.name == "nt" else ""
    return None, f"没有找到 {name}：请先安装并登录{where_hint}，或在设置里手动填写程序路径"


# ------------------------------------------------------------------ starting


@dataclass
class Invocation:
    argv: list
    cwd: Optional[str]
    wsl: bool = False
    run_id: str = ""
    distro: str = ""


def invocation(loc: Location, args: list[str], timeout_s: float, cwd: str) -> Invocation:
    """How to start ``loc`` with ``args``.  ``OUT_FILE`` among the args stands for the file the CLI
    writes its last message to: ``<cwd>/last.txt`` on this computer; inside WSL a file in a fresh
    temporary folder there, printed after the second marker."""
    if loc.source != "wsl" and not (loc.source == "custom" and os.name == "nt" and loc.program.startswith("/")):
        last = str(Path(cwd) / "last.txt")
        return Invocation([loc.program, *[last if a == OUT_FILE else a for a in args]], cwd)
    run_id = uuid.uuid4().hex[:12]
    cmd = shlex.join([loc.program, *args]).replace(shlex.quote(OUT_FILE), '"$d/last.txt"')
    t = max(30, int(timeout_s) + 10)  # inside WSL the run ends on its own, whatever happens to wsl.exe
    script = (
        'exec 3<&0; d=$(mktemp -d /tmp/milikara-ai-XXXXXX) || exit 97; cd "$d" || exit 97; '
        f"printf '%s\\n' '{OUT_MARK}'; "
        f'timeout -k 10 {t} {cmd} 0<&3 & p=$!; echo "$p" > /tmp/milikara-ai-{run_id}.pid; wait "$p"; rc=$?; '
        f"printf '\\n%s\\n' '{LAST_MARK}'; cat \"$d/last.txt\" 2>/dev/null; "
        f'rm -rf "$d" /tmp/milikara-ai-{run_id}.pid; exit $rc'
    )
    return Invocation(wsl_argv(loc.distro, script), None, True, run_id, loc.distro)


def split_output(out: str) -> tuple[str, str]:
    """(the CLI's own output, its last message) from a WSL run's fenced stdout."""
    if OUT_MARK in out:
        out = out.split(OUT_MARK, 1)[1].lstrip("\r\n")
    if LAST_MARK in out:
        body, last = out.rsplit(LAST_MARK, 1)
        return body.rstrip("\r\n"), last.strip("\r\n")
    return out, ""


def stop_wsl(inv: Invocation) -> None:
    """Stop a WSL run from the outside (a cancel): its timeout process, which ends the CLI."""
    if not inv.wsl:
        return
    script = (f'p=$(cat /tmp/milikara-ai-{inv.run_id}.pid 2>/dev/null) && '
              f'{{ pkill -TERM -P "$p" 2>/dev/null; kill -TERM "$p" 2>/dev/null; }}; true')
    head = [wsl_exe() or "wsl.exe"] + (["-d", inv.distro] if inv.distro else [])
    try:
        subprocess.run([*head, "-e", "sh", "-c", script], capture_output=True, timeout=20, env=_wsl_env())
    except (OSError, subprocess.SubprocessError):
        pass
