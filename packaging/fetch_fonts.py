"""Download the fonts MiliKara ships into a folder (default: the project's own ``fonts``):
Noto Sans CJK (regular + bold), a collection with Japanese, Simplified / Traditional Chinese and Korean
forms, under the SIL Open Font License.

    python packaging/fetch_fonts.py [folder]

It is the default font on Windows / Linux and the one used wherever a style's font lacks characters;
without it MiliKara still works with the system's fonts.  Files are pinned to a commit and checked.
"""

import hashlib
import sys
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/notofonts/noto-cjk"
FILES = {  # name: (commit/path, sha256)
    "NotoSansCJK-Regular.ttc": ("165c01b46ea533872e002e0785ff17e44f6d97d8/Sans/OTC/NotoSansCJK-Regular.ttc",
                                "b76b0433203017ca80401b2ee0dd69350349871c4b19d504c34dbdd80541690a"),
    "NotoSansCJK-Bold.ttc": ("165c01b46ea533872e002e0785ff17e44f6d97d8/Sans/OTC/NotoSansCJK-Bold.ttc",
                             "faa5f3656a78b2e2d450d27fe8382c778bc2b6bb5ea29c986664a6a435056ceb"),
    "LICENSE-NotoSansCJK.txt": ("a99a4354c68964f6bfac488d01010b1fb6d9178a/Sans/LICENSE",
                                "6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2"),
}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "fonts"
    out.mkdir(parents=True, exist_ok=True)
    for name, (path, digest) in FILES.items():
        dst = out / name
        if dst.exists() and sha256(dst) == digest:
            print(f"ok   {name}")
            continue
        tmp = dst.with_name(dst.name + ".part")
        print(f"get  {name}", flush=True)
        with urllib.request.urlopen(f"{BASE}/{path}", timeout=120) as r, open(tmp, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        if sha256(tmp) != digest:
            tmp.unlink()
            raise SystemExit(f"{name}: checksum mismatch")
        tmp.replace(dst)
    print(f"fonts in {out}")


if __name__ == "__main__":
    main()
