"""Command line interface.  Every command calls :mod:`kara_align.service`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

from . import __version__
from . import service as S


def _print(obj: Any) -> None:
    if isinstance(obj, str):
        print(obj)
    else:
        print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _read_text(path: str) -> tuple[str, str, Optional[str]]:
    """Return (text, origin, filename); ``-`` reads stdin (= paste)."""
    if path == "-":
        return sys.stdin.read(), "paste", None
    p = Path(path)
    return p.read_text(encoding="utf-8-sig"), "upload", p.name


def _progress(frac: float, msg: str = "") -> None:
    print(f"\r[{frac * 100:5.1f}%] {msg[:60]:<60}", end="", file=sys.stderr, flush=True)


def _preset_names() -> list[str]:
    from .audio.separation import PRESET_NAMES

    return list(PRESET_NAMES)


def _parse_set(items: list[str]) -> dict:
    """``--set decode.soft_sigma_ms=300`` → nested dict (values parsed as JSON when possible)."""
    out: dict = {}
    for item in items or []:
        key, _, raw = item.partition("=")
        try:
            val: Any = json.loads(raw)
        except json.JSONDecodeError:
            val = raw
        cur = out
        parts = key.split(".")
        for k in parts[:-1]:
            cur = cur.setdefault(k, {})
        cur[parts[-1]] = val
    return out


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_init(a) -> None:
    h = S.create_dir(Path(a.project), a.name or Path(a.project).name, a.mode)
    _print(f"已创建项目 {h.dir}（模式 {a.mode}）")


def cmd_lyrics(a) -> None:
    h = S.open_dir(Path(a.project))
    text, origin, filename = _read_text(a.file)
    pv = S.parse_lyrics(h, text, origin=origin, filename=filename, mode=a.mode)
    for w in pv["warnings"]:
        print(f"提示: {w}", file=sys.stderr)
    if pv["error"]:
        raise S.ServiceError(pv["error"])
    msgs = S.apply_lyrics(h, pv["preview_id"])
    for m in msgs:
        print(f"提示: {m}", file=sys.stderr)
    doc = h.project.lyrics
    sung = doc.sung_lines()
    _print(f"已导入 {len(doc.lines)} 行（参与对齐 {len(sung)} 行，格式 {pv['detected']}，语言 {doc.language}）")


def cmd_track(a) -> None:
    h = S.open_dir(Path(a.project))
    text, _, _ = _read_text(a.file)
    prev = S.preview_track(h, text, a.kind)
    for p in prev["pairs"]:
        print(f"{p['line_id']}  {p['line_text']}  ⇐  {p['text']}  ({p['method']})")
    if prev["unmatched"]:
        print(f"未配对: {len(prev['unmatched'])} 行", file=sys.stderr)
    if not a.dry_run:
        S.apply_track(h, a.kind, prev["pairs"])
        print("已应用", file=sys.stderr)


def cmd_fetch(a) -> None:
    out = S.fetch_link(a.link)
    if out["kind"] == "collection":
        _print({"collection": out.get("title"), "songs": [
            f"{s['platform']}:{s['song_id']}  {s['title']} - {', '.join(s['artists'])}" for s in out["songs"]]})
        return
    song = out["song"]
    info = {k: song[k] for k in ("platform", "song_id", "title", "artists", "album", "duration_ms", "has_timestamps")}
    _print(info)
    if a.project:
        h = S.open_dir(Path(a.project))
        pv = S.parse_from_song(h, song["platform"], song["song_id"])
        for w in pv["warnings"]:
            print(f"提示: {w}", file=sys.stderr)
        if pv["error"]:
            raise S.ServiceError(pv["error"])
        S.apply_lyrics(h, pv["preview_id"])
        print(f"已导入 {len(h.project.lyrics.lines)} 行", file=sys.stderr)
        for kind in a.with_track or []:
            text = pv["extra_tracks"].get(kind)
            if text:
                prev = S.preview_track(h, text, kind)
                S.apply_track(h, kind, prev["pairs"])
                print(f"已配对 {kind}: {len(prev['pairs'])} 行", file=sys.stderr)
    elif a.out:
        Path(a.out).write_text(song["tracks"].get("original", ""), encoding="utf-8")


def cmd_lines(a) -> None:
    h = S.open_dir(Path(a.project))
    for i, ln in enumerate(h.project.lyrics.lines):
        segs = " ".join(
            f"{s.surface}[{'/'.join(u.reading for u in s.units)}]" + ("!" if s.uncertain else "") +
            ("✓" if s.confirmed else "")
            for s in ln.segments if s.units)
        t = "" if ln.imported_start_ms is None else f"{ln.imported_start_ms}ms "
        flag = "" if ln.sing else "(不参与) "
        print(f"{ln.id} {flag}{t}{ln.kind}: {ln.text}")
        if a.readings and segs:
            print(f"      {segs}")


def cmd_readings(a) -> None:
    h = S.open_dir(Path(a.project))
    _print(S.prepare_readings(h, overwrite_rule=a.overwrite_rule))


def cmd_reading_set(a) -> None:
    h = S.open_dir(Path(a.project))
    units = [u for u in a.units.replace(" ", "/").split("/") if u] if a.units else None
    S.set_segment_reading(h, a.line, a.segment, a.reading, units, confirm=not a.no_confirm)
    _print("已更新")


def cmd_ai_prompt(a) -> None:
    h = S.open_dir(Path(a.project))
    out = S.ai_prompt(h, a.lines)
    if a.out:
        Path(a.out).write_text(out["prompt"], encoding="utf-8")
        print(f"提示词已写入 {a.out}（快照 {out['snapshot_id']}）", file=sys.stderr)
    else:
        print(out["prompt"])


def cmd_ai_apply(a) -> None:
    h = S.open_dir(Path(a.project))
    text, _, _ = _read_text(a.file)
    val = S.ai_validate(h, text)
    rep = val["report"]
    for ln in rep.get("lines", []):
        reasons = "; ".join(ln.get("reasons", []))
        print(f"{ln['line_id']}: {ln['status']} {reasons}", file=sys.stderr)
    for w in rep.get("warnings", []):
        print(f"警告: {w}", file=sys.stderr)
    for e in rep.get("errors", []):
        print(f"错误: {e}", file=sys.stderr)
    if a.dry_run:
        return
    _print(S.ai_apply(h, val["report_id"], a.lines))


def cmd_audio(a) -> None:
    h = S.open_dir(Path(a.project))
    asset = S.add_media(h, Path(a.file), a.role, filename=Path(a.file).name,
                        source_kind="upload" if a.role == "original" else "import")
    _print({"id": asset.id, "role": asset.role, "duration_ms": asset.duration_ms, "sample_rate": asset.sample_rate,
            "sync_report": asset.sync_report})


def cmd_separate(a) -> None:
    h = S.open_dir(Path(a.project))
    out = S.run_separation(h, a.preset, progress=_progress, device=a.device)
    print(file=sys.stderr)
    _print(out)


def cmd_calibrate(a) -> None:
    h = S.open_dir(Path(a.project))
    if a.mark:
        S.calibration_op(h, "mark", line_id=a.mark[0], marked_ms=int(a.mark[1]))
    elif a.shift is not None:
        S.calibration_op(h, "shift", user_shift_ms=a.shift)
    elif a.confirm_zero:
        S.calibration_op(h, "confirm-zero")
    elif a.check:
        S.calibration_op(h, "check", line_id=a.check[0], marked_ms=int(a.check[1]))
    elif a.undo:
        S.calibration_op(h, "undo")
    view = S.project_view(h)
    cal = h.project.calibration
    _print({"embedded_offset_raw": h.project.lyrics.embedded_offset_raw,
            "embedded_shift_ms": h.project.lyrics.embedded_shift_ms,
            "user_shift_ms": cal.user_shift_ms, "confirmed": cal.confirmed,
            "reference_line_id": cal.reference_line_id, "marked_ms": cal.marked_ms,
            "checks": [c.model_dump() for c in cal.checks],
            "issues": [i["message"] for i in view["view"]["calibration_issues"]]})


def cmd_anchor(a) -> None:
    h = S.open_dir(Path(a.project))
    ms = None if a.ms.lower() == "none" else int(a.ms)
    S.set_line_anchor(h, a.line, ms, hard=not a.soft, tolerance_ms=a.tolerance)
    _print("已设置" if ms is not None else "已清除")


def cmd_mode(a) -> None:
    h = S.open_dir(Path(a.project))
    S.update_settings(h, mode=a.mode)
    view = S.project_view(h)
    _print({"mode": a.mode, "notice": view["view"]["mode_notice"],
            "stale_results": [r["id"] for r in view["view"]["results"] if r["stale"]]})


def cmd_config(a) -> None:
    h = S.open_dir(Path(a.project))
    if a.set:
        S.update_settings(h, config=_parse_set(a.set))
    _print(h.project.config.model_dump(mode="json"))


def cmd_align(a) -> None:
    h = S.open_dir(Path(a.project))
    cfg = _parse_set(a.set)
    if a.backend:
        cfg["backend"] = a.backend
    r = S.run_align(h, line_ids=a.lines, audio_role=a.role, config=cfg or None, progress=_progress)
    print(file=sys.stderr)
    failed = [u for u in r.units if u.status != "ok"]
    _print({"result_id": r.id, "units": len(r.units), "failed": len(failed), "issues": len(r.issues),
            "coverage_full": r.coverage.full, "parent_result_id": r.parent_result_id})
    for i in r.issues[: a.show_issues]:
        print(f"  [{i.severity}] {i.code} {i.line_id or ''}: {i.message}", file=sys.stderr)


def cmd_results(a) -> None:
    h = S.open_dir(Path(a.project))
    view = S.project_view(h)
    for r in view["view"]["results"]:
        active = "*" if r["id"] == h.project.active_result_id else " "
        stale = f" 过期: {r['stale_reason']}" if r["stale"] else ""
        cov = "" if r["coverage"]["full"] else " (局部)"
        print(f"{active} {r['id']} {r['created']} {r['mode']} {r['audio_role']} units={r['n_units']} "
              f"failed={r['n_failed']} issues={r['n_issues']} manual={r['n_manual']}{cov}{stale}")
    if a.activate:
        S.activate_result(h, a.activate)
        print(f"已激活 {a.activate}", file=sys.stderr)


def cmd_import_result(a) -> None:
    h = S.open_dir(Path(a.project))
    text, _, _ = _read_text(a.file)
    r = S.import_result_json(h, text)
    _print({"result_id": r.id, "stale": r.stale, "stale_reason": r.stale_reason})


def cmd_show(a) -> None:
    h = S.open_dir(Path(a.project))
    r = S.get_result(h, a.result) if a.result else h.project.result()
    if r is None:
        raise S.ServiceError("没有对齐结果")
    texts = {ln.id: ln.text for ln in h.project.lyrics.lines}
    cur = None
    for u in r.units:
        if u.line_id != cur:
            cur = u.line_id
            print(f"{cur}: {texts.get(cur, '')}")
        t = f"{u.start_ms}-{u.end_ms}" if u.start_ms is not None else f"— ({u.reason})"
        lock = " 🔒" if u.locked else ""
        print(f"   {u.unit_id} {u.reading:<4} {t} {u.status}{lock} {' '.join(u.flags)}")
    for i in r.issues:
        print(f"[{i.severity}] {i.code} {i.line_id or ''} {i.message}")


def cmd_edit(a) -> None:
    from .project import edits

    h = S.open_dir(Path(a.project))
    r = S.get_result(h, a.result) if a.result else h.project.result()
    if r is None:
        raise S.ServiceError("没有对齐结果")
    orig = h.project.asset("original")
    if a.clear:
        ut = edits.clear_manual(r, a.unit)
    elif a.lock is not None:
        ut = edits.set_lock(r, a.unit, a.lock == "on")
    else:
        ut = edits.set_manual(r, a.unit, a.start, a.end, locked=True,
                              duration_ms=orig.duration_ms if orig else None)
    h.save()
    _print(ut.model_dump(mode="json", exclude={"manual_history"}))


def cmd_adopt(a) -> None:
    h = S.open_dir(Path(a.project))
    target = a.result or h.project.active_result_id
    r = S.adopt_lines(h, target, a.lines or [], from_result_id=a.from_result, candidate_id=a.candidate)
    _print({"result_id": r.id, "adoptions": r.stats.get("adoptions")})


def cmd_export(a) -> None:
    h = S.open_dir(Path(a.project))
    out = S.export(h, a.format, a.result)
    for w in out.warnings:
        print(f"注意: {w}", file=sys.stderr)
    path = Path(a.out) if a.out else h.dir / "exports" / out.filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(out.content, encoding="utf-8")
    print(f"已写入 {path}", file=sys.stderr)


def cmd_mix(a) -> None:
    h = S.open_dir(Path(a.project))
    out = S.export_mix(h, {"vocal_keep_pct": a.vocal, "instrumental_pct": a.inst, "master": a.master,
                           "limiter": a.limiter}, Path(a.out) if a.out else None)
    _print(out)


def cmd_video(a) -> None:
    h = S.open_dir(Path(a.project))
    out = S.export_video(h, {"vocal_keep_pct": a.vocal, "instrumental_pct": a.inst, "master": a.master})
    _print({"file": str(h.dir / "exports" / out["filename"]), "report": out["report"]})


def cmd_burn(a) -> None:
    h = S.open_dir(Path(a.project))
    out = S.karaoke_burn(h, background=a.background, audio=a.audio, quality=a.quality, vocal_keep_pct=a.vocal,
                         progress=_progress)
    print(file=sys.stderr)
    _print({"file": str(h.dir / "exports" / out["filename"]), "warnings": out["warnings"]})


def cmd_package(a) -> None:
    from .project import store

    h = S.open_dir(Path(a.project))
    out = store.export_package(h.project, h.dir, Path(a.out), include_audio=not a.no_audio)
    _print(f"已写入 {out}")


def cmd_unpack(a) -> None:
    from .project import store

    p = store.import_package(Path(a.package), Path(a.dest))
    _print(f"已解包项目 {p.name} 到 {a.dest}")


def cmd_eval(a) -> None:
    """Compare one or more runs (e.g. base / lrc / separated / reading-fixed) with a reference."""
    from .align.evaluate import compare_runs, load_reference
    from .models import AlignmentResult

    runs = {}
    for item in a.hyp:
        label, sep, path = item.partition("=")
        if not sep:
            label, path = Path(item).stem, item
        runs[label] = AlignmentResult.model_validate_json(Path(path).read_text(encoding="utf-8"))
    _print(compare_runs(runs, load_reference(a.ref), gross_ms=a.gross_ms, match=a.match))


def cmd_backends(a) -> None:
    from .align.backends import list_backends
    from .audio.separation import preset_dicts

    _print({"backends": list_backends(), "separation_presets": preset_dicts()})


def cmd_cache(a) -> None:
    import shutil

    from .project import store

    root = store.cache_dir()
    size = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
    if a.clear:
        shutil.rmtree(root, ignore_errors=True)
        _print(f"已清理缓存 {root}（{size / 1e6:.1f} MB）；项目数据不受影响")
    else:
        _print(f"缓存目录 {root}：{size / 1e6:.1f} MB")


def cmd_serve(a) -> None:
    import asyncio

    import uvicorn

    from .web.server import create_app

    extra = set(a.allow_host or [])
    if a.host not in ("127.0.0.1", "localhost", "::1", "0.0.0.0", "::"):
        extra.add(a.host)  # listening on a named address: requests to it are accepted
    app = create_app(Path(a.root) if a.root else None, allowed_hosts=extra)
    port = free_port(a.host) if a.port == "auto" else int(a.port)
    shown = "127.0.0.1" if a.host in ("0.0.0.0", "::") else a.host
    url = f"http://{'[' + shown + ']' if ':' in shown else shown}:{port}"
    print(f"MiliKara WebUI: {url}", file=sys.stderr)
    if a.open:
        _open_when_ready(url)
    server = uvicorn.Server(uvicorn.Config(app, host=a.host, port=port, log_level="warning"))

    async def serve() -> None:
        asyncio.get_running_loop().set_exception_handler(quiet_connection_resets)
        await server.serve()

    asyncio.run(serve())


def quiet_connection_resets(loop, context: dict) -> None:
    """Event loop errors, except a browser that closed its connection first: on Windows the loop
    reports that (ConnectionResetError, WinError 10054) after the request was answered; nothing
    went wrong."""
    if isinstance(context.get("exception"), (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        return
    loop.default_exception_handler(context)


def free_port(host: str = "127.0.0.1", first: int = 8765, last: int = 8799) -> int:
    """The first port in ``first..last`` nothing is listening on (the launchers' ``--port auto``)."""
    import socket

    for port in range(first, last + 1):
        with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET) as s:
            try:
                s.bind((host, port))
            except OSError:
                continue
            return port
    raise SystemExit(f"端口 {first}–{last} 都被占用了，请用 --port 指定一个")


