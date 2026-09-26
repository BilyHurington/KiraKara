// 极简模式：任务开始后马上确认“第一句从哪里开始唱”（LRC 模式）。
// 视频的声音常常不是歌词计时所用的那一版，所以这一步不能省；
// 确认后任务自动继续（AI 注音、分离、对齐、生成视频），不用再管。

import { ChevronLeft, ChevronRight, Play, PlayCircle, Square, ZoomIn } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { fmtMs, fmtSigned, parseTime } from '@/lib/format';
import type { PipelineTask } from '@/lib/types';
import { run, toast, useApp } from '@/store/app';
import { isEnter, isEscape } from '@/lib/keys';
import { confirmCalibration, loadTasks } from '@/store/simple';
import { Button, Callout, Dialog, Input } from '@/components/ui';

interface Peaks { per_second: number; mins: number[]; maxs: number[] }

const SPANS = [6000, 12000, 24000];

export function CalibrateDialog({ task, onClose }: { task: PipelineTask; onClose: () => void }) {
  const c = task.calibration!;
  const pid = task.project_id!;
  // start from what is already known: a confirmed mark, an offset set in the detailed mode, or the LRC time
  const start = c.confirmed_ms ?? c.current_ms ?? c.lrc_ms;
  const [marker, setMarker] = useState(start);
  const [span, setSpan] = useState(SPANS[1]);
  const [view0, setView0] = useState(Math.max(0, start - SPANS[1] * 0.35));
  const [peaks, setPeaks] = useState<Peaks | null>(null);
  const [playhead, setPlayhead] = useState<number | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const audio = useRef<HTMLAudioElement | null>(null);
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const raf = useRef(0);
  const stopAt = useRef<number | null>(null);
  const shift = marker - c.lrc_ms;
  const duration = c.duration_ms ?? Infinity;
  const src = c.asset_id ? `/api/projects/${pid}/audio/${c.asset_id}/playback.wav` : null;

  useEffect(() => {
    if (!c.asset_id) return;
    void run(async () => setPeaks(await api.get<Peaks>(`/api/projects/${pid}/audio/${c.asset_id}/peaks?per_second=100`)), '读取波形失败');
  }, [pid, c.asset_id]);

  const stop = useCallback(() => {
    audio.current?.pause();
    cancelAnimationFrame(raf.current);
    stopAt.current = null;
    setPlayhead(null);
  }, []);
  useEffect(() => () => stop(), [stop]);

  const play = (fromMs: number, seconds = 8) => {
    if (!src) return;
    if (!audio.current) audio.current = new Audio(src);
    const a = audio.current;
    const from = Math.max(0, fromMs);
    stop();
    stopAt.current = from + seconds * 1000;
    // keep the playing part visible
    if (from < view0 || from > view0 + span * 0.8) setView0(Math.max(0, from - span * 0.25));
    try {
      a.currentTime = from / 1000;
      void a.play()?.catch?.(() => {});
    } catch { /* no audio in this environment */ }
    const tick = () => {
      const t = a.currentTime * 1000;
      setPlayhead(t);
      if (stopAt.current !== null && t >= stopAt.current) { stop(); return; }
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
  };

  // the canvas follows its size and the theme: redraw when either changes
  const [sizeKey, setSizeKey] = useState(0);
  const theme = useApp((s) => s.theme);
  useEffect(() => {
    const cv = canvas.current;
    if (!cv || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => setSizeKey((k) => k + 1));
    ro.observe(cv);
    return () => ro.disconnect();
  }, []);

  // draw the waveform window
  useEffect(() => {
    const cv = canvas.current;
    const ctx = cv?.getContext?.('2d');
    if (!cv || !ctx) return;
    const w = cv.clientWidth * devicePixelRatio;
    const h = cv.clientHeight * devicePixelRatio;
    cv.width = w;
    cv.height = h;
    ctx.clearRect(0, 0, w, h);
    const css = getComputedStyle(cv);
    const col = (v: string, d: string) => css.getPropertyValue(v).trim() || d;
    if (peaks) {
      ctx.fillStyle = col('--c-muted', '#888');
      const ps = peaks.per_second;
      for (let x = 0; x < w; x++) {
        const t0 = view0 + (x / w) * span;
        const t1 = view0 + ((x + 1) / w) * span;
        const i0 = Math.floor((t0 / 1000) * ps);
        const i1 = Math.max(i0 + 1, Math.floor((t1 / 1000) * ps));
        let lo = 0;
        let hi = 0;
        for (let i = i0; i < i1 && i < peaks.maxs.length; i++) {
          if (i < 0) continue;
          lo = Math.min(lo, peaks.mins[i]);
          hi = Math.max(hi, peaks.maxs[i]);
        }
        const y0 = h / 2 - hi * h * 0.48;
        const y1 = h / 2 - lo * h * 0.48;
        ctx.fillRect(x, y0, 1, Math.max(1, y1 - y0));
      }
    }
    const xOf = (ms: number) => ((ms - view0) / span) * w;
    const line = (ms: number, color: string, dash: number[], width: number) => {
      const x = xOf(ms);
      if (x < 0 || x > w) return;
      ctx.save();
      ctx.strokeStyle = color;
      ctx.lineWidth = width * devicePixelRatio;
      ctx.setLineDash(dash.map((d) => d * devicePixelRatio));
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, h);
      ctx.stroke();
      ctx.restore();
    };
    line(c.lrc_ms, col('--c-subtle', '#999'), [4, 4], 1.5);
    line(marker, col('--c-accent', '#5b5bd6'), [], 2.5);
    if (playhead !== null) line(playhead, col('--c-fg', '#111'), [], 1);
  }, [peaks, view0, span, marker, playhead, c.lrc_ms, sizeKey, theme]);

  const pickAt = (clientX: number) => {
    const cv = canvas.current;
    if (!cv) return;
    const r = cv.getBoundingClientRect();
    const ms = Math.round(view0 + ((clientX - r.left) / r.width) * span);
    setMarker(Math.min(Math.max(0, ms), duration));
  };
  const dragging = useRef(false);

  const nudge = (d: number) => setMarker((m) => Math.min(Math.max(0, m + d), duration));
  // never past the end of the audio (at most the last window)
  const maxView = Number.isFinite(duration) ? Math.max(0, duration - span * 0.5) : Infinity;
  const pan = (d: number) => setView0((v) => Math.min(maxView, Math.max(0, v + d)));
  const zoom = () => {
    const next = SPANS[(SPANS.indexOf(span) + 1) % SPANS.length];
    const limit = Number.isFinite(duration) ? Math.max(0, duration - next * 0.5) : Infinity;
    setView0(Math.min(limit, Math.max(0, marker - next * 0.35)));
    setSpan(next);
  };

  const confirm = (body: { marked_ms?: number; plain?: boolean }) => run(async () => {
    setBusy(true);
    try {
      stop();
      try {
        await confirmCalibration(task.id, body);
      } catch (e) {
        // e.g. the lyrics were edited meanwhile and a new first line was chosen: show it
        void loadTasks().catch(() => undefined);
        throw e;
      }
      toast('ok', body.plain ? '已改用普通模式，任务继续' : `已确认（偏移 ${fmtSigned(shift)}），任务继续`, '接下来全部自动完成');
      onClose();
    } finally {
      setBusy(false);
    }
  }, '确认失败');

  const commitDraft = () => {
    if (draft === null) return;
    const ms = parseTime(draft);
    setDraft(null);
    if (ms !== null) setMarker(Math.min(Math.max(0, ms), duration));
  };

  return (
    <Dialog
      open
      wide
      onOpenChange={(o) => { if (!o) { stop(); onClose(); } }}
      title={<>确认开头位置 · {task.name || task.media_filename}</>}
      description="视频的声音经常和歌词里的时间对不上。请把标记放在第一句开始唱的位置，之后所有歌词按同样的偏移对齐。确认后其余步骤全部自动完成（这一步就是详细模式里的“首音校准”）。"
      footer={
        <>
          <Button variant="ghost" className="mr-auto" disabled={busy} onClick={() => confirm({ plain: true })}>不用歌词时间（改普通模式）</Button>
          <Button variant="ghost" onClick={() => { stop(); onClose(); }}>稍后</Button>
          <Button variant="primary" loading={busy} onClick={() => confirm({ marked_ms: marker })}>确认并继续</Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="rounded-xl bg-surface-2 px-4 py-3">
          <div className="text-xs text-muted">第一句</div>
          <div className="mt-0.5 text-lg font-semibold">{c.line_text}</div>
          <div className="mt-1 text-xs text-muted">
            歌词里写的时间 <span className="tabular font-mono">{fmtMs(c.lrc_ms)}</span>
            {c.lines.length > 1 && <> · 之后：{c.lines.slice(1).map((l) => l.text).join(' / ')}</>}
          </div>
        </div>
        {!!c.lines_after_audio && (
          <Callout tone="warn" title={`有 ${c.lines_after_audio} 行歌词的时间在音频结束之后`}>
            视频可能是剪短的版本（例如 TV 版），而歌词是完整版。这些行不会对齐、也不会出现在字幕里；其余的行照常处理。
            如果整首都对不上，请检查视频和歌词是不是同一首歌、同一个版本。
          </Callout>
        )}
        {c.current_ms != null && c.confirmed_ms == null && (
          <p className="text-xs text-muted">标记从详细模式里已设置的偏移（{fmtSigned(c.current_ms - c.lrc_ms)}）开始，确认即可继续。</p>
        )}

        <div>
          <div className="mb-1.5 flex items-center gap-2 text-xs text-muted">
            <span className="inline-block h-3 w-0.5 bg-accent" />标记（第一句开始唱）
            <span className="ml-3 inline-block h-3 w-0 border-l-2 border-dashed border-subtle" />歌词里写的时间
            <span className="ml-auto flex items-center gap-1">
              <Button size="xs" variant="ghost" aria-label="往前看" icon={<ChevronLeft className="size-3.5" />} onClick={() => pan(-span / 2)} />
              <Button size="xs" variant="ghost" aria-label="往后看" icon={<ChevronRight className="size-3.5" />} onClick={() => pan(span / 2)} />
              <Button size="xs" variant="ghost" icon={<ZoomIn className="size-3.5" />} onClick={zoom}>{span / 1000} 秒</Button>
            </span>
          </div>
          <canvas
            ref={canvas}
            aria-label="波形：点击或拖动设置标记"
            className="h-32 w-full cursor-crosshair rounded-xl bg-surface-2 ring-1 ring-line"
            onPointerDown={(e) => { dragging.current = true; (e.target as HTMLElement).setPointerCapture?.(e.pointerId); pickAt(e.clientX); }}
            onPointerMove={(e) => { if (dragging.current) pickAt(e.clientX); }}
            onPointerUp={() => { dragging.current = false; }}
            onPointerCancel={() => { dragging.current = false; }}
            onLostPointerCapture={() => { dragging.current = false; }}
          />
          <div className="mt-1 flex justify-between font-mono text-[11px] text-subtle">
            <span>{fmtMs(view0, false)}</span><span>{fmtMs(view0 + span, false)}</span>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[13px] font-medium">标记</span>
          <Input aria-label="标记时间" className="h-8 w-28 font-mono text-[13px]" value={draft ?? fmtMs(marker)}
            onFocus={() => setDraft(fmtMs(marker))} onChange={(e) => setDraft(e.target.value)} onBlur={commitDraft}
            onKeyDown={(e) => { if (isEnter(e)) (e.target as HTMLInputElement).blur(); if (isEscape(e)) setDraft(null); }} />
          <span className="text-xs text-muted">偏移 <b className="tabular font-mono">{fmtSigned(shift)}</b></span>
          <span className="mx-1 h-5 w-px bg-line" />
          {[-100, -10, 10, 100].map((d) => (
            <Button key={d} size="xs" variant="secondary" className="tabular font-mono" onClick={() => nudge(d)}>{d > 0 ? '+' : '−'}{Math.abs(d)} ms</Button>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary" size="sm" icon={<Play className="size-4" />} disabled={!src} onClick={() => play(marker)}>从标记处播放</Button>
          <Button variant="outline" size="sm" icon={<PlayCircle className="size-4" />} disabled={!src} onClick={() => play(marker - 2000, 10)}>标记前 2 秒开始</Button>
          {c.check_line && (
            <Button variant="outline" size="sm" icon={<PlayCircle className="size-4" />} disabled={!src}
              title="按当前偏移播放中间的一句，听它是不是正好开始"
              onClick={() => play(c.check_line!.lrc_ms + shift, 6)}>
              试听中间一句「{c.check_line.text}」
            </Button>
          )}
          {playhead !== null && <Button variant="ghost" size="sm" icon={<Square className="size-3.5" />} onClick={stop}>停止</Button>}
        </div>

        {!src && <Callout tone="warn">没有找到音频，无法试听；可以直接按歌词时间确认或改用普通模式。</Callout>}
      </div>
    </Dialog>
  );
}
