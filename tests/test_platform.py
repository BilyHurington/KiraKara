"""Pieces that differ between POSIX and Windows: the queue lock, stopping a process tree, the
serve command's port choice."""

import socket
import subprocess
import sys
import time

import pytest

from kara_align import cli, procs
from kara_align.pipeline import TaskQueue


def test_kill_tree_ends_child_and_grandchild(tmp_path):
    marker = tmp_path / "grandchild.pid"
    # a child that starts a grandchild and waits; both must be gone after kill_tree
    code = (
        "import subprocess, sys, time\n"
        f"g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open(r'{marker}', 'w').write(str(g.pid))\n"
        "time.sleep(60)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", code], **procs.NEW_GROUP)
    for _ in range(100):
        if marker.exists() and marker.read_text():
            break
        time.sleep(0.05)
    gpid = int(marker.read_text())
    procs.kill_tree(proc)
    assert proc.wait(10) is not None
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _alive(gpid):
        time.sleep(0.1)
    assert not _alive(gpid)


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    import os

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # a zombie (ended, not yet reaped by its parent's parent) counts as ended
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split()[2] != "Z"
    except OSError:
        out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout
        return bool(out.strip()) and not out.strip().startswith("Z")


def test_kill_tree_on_finished_process_is_harmless():
    proc = subprocess.Popen([sys.executable, "-c", "pass"], **procs.NEW_GROUP)
    proc.wait(10)
    procs.kill_tree(proc)
    procs.kill_tree(proc, force=False)


def test_queue_lock_is_exclusive(tmp_path):
    a = TaskQueue.__new__(TaskQueue)
    b = TaskQueue.__new__(TaskQueue)
    a.dir = b.dir = tmp_path
    fd = a._acquire()
    assert fd is not None
    if fd is True:
        pytest.skip("no file locking on this platform")
    a._lock_file = fd
    try:
        assert b._acquire() is None  # a second queue on the same folder does not run tasks
    finally:
        a._release()
    b._lock_file = b._acquire()  # free again once released
    assert b._lock_file not in (None, True)
    b._release()


def test_free_port_skips_a_busy_one():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        busy = s.getsockname()[1]
        assert cli.free_port("127.0.0.1", busy, busy + 20) != busy


def test_serve_port_argument():
    ap = cli.build_parser()
    assert ap.parse_args(["serve", "--port", "auto", "--open"]).port == "auto"
    assert ap.parse_args(["serve", "--port", "9000"]).port == 9000
    with pytest.raises(SystemExit):
        ap.parse_args(["serve", "--port", "x"])


def test_batch_files_run_through_cmd_s(monkeypatch):
    assert procs.command(["/usr/bin/claude", "-p", ""]) == ["/usr/bin/claude", "-p", ""]
    monkeypatch.setattr(procs.os, "name", "nt")
    line = procs.command([r"C:\Users\A B\npm\claude.cmd", "-p", "--tools", ""])
    assert line == r'cmd.exe /d /s /c ""C:\Users\A B\npm\claude.cmd" -p --tools """'
    assert procs.command([r"C:\x\claude.exe", "-p"]) == [r"C:\x\claude.exe", "-p"]
