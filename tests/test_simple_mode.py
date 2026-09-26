"""Simple mode: app settings, AI via CLI / API, automatic LRC offset and the task queue."""

import json
import os
import shutil
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest
import soundfile as sf

from kara_align import pipeline as P
from kara_align import service as S
from kara_align import settings as AS
from kara_align.align.backends.fake import ScriptedBackend
from kara_align.interfaces import CancelToken, Cancelled
from kara_align.reading import llm

SCRIPT = [
    ("ki", 1000, 1200), ("mi", 1200, 1400), ("to", 1400, 1700),
    ("a", 3000, 3200), ("ru", 3200, 3400), ("i", 3400, 3600), ("ta", 3600, 3900),
    ("so", 5000, 5200), ("ra", 5200, 5500),
]
# LRC times are 500 ms late: the automatic calibration must find -500
LRC = "[00:01.50]きみと\n[00:03.50]あるいた\n[00:05.50]そら\n"
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("KARA_ALIGN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(ScriptedBackend, "default_script", SCRIPT)
    llm._detect_cache.clear()


def _wav(path, seconds=7.0, sr=22050):
    rng = np.random.default_rng(0)
    sf.write(path, (rng.standard_normal(int(seconds * sr)) * 0.05).astype(np.float32), sr)
    return path


# ------------------------------------------------------------------ settings


def test_settings_merge_and_never_return_the_key():
    s = AS.update({"ai": {"provider": "openai", "model": "m", "api_key": "sk-secret"}, "simple": {"quality": "high"}})
    assert s.ai.api_key == "sk-secret" and s.simple.quality == "high"
    pub = AS.public(AS.load())
    assert "api_key" not in pub["ai"] and pub["ai"]["has_api_key"] is True
    assert "sk-secret" not in json.dumps(pub)
    AS.update({"ai": {"model": "m2", "api_key": ""}})  # empty key field: unchanged
    assert AS.load().ai.api_key == "sk-secret" and AS.load().ai.model == "m2"
    AS.update({"ai": {"clear_api_key": True}})
    assert AS.load().ai.api_key == ""
    assert oct(os.stat(AS.settings_path()).st_mode & 0o777) == "0o600"
    with pytest.raises(ValueError):
        AS.update({"ai": {"provider": "nope"}})


def test_simple_mode_default_subtitle_style_and_reset():
    k = AS.load().simple.karaoke
    # the look tuned on a real project: pink sweep, romaji over everything, shown 4 s early, held 2 s
    assert (k.text.color_sung, k.ruby.script, k.ruby.target) == ("#ED35B3", "romaji", "all")
    assert (k.layout.margin_v, k.layout.line_spacing, k.layout.margin_h) == (40, 0, 240)
    assert (k.timing.lead_in_ms, k.timing.hold_ms, k.timing.early_max_ms) == (4000, 2000, 6000)
    AS.update({"simple": {"karaoke": {"timing": {"hold_ms": 800}}}})  # nested partial update keeps the rest
    k2 = AS.load().simple.karaoke
    assert k2.timing.hold_ms == 800 and k2.timing.lead_in_ms == 4000 and k2.ruby.script == "romaji"
    AS.update({"simple": {"reset_karaoke": True}})
    assert AS.load().simple.karaoke == k


# ------------------------------------------------------------------ AI providers


def _fake_bin(tmp_path, monkeypatch, name, body):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text(f"#!{sys.executable}\n{body}")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")
    return p


CLAUDE = """import json, os, sys
if "--version" in sys.argv: print("9.9 (Claude Code)"); sys.exit(0)
prompt = sys.stdin.read()
assert "--tools" in sys.argv and sys.argv[sys.argv.index("--tools") + 1] == ""
assert not os.listdir(".")  # runs in an empty folder
print(json.dumps({"is_error": False, "result": "echo:" + prompt[:20], "total_cost_usd": 0.01,
                  "modelUsage": {"claude-x": {}}}))
"""
CODEX = """import sys
if "--version" in sys.argv: print("codex-cli 9.9"); sys.exit(0)
assert sys.argv[sys.argv.index("-s") + 1] == "read-only"
prompt = sys.stdin.read()
open(sys.argv[sys.argv.index("-o") + 1], "w").write("codex:" + prompt[:20])
print("model: gpt-test", file=sys.stderr)
"""


def test_claude_and_codex_cli(tmp_path, monkeypatch):
    _fake_bin(tmp_path, monkeypatch, "claude", CLAUDE)
    _fake_bin(tmp_path, monkeypatch, "codex", CODEX)
    found = {p["id"]: p for p in llm.detect_all(refresh=True)}
    assert found["claude"]["available"] and "9.9" in found["claude"]["version"]
    r = llm.ask(AS.AiSettings(provider="claude"), "你好，读音")
    assert r.text == "echo:你好，读音" and r.cost_usd == 0.01 and r.model == "claude-x"
    r = llm.ask(AS.AiSettings(provider="codex"), "hello codex")
    assert r.text == "codex:hello codex" and r.model == "gpt-test"


def test_cli_missing_timeout_and_cancel(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert not llm.detect("claude", refresh=True)["available"]
    with pytest.raises(llm.LlmError, match="没有找到 claude"):
        llm.ask(AS.AiSettings(provider="claude"), "x")
    _fake_bin(tmp_path, monkeypatch, "claude", "import time, sys\nsys.stdin.read()\ntime.sleep(30)\n")
    tok = CancelToken()
    threading.Timer(0.5, tok.cancel).start()
    t0 = time.time()
    with pytest.raises(Cancelled):
        llm.ask(AS.AiSettings(provider="claude"), "x", cancel=tok)
    assert time.time() - t0 < 5
    with pytest.raises(llm.LlmError, match="没有设置 AI"):
        llm.ask(AS.AiSettings(provider="none"), "x")


class _Api(BaseHTTPRequestHandler):
    seen: dict = {}

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Api.seen = {"path": self.path, "auth": self.headers.get("Authorization"), "body": body}
        out = json.dumps({"model": body["model"], "choices": [{"message": {"content": "api:" + body["messages"][0]["content"]}}]})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out.encode())

    def log_message(self, *a):
        pass


def test_openai_compatible_api(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), _Api)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_port}/v1"
        with pytest.raises(llm.LlmError, match="API Key"):
            llm.ask(AS.AiSettings(provider="openai", model="m", base_url=base, api_key_env="NO_SUCH_KEY"), "x")
        monkeypatch.setenv("MY_KEY", "sk-env")
        r = llm.ask(AS.AiSettings(provider="openai", model="m1", base_url=base, api_key_env="MY_KEY"), "hi")
        assert r.text == "api:hi" and r.model == "m1"
        assert _Api.seen["path"] == "/v1/chat/completions" and _Api.seen["auth"] == "Bearer sk-env"
    finally:
        srv.shutdown()


