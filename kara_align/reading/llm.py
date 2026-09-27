"""Send one message to an LLM and get its text reply.

Providers:

* ``claude`` – Claude Code CLI: ``claude -p`` with every tool disabled, no
  session saved, JSON output (reply text + cost);
* ``codex``  – Codex CLI: ``codex exec`` in a read-only sandbox, ephemeral,
  last message written to a file;
* ``openai`` – any OpenAI-compatible ``/chat/completions`` endpoint.

The CLIs start in an empty temporary directory.  Claude Code has every tool
disabled; Codex's read-only sandbox still lets it read files elsewhere on the
computer (it cannot change them).  Prompts go through stdin / the request body,
never the command line.  Every call can be cancelled and has a timeout (the CLI
and anything it started are killed); failures raise :class:`LlmError` with a
readable reason.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..interfaces import CancelToken, Cancelled
from ..procs import NEW_GROUP, command, kill_tree
from ..settings import AiSettings, api_key

PROVIDERS = {
    "claude": {"label": "Claude Code", "binary": "claude"},
    "codex": {"label": "Codex", "binary": "codex"},
    "openai": {"label": "OpenAI 兼容 API", "binary": None},
}


class LlmError(Exception):
    pass


@dataclass
class LlmReply:
    text: str
    provider: str
    model: str = ""
    elapsed_s: float = 0.0
    cost_usd: Optional[float] = None
    extra: dict = field(default_factory=dict)


_detect_cache: dict[str, tuple[float, dict]] = {}


def detect(provider: str, *, refresh: bool = False) -> dict:
    """Availability of one provider: {id, label, available, version, detail}."""
    info = PROVIDERS[provider]
    now = time.time()
    if not refresh and provider in _detect_cache and now - _detect_cache[provider][0] < 60:
        return _detect_cache[provider][1]
    out = {"id": provider, "label": info["label"], "available": True, "version": None, "detail": ""}
    if info["binary"]:
        path = shutil.which(info["binary"])
        if path is None:
            out.update(available=False, detail=f"没有找到 {info['binary']} 命令（需要先安装并登录）")
        else:
            try:
                v = subprocess.run(command([path, "--version"]), capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=20)
                out["version"] = (v.stdout or v.stderr).strip().splitlines()[0] if (v.stdout or v.stderr) else None
                out["detail"] = path
            except Exception as e:  # installed but broken
                out.update(available=False, detail=f"{info['binary']} 无法运行：{e}")
    _detect_cache[provider] = (now, out)
    return out


def detect_all(refresh: bool = False) -> list[dict]:
    return [detect(p, refresh=refresh) for p in PROVIDERS]


def ask(cfg: AiSettings, prompt: str, *, cancel: Optional[CancelToken] = None,
        on_wait: Optional[Callable[[float], None]] = None) -> LlmReply:
    if cfg.provider in ("manual", "none"):
        raise LlmError("当前是手动网页聊天往返：请复制提示词到 AI 聊天网页，再粘贴回复；"
                       "要自动发送请在设置中选择 Claude Code、Codex 或 API")
    t0 = time.time()
    if cfg.provider == "claude":
        r = _claude(cfg, prompt, cancel, on_wait)
    elif cfg.provider == "codex":
        r = _codex(cfg, prompt, cancel, on_wait)
    elif cfg.provider == "openai":
        r = _openai(cfg, prompt, cancel, on_wait)
    else:
        raise LlmError(f"未知的 AI 提供方 {cfg.provider}")
    r.elapsed_s = round(time.time() - t0, 1)
    if not r.text.strip():
        raise LlmError("AI 返回了空回复")
    return r


# ---------------------------------------------------------------------------- CLIs


def _run(cmd: list[str], prompt: str, cwd: str, timeout: float, cancel: Optional[CancelToken],
         on_wait: Optional[Callable[[float], None]]) -> tuple[int, str, str]:
    """Run a CLI with the prompt on stdin.

    The CLI (and anything it started) is killed whenever this does not return normally: a cancel,
    the timeout, or an exception anywhere — also one raised by ``on_wait`` (a progress callback
    raises :class:`Cancelled` when the job is cancelled)."""
    try:
        # its own process group, so the whole tree can be stopped
        proc = subprocess.Popen(command(cmd), cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                env={**os.environ, "NO_COLOR": "1"}, **NEW_GROUP)
    except OSError as e:
        raise LlmError(f"无法启动 {cmd[0]}：{e}") from e
    out: dict[str, str] = {}

    def talk() -> None:
        try:
            o, e = proc.communicate(prompt)
            out["o"], out["e"] = o, e
        except Exception:  # pipes closed because the process was killed
            pass

    t = threading.Thread(target=talk, daemon=True)
    t.start()
    t0 = time.time()
    done = False
    try:
        while t.is_alive():
            t.join(0.5)
            waited = time.time() - t0
            if cancel is not None and cancel.cancelled:
                raise Cancelled()
            if waited > timeout:
                raise LlmError(f"{Path(cmd[0]).name} 超过 {int(timeout)} 秒没有返回")
            if on_wait is not None and t.is_alive():
                on_wait(waited)
        done = True
    finally:
        if not done or proc.poll() is None:
            _kill_tree(proc)
            t.join(5)
    return proc.returncode, out.get("o", ""), out.get("e", "")


def _kill_tree(proc: subprocess.Popen) -> None:
    kill_tree(proc)
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        pass


def _tail(s: str, n: int = 400) -> str:
    s = s.strip()
    return s[-n:] if len(s) > n else s


def _claude(cfg: AiSettings, prompt: str, cancel, on_wait) -> LlmReply:
    exe = shutil.which("claude")
    if exe is None:
        raise LlmError("没有找到 claude 命令：请先安装 Claude Code 并登录")
    cmd = [exe, "-p", "--tools", "", "--no-session-persistence", "--output-format", "json"]
    if cfg.model:
        cmd.append(f"--model={cfg.model}")  # one argument: a name starting with "-" is never read as an option
    with tempfile.TemporaryDirectory(prefix="kara-ai-") as td:
        code, o, e = _run(cmd, prompt, td, cfg.timeout_s, cancel, on_wait)
    try:
        data = json.loads(o)
    except json.JSONDecodeError:
        raise LlmError(f"claude 没有返回 JSON（退出码 {code}）：{_tail(e or o)}") from None
    if data.get("is_error") or code != 0:
        raise LlmError(f"claude 返回错误：{_tail(str(data.get('result') or e))}")
    return LlmReply(text=str(data.get("result") or ""), provider="claude",
                    model=cfg.model or str(next(iter(data.get("modelUsage") or {}), "")),
                    cost_usd=data.get("total_cost_usd"))


def _codex(cfg: AiSettings, prompt: str, cancel, on_wait) -> LlmReply:
    exe = shutil.which("codex")
    if exe is None:
        raise LlmError("没有找到 codex 命令：请先安装 Codex 并登录")
    with tempfile.TemporaryDirectory(prefix="kara-ai-") as td:
        last = Path(td) / "last.txt"
        cmd = [exe, "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only", "--color", "never",
               "-o", str(last)]
        if cfg.model:
            cmd.append(f"--model={cfg.model}")
        cmd.append("-")
        code, o, e = _run(cmd, prompt, td, cfg.timeout_s, cancel, on_wait)
        text = last.read_text(encoding="utf-8") if last.exists() else ""
    if code != 0 or not text.strip():
        raise LlmError(f"codex 执行失败（退出码 {code}）：{_tail(e or o)}")
    model = cfg.model
    for line in (e + "\n" + o).splitlines():
        if line.strip().startswith("model:") and not model:
            model = line.split(":", 1)[1].strip()
    return LlmReply(text=text, provider="codex", model=model)


# ---------------------------------------------------------------------------- API


def _openai(cfg: AiSettings, prompt: str, cancel, on_wait) -> LlmReply:
    key = api_key(cfg)
    if not cfg.model:
        raise LlmError("使用 API 需要填写模型名称")
    if not key:
        raise LlmError(f"没有 API Key：请在设置中填写，或设置环境变量 {cfg.api_key_env}")
    url = cfg.base_url.rstrip("/") + "/chat/completions"
    body = json.dumps({"model": cfg.model, "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    out: dict = {}

    def call() -> None:
        try:
            with urllib.request.urlopen(req, timeout=cfg.timeout_s) as resp:
                out["data"] = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            out["err"] = f"HTTP {e.code}：{_tail(detail)}"
        except Exception as e:
            out["err"] = f"{type(e).__name__}: {e}"

    t = threading.Thread(target=call, daemon=True)
    t.start()
    t0 = time.time()
    while t.is_alive():
        t.join(0.5)
        waited = time.time() - t0
        if cancel is not None and cancel.cancelled:
            raise Cancelled()  # the request thread finishes on its own; its result is ignored
        # the socket timeout applies to each read, so a server trickling bytes could last forever
        if waited > cfg.timeout_s + 5:
            raise LlmError(f"API 超过 {int(cfg.timeout_s)} 秒没有返回")
        if on_wait is not None and t.is_alive():
            on_wait(waited)
    if "err" in out:
        raise LlmError(f"API 请求失败：{out['err']}")
    data = out.get("data") or {}
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise LlmError(f"API 返回格式无法识别：{_tail(json.dumps(data, ensure_ascii=False))}") from None
    if isinstance(text, list):  # some servers return content parts
        text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
    return LlmReply(text=str(text or ""), provider="openai", model=str(data.get("model") or cfg.model),
                    extra={"usage": data.get("usage")})
