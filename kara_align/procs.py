"""Stopping a child process together with everything it started (the separator's workers, an AI CLI's
node / helper processes).

POSIX: children are started in their own session (``start_new_session``), so the whole process group is
signalled.  Windows has no process groups in that sense: ``taskkill /T /F`` ends the process tree.
"""

from __future__ import annotations

import os
import signal
import subprocess

# Popen keyword arguments that make the tree stoppable by :func:`kill_tree`
NEW_GROUP: dict = ({"start_new_session": True} if os.name == "posix"
                   else {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)})


def kill_tree(proc: subprocess.Popen, *, force: bool = True) -> None:
    """End ``proc`` and its descendants (``force=False`` asks politely on POSIX; Windows always forces)."""
    pid = getattr(proc, "pid", None)
    if not isinstance(pid, int) or pid <= 0:
        return
    if os.name == "posix":
        try:
            os.killpg(pid, signal.SIGKILL if force else signal.SIGTERM)
        except OSError:
            pass
    elif proc.poll() is None:
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            pass
    if proc.poll() is None:
        try:
            proc.kill() if force else proc.terminate()
        except OSError:
            pass


def command(cmd: list[str]):
    """``cmd`` ready for Popen.  A Windows batch file (``claude.cmd`` / ``codex.cmd`` from npm) runs
    through cmd.exe, which strips the first and last quote of a command line with more than two
    quotes (a path with a space plus an empty argument breaks); ``cmd /s /c "..."`` only strips the
    outer pair, which is added here."""
    if os.name == "nt" and cmd and cmd[0].lower().endswith((".cmd", ".bat")):
        return f'cmd.exe /d /s /c "{subprocess.list2cmdline(cmd)}"'
    return cmd