def _patch_for(prompt: str, *, drop_last=False) -> str:
    """A valid reading patch for the lines in a prompt (what a good model answers)."""
    snap = prompt.split('"snapshot": "')[1].split('"')[0]
    data = json.loads(prompt.rsplit("```json\n", 1)[1].split("\n```")[0])
    lines = []
    for ln in data["lines"]:
        segs = [{"surface": s["surface"], "reading": s.get("reading") or "",
                 "units": s.get("units") or []} if s.get("reading") else {"surface": s["surface"]}
                for s in ln["current_segments"]]
        lines.append({"id": ln["id"], "text": ln["text"], "segments": segs})
    if drop_last:
        lines = lines[:-1]
    return "```json\n" + json.dumps({"format": "kara-align/reading-patch", "version": 1, "snapshot": snap,
                                     "lines": lines}, ensure_ascii=False) + "\n```"


def test_ai_auto_validates_and_retries_with_the_problems(tmp_path, monkeypatch):
    h = S.create_dir(tmp_path / "proj", "t", "plain")
    pv = S.parse_lyrics(h, "君と\n空へ\n", origin="paste")
    S.apply_lyrics(h, pv["preview_id"])
    prompts = []

    def fake_ask(cfg, prompt, cancel=None, on_wait=None):
        prompts.append(prompt)
        return llm.LlmReply(text=_patch_for(prompt, drop_last=len(prompts) == 1), provider="claude",
                            cost_usd=0.02)

    monkeypatch.setattr(llm, "ask", fake_ask)
    out = S.ai_auto(h, cfg=AS.AiSettings(provider="claude"))
    assert len(prompts) == 2 and "缺少这些行" in prompts[1]  # retried, quoting what was missing
    assert out["report"]["ok"] and not out["report"]["missing_line_ids"]
    assert out["meta"]["cost_usd"] == 0.04 and len(out["meta"]["attempts"]) == 2
    summary = S.ai_apply(h, out["report_id"])
    assert set(summary["applied"]) | set(summary["unchanged"]) == {ln.id for ln in h.project.lyrics.sung_lines()}
    assert h.project.ai_roundtrips[-1].status == "applied"


