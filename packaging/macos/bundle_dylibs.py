#!/usr/bin/env python3
"""Copy command-line tools with every non-system library they load into <out>/bin and <out>/lib, and
relink them to load from there (@loader_path), so they run on a Mac without Homebrew.

    python3 bundle_dylibs.py <out> /opt/homebrew/bin/ffmpeg /opt/homebrew/bin/ffprobe ...

Libraries under /usr/lib and /System stay system ones.  Everything is re-signed ad hoc (changing load
commands breaks the original signature, and arm64 refuses unsigned code).
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SYSTEM = ("/usr/lib/", "/System/")


def run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def deps(path: Path) -> list[str]:
    lines = run("otool", "-L", str(path)).splitlines()[1:]
    return [ln.strip().split(" (compatibility")[0] for ln in lines if ln.strip()]


def rpaths(path: Path) -> list[str]:
    return re.findall(r"cmd LC_RPATH\n\s+cmdsize \d+\n\s+path (\S+)", run("otool", "-l", str(path)))


def resolve(ref: str, owner: Path, exe: Path) -> Path | None:
    """Where a load command of ``owner`` points (None for system libraries)."""
    if ref.startswith(SYSTEM):
        return None
    if ref.startswith("@loader_path/"):
        return (owner.parent / ref[len("@loader_path/"):]).resolve()
    if ref.startswith("@executable_path/"):
        return (exe.parent / ref[len("@executable_path/"):]).resolve()
    if ref.startswith("@rpath/"):
        name = ref[len("@rpath/"):]
        for rp in rpaths(owner):
            base = rp.replace("@loader_path", str(owner.parent)).replace("@executable_path", str(exe.parent))
            cand = Path(base) / name
            if cand.exists():
                return cand.resolve()
        raise SystemExit(f"cannot resolve {ref} of {owner}")
    return Path(ref).resolve()


def main() -> None:
    out = Path(sys.argv[1])
    bindir, libdir = out / "bin", out / "lib"
    bindir.mkdir(parents=True, exist_ok=True)
    libdir.mkdir(parents=True, exist_ok=True)
    tools = sys.argv[2:]
    files: dict[Path, Path] = {}  # original (resolved) -> its copy
    edits: dict[Path, list[tuple[str, str]]] = {}
    queue: list[tuple[Path, Path]] = []  # (original, the executable it is loaded by)
    for tool in tools:
        src = Path(tool).resolve()
        dst = bindir / Path(tool).name
        shutil.copy2(src, dst)
        os.chmod(dst, 0o755)
        files[src] = dst
        queue.append((src, src))
    while queue:
        src, exe = queue.pop()
        for ref in deps(src):
            real = resolve(ref, src, exe)
            if real is None or real == src:
                continue
            if real not in files:
                dst = libdir / real.name
                if dst.exists():
                    raise SystemExit(f"two different libraries named {real.name}")
                shutil.copy2(real, dst)
                os.chmod(dst, 0o755)
                files[real] = dst
                queue.append((real, exe))
            edits.setdefault(files[src], []).append((ref, files[real].name))
    for dst in files.values():
        in_bin = dst.parent == bindir
        args = ["install_name_tool"]
        for ref, name in edits.get(dst, []):
            args += ["-change", ref, f"@loader_path/{'../lib/' if in_bin else ''}{name}"]
        if not in_bin:
            args += ["-id", f"@loader_path/{dst.name}"]
        for rp in rpaths(dst):
            args += ["-delete_rpath", rp]
        if len(args) > 1:
            subprocess.run(args + [str(dst)], check=True, capture_output=True)
        subprocess.run(["codesign", "--force", "--sign", "-", str(dst)], check=True, capture_output=True)
    left = [str(f) for f in files.values() if any(r.startswith(("/opt/homebrew", "/usr/local")) for r in deps(f))]
    if left:
        raise SystemExit(f"still pointing at Homebrew: {left}")
    print(f"{len(tools)} tools, {len(files) - len(tools)} libraries → {out}")


if __name__ == "__main__":
    main()
