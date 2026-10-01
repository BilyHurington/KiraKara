"""Where Claude Code / Codex run from: PATH, a desktop app's own copy, WSL, a typed path; chosen
automatically or by hand.  WSL is played by a stand-in wsl.exe that runs the same shell script here."""

import os
import shutil
import stat
import sys
import threading
import time

import pytest

from kara_align import settings as AS
from kara_align.interfaces import CancelToken, Cancelled
from kara_align.reading import cli_locate as L
from kara_align.reading import llm

from .test_simple_mode import CLAUDE, CODEX, _fake_bin

posix_only = pytest.mark.skipif(os.name == "nt", reason="the stand-ins are POSIX scripts")
TIMEOUT = shutil.which("timeout")  # (before the tests narrow PATH)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("KARA_ALIGN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty") + os.pathsep + "/usr/bin" + os.pathsep + "/bin")
    monkeypatch.setattr(L, "app_candidates", lambda provider: [])
    monkeypatch.setattr(L, "wsl_exe", lambda: None)
    monkeypatch.setattr(L, "_wsl_cache", None)
    llm._detect_cache.clear()


def _program(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@posix_only
def test_a_desktop_apps_own_copy_and_choosing_by_hand(tmp_path, monkeypatch):
    app = _program(tmp_path / "Claude" / "claude-code" / "2.1.284" / "claude", CLAUDE)
    monkeypatch.setattr(L, "app_candidates", lambda p: [app] if p == "claude" else [])
    d = llm.detect("claude", refresh=True)
    assert d["available"] and d["chosen"]["where"] == "app" and "9.9" in d["version"]
    r = llm.ask(AS.AiSettings(provider="claude"), "你好")
    assert r.text == "echo:你好"
    # on PATH too: auto takes PATH first; by hand, the app's copy
    path_copy = _fake_bin(tmp_path, monkeypatch, "claude", CLAUDE.replace("echo:", "path:"))
    assert llm.ask(AS.AiSettings(provider="claude"), "x").text == "path:x"
    cfg = AS.AiSettings(provider="claude", claude_cli={"where": "app"})
    assert llm.ask(cfg, "x").text == "echo:x"
    assert [x.where for x in L.locations("claude")] == ["path", "app"]
    # a typed path
    other = _program(tmp_path / "elsewhere" / "my-claude", CLAUDE.replace("echo:", "mine:"))
    cfg = AS.AiSettings(provider="claude", claude_cli={"where": "custom", "path": str(other)})
    assert llm.ask(cfg, "x").text == "mine:x"
    bad = AS.AiSettings(provider="claude", claude_cli={"where": "custom", "path": str(tmp_path / "nope")})
    with pytest.raises(llm.LlmError, match="自定义路径"):
        llm.ask(bad, "x")
    with pytest.raises(llm.LlmError, match="WSL"):
        llm.ask(AS.AiSettings(provider="claude", claude_cli={"where": "wsl:Ubuntu"}), "x")
    assert path_copy.exists()


def test_settings():
    s = AS.update({"ai": {"codex_cli": {"where": "wsl:Ubuntu-24.04"}}})
    assert s.ai.codex_cli.where == "wsl:Ubuntu-24.04" and s.ai.claude_cli.where == "auto"
    s = AS.update({"ai": {"codex_cli": {"path": "/x/codex"}}})  # a partial update keeps the rest
    assert (s.ai.codex_cli.where, s.ai.codex_cli.path) == ("wsl:Ubuntu-24.04", "/x/codex")
    with pytest.raises(ValueError):
        AS.update({"ai": {"claude_cli": {"where": "somewhere"}}})


def test_parsing():
    assert L._decode("Ubuntu\r\nDebian\r\n".encode("utf-16-le")) == "Ubuntu\r\nDebian\r\n"
    found = L.parse_probe("claude\t/home/u/.nvm/bin/claude\t2.1.284 (Claude Code)\r\nnoise\ncodex\t/usr/bin/codex\t\n", "Ubuntu")
    assert found["claude"].program == "/home/u/.nvm/bin/claude" and found["claude"].where == "wsl:Ubuntu"
    assert found["codex"].version == "" and found["codex"].label == "WSL（Ubuntu）"
    out = f"motd from .bashrc\n{L.OUT_MARK}\n{{\"a\": 1}}\n{L.LAST_MARK}\nthe reply\n"
    assert L.split_output(out) == ('{"a": 1}', "the reply")


# a stand-in for wsl.exe: -l -q lists two distributions (UTF-16, as wsl.exe writes), otherwise
# "-d <name> -e <cmd...>" runs <cmd...> here (it is the same sh script WSL would run)
WSL = r"""import os, sys
a = sys.argv[1:]
if a[:2] == ["-l", "-q"]:
    sys.stdout.buffer.write("Ubuntu\r\ndocker-desktop\r\nDebian\r\n".encode("utf-16-le")); sys.exit(0)
if a and a[0] == "-d":
    os.environ["FAKE_DISTRO"] = a[1]; a = a[2:]
assert a[0] == "-e", a
if os.environ.get("FAKE_DISTRO") == "Debian":  # nothing installed there
    sys.exit(0)
os.execvp(a[1], a[1:])
"""


@posix_only
@pytest.mark.skipif(TIMEOUT is None, reason="needs timeout (coreutils), as every WSL distribution has")
def test_wsl(tmp_path, monkeypatch):
    wsl = _program(tmp_path / "win" / "wsl.exe", WSL)
    monkeypatch.setattr(L, "wsl_exe", lambda: str(wsl))
    linux = tmp_path / "linux-bin"  # what is installed "inside WSL"
    _program(linux / "nvm-bin" / "claude", CLAUDE)  # found only with the login shell's PATH
    _program(linux / "codex", CODEX)
    # the user's login shell, as getent names it: it adds a folder to PATH (as ~/.bashrc does for nvm);
    # nothing of this computer's own profile is read
    nvm = linux / "nvm-bin"
    shell = linux / "login-shell"
    shell.write_text(f'#!/bin/sh\nshift\nPATH="{nvm}:$PATH" exec /bin/sh -c "$1"\n')
    shell.chmod(0o755)
    getent = linux / "getent"
    getent.write_text(f'#!/bin/sh\necho "u:x:1000:1000::/home/u:{shell}"\n')
    getent.chmod(0o755)
    monkeypatch.setenv("PATH", f"{linux}{os.pathsep}{os.path.dirname(TIMEOUT or '')}{os.pathsep}/usr/bin{os.pathsep}/bin")
    assert L.wsl_distros() == ["Ubuntu", "Debian"]
    found = L.find_wsl(refresh=True)
    assert [x.where for x in found["claude"]] == ["wsl:Ubuntu"] and "9.9" in found["claude"][0].version
    assert found["claude"][0].program == str(linux / "nvm-bin" / "claude")
    # auto: nothing on this computer, so WSL; the prompt reaches it on stdin, the reply comes back fenced
    d = llm.detect("claude", refresh=True)
    assert d["available"] and d["chosen"]["label"] == "WSL（Ubuntu）"
    r = llm.ask(AS.AiSettings(provider="claude"), "你好，读音")
    assert r.text == "echo:你好，读音" and r.cost_usd == 0.01
    r = llm.ask(AS.AiSettings(provider="codex", codex_cli={"where": "wsl:Ubuntu"}), "hello codex")
    assert r.text == "codex:hello codex"  # the -o file inside WSL, printed after the marker
    with pytest.raises(llm.LlmError, match="WSL（Debian）里没有找到 claude"):
        llm.ask(AS.AiSettings(provider="claude", claude_cli={"where": "wsl:Debian"}), "x")
    # a cancel stops it inside "WSL" as well
    _program(linux / "nvm-bin" / "claude", "import sys, time\nsys.stdin.read()\nopen(sys.argv[0] + '.started', 'w').close()\ntime.sleep(60)\n")
    tok = CancelToken()
    threading.Timer(1.5, tok.cancel).start()
    t0 = time.time()
    with pytest.raises(Cancelled):
        llm.ask(AS.AiSettings(provider="claude"), "x", cancel=tok)
    assert time.time() - t0 < 15 and (linux / "nvm-bin" / "claude.started").exists()
    time.sleep(0.5)
    import subprocess

    left = subprocess.run(["pgrep", "-f", str(linux / "nvm-bin" / "claude")], capture_output=True, text=True).stdout.split()
    assert left == []  # nothing left running