def _open_when_ready(url: str) -> None:
    """Open the browser once the server answers (in the background; gives up after a minute)."""
    import threading
    import time
    import urllib.request
    import webbrowser

    def wait() -> None:
        for _ in range(120):
            try:
                urllib.request.urlopen(url + "/api/info", timeout=2).close()
            except OSError:
                time.sleep(0.5)
                continue
            webbrowser.open(url)
            return

    threading.Thread(target=wait, daemon=True).start()


# ---------------------------------------------------------------------------


def _port(v: str):
    if v == "auto":
        return v
    try:
        return int(v)
    except ValueError:
        raise argparse.ArgumentTypeError("端口应为数字或 auto") from None


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="milikara", description="MiliKara：用歌曲和已知歌词做逐字卡拉OK字幕与视频")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name: str, fn, help: str, project: bool = True) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help)
        if project:
            p.add_argument("project", help="项目目录")
        p.set_defaults(fn=fn)
        return p

    p = add("init", cmd_init, "创建项目")
    p.add_argument("--name")
    p.add_argument("--mode", choices=["plain", "lrc"], default="plain")

    p = add("mode", cmd_mode, "切换模式（保留输入与人工修改）")
    p.add_argument("mode", choices=["plain", "lrc"])

    p = add("lyrics", cmd_lyrics, "导入歌词（文件或 - 从标准输入粘贴）")
    p.add_argument("file")
    p.add_argument("--mode", choices=["plain", "lrc"])

    p = add("track", cmd_track, "配对翻译/音译轨")
    p.add_argument("file")
    p.add_argument("--kind", choices=["translation", "romanization"], default="translation")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("fetch", help="从网易云/QQ 音乐链接获取歌词（仅歌词与元数据）")
    p.add_argument("link")
    p.add_argument("--project", help="直接导入到项目")
    p.add_argument("--with-track", action="append", choices=["translation", "romanization"])
    p.add_argument("--out", help="把原文歌词写入文件")
    p.set_defaults(fn=cmd_fetch)

    p = add("lines", cmd_lines, "列出歌词行")
    p.add_argument("--readings", action="store_true")

    p = add("readings", cmd_readings, "规则注音（不覆盖人工/AI/已确认读音）")
    p.add_argument("--overwrite-rule", action=argparse.BooleanOptionalAction, default=True,
                   help="重新生成规则读音（默认）；--no-overwrite-rule 保留现有的规则读音")

    p = add("reading-set", cmd_reading_set, "手工设置片段读音")
    p.add_argument("line")
    p.add_argument("segment")
    p.add_argument("reading")
    p.add_argument("--units", help="单元，用 / 分隔，如 き/み")
    p.add_argument("--no-confirm", action="store_true")

    p = add("ai-prompt", cmd_ai_prompt, "生成网页聊天用的注音提示词")
    p.add_argument("--lines", nargs="*")
    p.add_argument("--out")

    p = add("ai-apply", cmd_ai_apply, "校验并应用 AI 回传的注音补丁")
    p.add_argument("file")
    p.add_argument("--lines", nargs="*")
    p.add_argument("--dry-run", action="store_true")

    p = add("audio", cmd_audio, "添加原曲或已有分轨（也可以是视频，会提取音轨）")
    p.add_argument("file")
    p.add_argument("--role", choices=["original", "vocals", "instrumental"], default="original")

    p = add("separate", cmd_separate, "人声分离（需要 milikara[separation]）")
    p.add_argument("--preset", default="melband-roformer", choices=_preset_names(),
                   help="分离预设（kara-align backends 列出说明）")
    p.add_argument("--device", choices=["auto", "cpu"], default="auto",
                   help="auto 使用 GPU/MPS（若可用）；cpu 更慢但可避开部分环境下的 MPS 卡死")

    p = add("calibrate", cmd_calibrate, "LRC 首音校准")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--mark", nargs=2, metavar=("LINE", "MS"), help="把某行首个发音标在 MS")
    g.add_argument("--shift", type=int, help="直接设置人工全局平移（ms，正值=歌词后移）")
    g.add_argument("--confirm-zero", action="store_true")
    g.add_argument("--check", nargs=2, metavar=("LINE", "MS"), help="在中段/末段检查")
    g.add_argument("--undo", action="store_true")

    p = add("anchor", cmd_anchor, "设置单行绝对锚点（原音频时间；none 清除）")
    p.add_argument("line")
    p.add_argument("ms")
    p.add_argument("--soft", action="store_true")
    p.add_argument("--tolerance", type=int, default=80)

    p = add("config", cmd_config, "查看/修改对齐配置 (--set decode.soft_sigma_ms=300)")
    p.add_argument("--set", action="append")

    p = add("align", cmd_align, "运行对齐（--lines 局部重跑）")
    p.add_argument("--lines", nargs="*")
    p.add_argument("--role", choices=["original", "vocals"])
    p.add_argument("--backend")
    p.add_argument("--set", action="append")
    p.add_argument("--show-issues", type=int, default=20)

    p = add("results", cmd_results, "列出结果")
    p.add_argument("--activate")

    p = add("import-result", cmd_import_result, "导入 alignment.json 作为（非激活）结果")
    p.add_argument("file")

    p = add("show", cmd_show, "显示结果")
    p.add_argument("--result")

    p = add("edit", cmd_edit, "人工修改单元时间")
    p.add_argument("unit")
    p.add_argument("--start", type=int)
    p.add_argument("--end", type=int)
    p.add_argument("--clear", action="store_true")
    p.add_argument("--lock", choices=["on", "off"])
    p.add_argument("--result")

    p = add("adopt", cmd_adopt, "从局部重跑或候选采用某些行")
    p.add_argument("--from-result")
    p.add_argument("--candidate")
    p.add_argument("--lines", nargs="*")
    p.add_argument("--result")

    p = add("export", cmd_export, "导出")
    p.add_argument("format", choices=["alignment", "prepared", "project", "csv", "lrc-line", "lrc-unit",
                                      "lrc-calibrated", "karaoke-ass"])
    p.add_argument("--out")
    p.add_argument("--result")

    p = add("mix", cmd_mix, "导出人声保留混音 WAV")
    p.add_argument("--vocal", type=float, default=100.0, help="人声保留 p%%")
    p.add_argument("--inst", type=float, default=100.0, help="伴奏 q%%")
    p.add_argument("--master", type=float, default=1.0)
    p.add_argument("--limiter", choices=["normalize_peak", "none"], default="normalize_peak")
    p.add_argument("--out")

    p = add("video", cmd_video, "导出降低人声的视频（需要以视频作为原曲并完成分离）")
    p.add_argument("--vocal", type=float, default=20.0, help="人声保留 p%%")
    p.add_argument("--inst", type=float, default=100.0, help="伴奏 q%%")
    p.add_argument("--master", type=float, default=1.0)

    p = add("burn", cmd_burn, "把卡拉OK字幕烧录进视频（字幕样式见 WebUI“卡拉OK字幕”页；ASS 用 export karaoke-ass）")
    p.add_argument("--background", choices=["auto", "black"], default="auto", help="auto：有视频时用原视频，否则纯黑")
    p.add_argument("--audio", choices=["original", "mix", "none"], default="original",
                   help="mix：降低人声（需要分轨），保留比例见 --vocal，默认用字幕样式中的设置；none：无声")
    p.add_argument("--vocal", type=float, help="--audio mix 时的人声保留 p%%（0–100）")
    p.add_argument("--quality", choices=["standard", "high"], default="standard")

    p = add("package", cmd_package, "导出便携项目包")
    p.add_argument("out")
    p.add_argument("--no-audio", action="store_true")

    p = sub.add_parser("unpack", help="解包便携项目包")
    p.add_argument("package")
    p.add_argument("dest")
    p.set_defaults(fn=cmd_unpack)

    p = sub.add_parser("eval", help="与人工标注比较精度")
    p.add_argument("--ref", required=True)
    p.add_argument("--hyp", required=True, action="append",
                   help="alignment.json，可重复；可写成 label=path 以比较多种配置")
    p.add_argument("--gross-ms", type=int, default=300)
    p.add_argument("--match", choices=["id", "order"], default="id",
                   help="按单元 ID 或按顺序匹配参考标注")
    p.set_defaults(fn=cmd_eval)

    p = sub.add_parser("backends", help="列出对齐后端与分离预设")
    p.set_defaults(fn=cmd_backends)

    p = sub.add_parser("cache", help="查看/清理缓存")
    p.add_argument("--clear", action="store_true")
    p.set_defaults(fn=cmd_cache)

    p = sub.add_parser("serve", help="启动本地 WebUI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=_port, default=8765, help="端口（auto：从 8765 起第一个空闲端口）")
    p.add_argument("--open", action="store_true", help="启动后在浏览器中打开")
    p.add_argument("--root", help="项目根目录（默认 ~/.kara_align/projects）")
    p.add_argument("--allow-host", action="append", default=[],
                   help="除 127.0.0.1 / localhost 外还接受的主机名（例如在局域网中访问时本机的地址）")
    p.set_defaults(fn=cmd_serve)
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    if argv is None and Path(sys.argv[0]).stem.lower() == "kirakara":
        print("提示：KiraKara 已改名为 MiliKara，以后请使用 milikara 命令（kirakara 仍然可用）", file=sys.stderr)
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130
    except Exception as e:  # show the real reason, no traceback noise
        from .project.store import ProjectError

        known = (S.ServiceError, ProjectError, FileNotFoundError, ValueError, RuntimeError, KeyError)
        if isinstance(e, known) or type(e).__name__.endswith("Error"):
            print(f"错误: {e}", file=sys.stderr)
            return 1
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