# ------------------------------------------------------------------ task queue


def test_music_link_detection():
    assert P.is_music_link("https://music.163.com/song?id=423314091&uct2=abc")
    assert P.is_music_link("分享 初恋 https://y.qq.com/n/ryqq/songDetail/0039MnYb0qxYhV (来自QQ音乐)")
    assert P.is_music_link("netease: 423314091")
    assert not P.is_music_link("[00:01.00]きみと\n[00:02.00]あるいた")
    assert not P.is_music_link("君と\n歩いた\n道")


def _wait(q, tid, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        t = q.get(tid)
        if t.status not in ("preparing", "queued", "running"):
            return t
        time.sleep(0.2)
    raise AssertionError(f"task still {q.get(tid).status}: {q.get(tid).message}")


def _scripted_import(monkeypatch):
    orig = P.STAGE_FUNCS["import"]

    def wrapped(q, task, cfg, cancel, progress):
        out = orig(q, task, cfg, cancel, progress)
        S.update_settings(q.ws.get(task.project_id), config={"backend": "scripted"})
        return out

    monkeypatch.setitem(P.STAGE_FUNCS, "import", wrapped)


@needs_ffmpeg
def test_task_runs_from_upload_to_video(tmp_path, monkeypatch):
    _scripted_import(monkeypatch)
    AS.update({"simple": {"separate": False, "auto_export": True, "karaoke": {"ruby": {"script": "katakana"}}}})
    q = P.TaskQueue(S.Workspace(tmp_path / "projects"))
    t = q.add(media=_wav(tmp_path / "song.wav"), filename="song.wav", lyrics=LRC, mode="lrc")
    assert t.lyrics_kind == "text" and t.name == "song"
    # LRC mode: right after import + lyrics (before separation) it asks where the first line starts
    t = _wait(q, t.id)
    assert t.status == "waiting", (t.error, t.detail)
    assert [x.status for x in t.stages] == ["done", "done", "waiting", "pending", "pending", "pending", "pending"]
    c = t.calibration
    assert c["line_text"] == "きみと" and c["lrc_ms"] == 1500 and c["check_line"]["text"] == "あるいた"
    assert c["asset_id"] == q.ws.get(t.project_id).project.asset("original").id
    with pytest.raises(S.ServiceError):
        q.confirm_calibration(t.id)  # a position is required
    q.confirm_calibration(t.id, marked_ms=1000)  # sung 0.5 s before the LRC time
    t = _wait(q, t.id)
    assert t.status == "succeeded", (t.error, t.detail)
    st = {s.key: (s.status, s.message) for s in t.stages}
    assert st["readings"][0] == "skipped" and st["separate"][0] == "skipped"  # no AI / separation set up
    assert st["calibrate"] == ("done", "偏移 -500 ms（已确认）")
    assert st["import"] == ("done", "") and st["lyrics"] == ("done", "3 行")  # result notes only, no stale progress text
    assert st["export"][0] == "done" and t.progress == 1.0
    h = q.ws.get(t.project_id)
    assert h.project.calibration.user_shift_ms == -500
    r = h.project.result()
    got = [(u.reading, u.start_ms) for u in r.units]
    assert all(abs(s - e[1]) <= 45 for (_, s), e in zip(got, SCRIPT))
    k = h.project.karaoke  # the simple mode's full default style, with the one change made above
    assert k.ruby.script == "katakana" and k.ruby.target == "all" and k.text.color_sung == "#ED35B3"
    assert (k.timing.lead_in_ms, k.timing.hold_ms, k.layout.margin_v) == (4000, 2000, 40)
    video = h.dir / "exports" / t.outputs["video"]["filename"]
    assert video.exists() and video.stat().st_size > 1000
    # the queue survives a restart; finished tasks stay listed
    q2 = P.TaskQueue(S.Workspace(tmp_path / "projects"))
    assert q2.get(t.id).status == "succeeded"
    q.shutdown()
    q2.shutdown()


def test_plain_lyrics_in_lrc_mode_fall_back_and_failures_can_be_retried(tmp_path, monkeypatch):
    _scripted_import(monkeypatch)
    AS.update({"simple": {"separate": False, "auto_export": False}})
    q = P.TaskQueue(S.Workspace(tmp_path / "projects"))
    boom = {"n": 0}
    real_align = P.STAGE_FUNCS["align"]

    def flaky(*a):
        boom["n"] += 1
        if boom["n"] == 1:
            raise RuntimeError("model crashed")
        return real_align(*a)

    monkeypatch.setitem(P.STAGE_FUNCS, "align", flaky)
    t = q.add(media=_wav(tmp_path / "s.wav"), filename="s.wav", lyrics="きみと\nあるいた\nそら\n", mode="lrc",
              name="我的歌")
    t = _wait(q, t.id)
    assert t.status == "failed" and "model crashed" in t.error
    assert any("普通模式" in w for w in t.warnings) and t.mode == "plain"
    assert t.stage("align").status == "failed" and t.stage("lyrics").status == "done"
    q.retry(t.id)
    t = _wait(q, t.id)
    assert t.status == "succeeded" and t.stage("export").status == "skipped"
    assert q.ws.get(t.project_id).project.name == "我的歌"
    q.shutdown()


def test_tasks_http_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from kara_align.web.server import create_app

    monkeypatch.setattr(P.TaskQueue, "_worker", lambda self: None)  # nothing runs
    monkeypatch.setattr(P.TaskQueue, "_prepare", lambda self, t: None)
    app = create_app(tmp_path / "projects")
    client = TestClient(app)
    wav = _wav(tmp_path / "s.wav")
    with open(wav, "rb") as f:
        r = client.post("/api/tasks", files={"file": ("s.wav", f, "audio/wav")},
                        data={"lyrics": "https://music.163.com/song?id=1", "mode": "lrc"})
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["status"] == "preparing" and t["lyrics_kind"] == "link" and len(t["stages"]) == 7
    assert [x["id"] for x in client.get("/api/tasks").json()] == [t["id"]]
    assert client.post(f"/api/tasks/{t['id']}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/tasks/{t['id']}/retry").json()["status"] == "preparing"  # starts from import again
    assert client.post(f"/api/tasks/{t['id']}/calibration", json={"marked_ms": 1}).status_code == 400  # not waiting
    with open(wav, "rb") as f:
        bad = client.post("/api/tasks", files={"file": ("s.txt", f, "text/plain")}, data={"lyrics": "x"})
    assert bad.status_code == 400
    assert client.delete(f"/api/tasks/{t['id']}").json() == {"ok": True}
    assert client.get("/api/tasks").json() == []
    # settings + providers
    s = client.put("/api/settings", json={"ai": {"provider": "codex", "api_key": "sk-x"}}).json()
    assert s["ai"]["provider"] == "codex" and s["ai"]["has_api_key"] and "api_key" not in s["ai"]
    assert {p["id"] for p in client.get("/api/ai/providers").json()} == {"claude", "codex", "openai"}
    assert client.put("/api/settings", json={"simple": {"quality": "ultra"}}).status_code == 400


def test_waiting_task_does_not_block_the_queue_and_can_switch_to_plain(tmp_path, monkeypatch):
    _scripted_import(monkeypatch)
    AS.update({"simple": {"separate": False, "auto_export": False}})
    q = P.TaskQueue(S.Workspace(tmp_path / "projects"))
    a = q.add(media=_wav(tmp_path / "a.wav"), filename="a.wav", lyrics=LRC, mode="lrc", name="A")
    b = q.add(media=_wav(tmp_path / "b.wav"), filename="b.wav", lyrics="きみと\nあるいた\nそら\n", mode="plain", name="B")
    assert _wait(q, a.id).status == "waiting"
    assert _wait(q, b.id).status == "succeeded"  # ran while A waited
    assert q.get(b.id).stage("calibrate").status == "skipped"  # plain mode needs no offset
    # restart while waiting: still waiting, still answerable
    q.shutdown()
    q = P.TaskQueue(S.Workspace(tmp_path / "projects"))
    assert q.get(a.id).status == "waiting"
    q.confirm_calibration(a.id, plain=True)
    t = _wait(q, a.id)
    assert t.status == "succeeded" and t.mode == "plain" and t.stage("calibrate").message == "改用普通模式"
    assert q.ws.get(t.project_id).project.mode == "plain"
    with pytest.raises(S.ServiceError):
        q.confirm_calibration(a.id, marked_ms=1000)  # nothing to confirm any more
    q.shutdown()


def test_automatic_offset_suggestion_for_the_detailed_page(tmp_path):
    from kara_align.auto_calibrate import suggest_calibration

    h = S.create_dir(tmp_path / "proj", "t", "lrc")
    S.update_settings(h, config={"backend": "scripted"})
    pv = S.parse_lyrics(h, LRC, origin="paste")
    S.apply_lyrics(h, pv["preview_id"])
    S.add_audio(h, _wav(tmp_path / "s.wav"), "original")
    before = h.project.calibration.model_dump()
    sug = suggest_calibration(h)
    assert sug["shift_ms"] == -500 and sug["agree"] == 1.0 and sug["lines_checked"] == 3
    assert sug["audio_role"] == "original" and sug["vocal_onset_ms"] is None
    assert h.project.calibration.model_dump() == before and not h.project.results  # nothing saved


def test_platform_translation_is_paired_and_invisible_characters_dropped(tmp_path):
    h = S.create_dir(tmp_path / "proj", "t", "lrc")
    pv = S.parse_lyrics(h, "[00:01.50]きみと\ufeff\n[00:03.50]あるいた\u200b\n", origin="paste")
    S.apply_lyrics(h, pv["preview_id"])
    assert [ln.text for ln in h.project.lyrics.sung_lines()] == ["きみと", "あるいた"]
    assert P.pair_translation(h, "[by:someone]\n[00:01.50]和你\n[00:03.50]一起走过\n") == 2
    assert [ln.translation for ln in h.project.lyrics.sung_lines()] == ["和你", "一起走过"]
    assert P.pair_translation(h, None) == 0


def test_each_task_keeps_its_own_style_and_video_settings(tmp_path, monkeypatch):
    _scripted_import(monkeypatch)
    AS.update({"simple": {"separate": False, "auto_export": False}})
    q = P.TaskQueue(S.Workspace(tmp_path / "projects"))
    lyr = "きみと\nあるいた\nそら\n"
    a = q.add(media=_wav(tmp_path / "a.wav"), filename="a.wav", lyrics=lyr, mode="plain", name="A",
              style={"source": "template", "template": "glow", "color": "#FF8A1E", "secondary": "#FFC53D",
                     "translation": False, "song_info": True, "ruby": "romaji", "ruby_target": "kanji", "font_size": 72,
                     "video_audio": "mix", "vocal_keep_pct": 35})
    # the choices are remembered for the next task
    assert AS.load().simple.task_style.secondary == "#FFC53D"
    b = q.add(media=_wav(tmp_path / "b.wav"), filename="b.wav", lyrics=lyr, mode="plain", name="B",
              style={"source": "template", "template": "plain", "color": "#2F80ED"})
    # settings changed while both wait in the queue: neither task picks it up
    AS.update({"simple": {"auto_export": True, "video_audio": "mix", "karaoke": {"text": {"size": 60}}}})
    a, b = _wait(q, a.id), _wait(q, b.id)
    assert a.status == b.status == "succeeded", (a.error, b.error)
    assert (a.style_label, a.style_colors) == ("荧光", ["#FF8A1E", "#FFC53D"]) and b.style_label == "朴素"
    assert (a.video.video_audio, a.video.vocal_keep_pct) == ("mix", 35) and not a.video.auto_export
    assert (b.video.video_audio, b.video.vocal_keep_pct) == ("original", 20)  # the settings' level when not chosen
    assert a.stage("export").status == b.stage("export").status == "skipped"  # auto export was off when added
    ka, kb = q.ws.get(a.project_id).project.karaoke, q.ws.get(b.project_id).project.karaoke
    assert ka.glow.enabled and ka.glow.color_unsung == "#FFC53D" and ka.effects.kind == "sparkle"
    assert (ka.translation.enabled, ka.info.enabled, ka.ruby.script, ka.ruby.target) == (False, True, "romaji", "kanji")
    assert ka.output.vocal_keep_pct == 35 and ka.text.size == 72
    assert not kb.glow.enabled and kb.text.color_sung == "#2F80ED" and kb.text.size == 88
    # a saved style, and a preset that no longer exists
    from kara_align.karaoke.styles import save_style

    mine = save_style("我的", kb.model_dump(mode="json"))
    c = q.add(media=_wav(tmp_path / "c.wav"), filename="c.wav", lyrics=lyr, mode="plain",
              style={"source": "saved", "saved_id": mine["id"], "ruby": "off"})
    assert c.style_label == "我的" and not c.karaoke.ruby.enabled
    with pytest.raises(S.ServiceError):
        q.add(media=_wav(tmp_path / "d.wav"), filename="d.wav", lyrics=lyr, mode="plain",
              style={"source": "saved", "saved_id": "st_gone"})
    with pytest.raises(S.ServiceError):
        q.add(media=_wav(tmp_path / "e.wav"), filename="e.wav", lyrics=lyr, mode="plain",
              style={"source": "template", "color": "not-a-colour"})
    _wait(q, c.id)
    q.shutdown()


def test_theme_api(tmp_path):
    from fastapi.testclient import TestClient

    from kara_align.web.server import create_app

    client = TestClient(create_app(tmp_path / "projects"))
    t = client.get("/api/karaoke/themes").json()
    assert [x["id"] for x in t["templates"]] == ["plain", "glow"] and "#FF8A1E" in t["swatches"]
    r = client.post("/api/karaoke/theme", json={"template": "glow", "color": "#FF8A1E", "secondary": "#FFC53D"}).json()
    assert r["palette"]["glow_unsung"] == "#FFC53D" and r["style"]["glow"]["enabled"]
    assert client.post("/api/karaoke/theme", json={"template": "glow", "color": "zz"}).status_code == 400
