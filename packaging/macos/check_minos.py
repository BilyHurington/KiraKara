#!/usr/bin/env python3
"""Fail when a Mach-O file in <folder> needs a newer macOS than <version> (its LC_BUILD_VERSION /
LC_VERSION_MIN_MACOSX): the package would not start there.

    python3 check_minos.py <folder> 14.0
"""

import re
import subprocess
import sys
from pathlib import Path

MAGICS = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}


def minos(path: Path) -> tuple[int, ...]:
    out = subprocess.run(["otool", "-l", str(path)], capture_output=True, text=True).stdout
    found = re.findall(r"cmd LC_BUILD_VERSION.*?minos (\d+(?:\.\d+)*)", out, re.S)
    found += re.findall(r"cmd LC_VERSION_MIN_MACOSX.*?version (\d+(?:\.\d+)*)", out, re.S)
    return max((tuple(int(x) for x in v.split(".")) for v in found), default=())


def main() -> None:
    root, limit = Path(sys.argv[1]), tuple(int(x) for x in sys.argv[2].split("."))
    worst: list[tuple[tuple[int, ...], str]] = []
    n = 0
    for p in root.rglob("*"):
        if p.is_symlink() or not p.is_file():
            continue
        with open(p, "rb") as f:
            if f.read(4) not in MAGICS:
                continue
        n += 1
        v = minos(p)
        if v > limit:
            worst.append((v, str(p.relative_to(root))))
    print(f"{n} Mach-O files checked against macOS {sys.argv[2]}")
    for v, p in sorted(worst, reverse=True)[:40]:
        print(f"  needs macOS {'.'.join(map(str, v))}: {p}")
    if worst:
        raise SystemExit(f"{len(worst)} files need a newer macOS than {sys.argv[2]}")


if __name__ == "__main__":
    main()
