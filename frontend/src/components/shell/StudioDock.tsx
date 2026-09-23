// Persistent bottom "studio": transport, source/mix controls and the waveform.
// Slider changes only touch GainNodes — they never start server work.

import {
  ChevronDown, ChevronUp, Maximize2, Minus, Pause, Play, Plus, Repeat, SkipBack, Volume2, X,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { cn, fmtMs, ROLE_LABEL } from '@/lib/format';
import type { Source } from '@/lib/types';
import { player, usePlayer, usePlayhead } from '@/audio/player';
import { Waveform, type Overlays, type Peaks } from '@/audio/waveform';
import { waveformRef } from '@/audio/waveformRef';
import { ppath, resultFrom, run, setLayoutSize, toast, useApp, WAVE_HEIGHT } from '@/store/app';
import { setUnitTimes } from '@/store/edits';
import { Badge, IconButton, Kbd, ResizeHandle, Segmented, SliderField, Tip } from '@/components/ui';

export function StudioDock() {
  const pv = useApp((s) => s.pv);
  const pid = useApp((s) => s.pid);
  const open = useApp((s) => s.dockOpen);
  const waveHeight = useApp((s) => s.waveHeight);
  const p = usePlayer();

  // keep decoded buffers in sync with the project's audio assets
  const assetKey = pv ? pv.project.audio.map((a) => `${a.role}:${a.id}:${pv.view.audio[a.role as 'original']?.available}`).join('|') : '';
  useEffect(() => {
    if (pid && pv) void player.syncAssets(pid, pv);
    else player.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid, assetKey]);

  // mix settings follow the project
  useEffect(() => {
    if (pv) player.setMix({ p: pv.project.mix.vocal_keep_pct, q: pv.project.mix.instrumental_pct, master: pv.project.mix.master });
  }, [pv?.project.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (p.error) {
      toast('error', p.error);
      p.error = null;
    }
  }, [p.error, p]);

  if (!pv) return null;
  const hasAudio = !!pv.project.audio.find((a) => a.role === 'original');

  return (
    <footer className="relative z-20 shrink-0 border-t border-line bg-surface/95 backdrop-blur">
      {open && (
        <ResizeHandle
          axis="y" invert label="调整波形高度"
          value={waveHeight} onChange={(v) => setLayoutSize('waveHeight', v)}
          min={WAVE_HEIGHT.min} max={WAVE_HEIGHT.max} defaultValue={WAVE_HEIGHT.default}
          className="absolute -top-1 left-0"
        />
      )}
      <Transport hasAudio={hasAudio} />
      <div className={cn('grid transition-[grid-template-rows] duration-200', open ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]')}>
        <div className="min-h-0 overflow-hidden">
          <WaveArea />
        </div>
      </div>
    </footer>
  );
}

function Transport({ hasAudio }: { hasAudio: boolean }) {
  const p = usePlayer();
  const ms = usePlayhead();
  const open = useApp((s) => s.dockOpen);
  const pv = useApp((s) => s.pv)!;
  const sources = p.availableSources();
  const loading = Object.keys(p.loading);
  const canMix = sources.includes('mix');
  const [bus, setBus] = useState<{ bus_gain: number; peak_before: number } | null>(null);

  // bus gain for the current mix (only when both stems exist)
  const mix = pv.project.mix;
  useEffect(() => {
    if (!canMix) { setBus(null); return; }
    const t = setTimeout(() => {
      api.post<{ bus_gain: number; peak_before: number }>(ppath('/mix/preview-gain'), {
        vocal_keep_pct: p.mix.p, instrumental_pct: p.mix.q, master: p.mix.master, limiter: mix.limiter,
      }).then((r) => { setBus(r); player.setMix({ bus: r.bus_gain }); }).catch(() => setBus(null));
    }, 250);
    return () => clearTimeout(t);
  }, [canMix, p.mix.p, p.mix.q, p.mix.master, mix.limiter]); // eslint-disable-line react-hooks/exhaustive-deps

  const persistMix = () => run(() => api.patch(ppath(''), {
    mix: { vocal_keep_pct: player.mix.p, instrumental_pct: player.mix.q, master: player.mix.master },
  }));

  return (
    <>
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2">
      <div className="flex items-center gap-1">
        <IconButton label="回到开头" onClick={() => player.seek(0)} disabled={!sources.length}><SkipBack className="size-4" /></IconButton>
        <Tip content={<span>播放 / 暂停 <Kbd>Space</Kbd></span>}>
          <button
            onClick={() => player.toggle()}
            disabled={!sources.length}
            className="focus-ring grid size-10 place-items-center rounded-full bg-accent text-accent-fg shadow-md shadow-accent/30 transition hover:scale-105 disabled:opacity-40"
            aria-label={p.playing ? '暂停' : '播放'}
          >
            {p.playing ? <Pause className="size-4 fill-current" /> : <Play className="ml-0.5 size-4 fill-current" />}
          </button>
        </Tip>
        <Tip content={<span>循环所选区间 <Kbd>L</Kbd>（在波形上拖动选择）</span>}>
          <button
            onClick={() => { if (!player.toggleLoop()) toast('info', '请先在波形上拖选循环区间'); }}
            className={cn('focus-ring grid size-8 place-items-center rounded-lg transition', p.loop.on ? 'bg-warn-soft text-warn' : 'text-muted hover:bg-surface-2 hover:text-fg')}
            aria-label="循环"
          >
            <Repeat className="size-4" />
          </button>
        </Tip>
        {p.loop.start !== null && (
          <IconButton label="清除循环区间" size="xs" onClick={() => player.setLoop(null, null)}><X className="size-3.5" /></IconButton>
        )}
      </div>

      <div className="tabular font-mono text-sm">
        <span className="font-semibold text-fg">{fmtMs(ms)}</span>
        <span className="text-subtle"> / {fmtMs(p.durationMs)}</span>
      </div>

      {sources.length > 0 ? (
        <Segmented<Source>
          size="sm"
          value={p.source}
          onChange={(v) => player.setSource(v)}
          options={(['original', 'vocals', 'instrumental', 'mix'] as Source[]).map((s) => ({
            value: s, label: ROLE_LABEL[s], disabled: !sources.includes(s),
            title: s === 'mix' ? '人声 + 伴奏按下方比例混合（与导出同一规则）' : undefined,
          }))}
        />
      ) : (
        <span className="text-xs text-muted">{hasAudio ? '音频加载中…' : '尚未上传音频'}</span>
      )}

      <Segmented<string>
        size="sm"
        value={String(p.rate)}
        onChange={(v) => player.setRate(Number(v))}
        options={[{ value: '1', label: '1×' }, { value: '0.75', label: '0.75×' }, { value: '0.5', label: '0.5×' }]}
      />

      {loading.length > 0 && <Badge tone="info">加载 {loading.map((r) => ROLE_LABEL[r]).join('、')}…</Badge>}

      <div className="ml-auto">
        <IconButton label={open ? '收起波形' : '展开波形'} onClick={() => useApp.setState({ dockOpen: !open })}>
          {open ? <ChevronDown className="size-4" /> : <ChevronUp className="size-4" />}
        </IconButton>
      </div>
      </div>

      {/* mix row: long sliders with typeable values */}
      <div className="flex flex-wrap items-center gap-x-8 gap-y-2 border-t border-line/70 px-4 py-2">
        {canMix ? (
          <>
            <div className="min-w-[320px] flex-1 basis-80">
              <SliderField
                label={<HintLabel tip="线性幅度 ×p/100（“保留 p%”，不是“降低 p%”）；试听与导出使用同一规则">人声保留</HintLabel>}
                value={p.mix.p} onChange={(v) => player.setMix({ p: v })} onCommit={persistMix}
              />
            </div>
            <div className="min-w-[320px] flex-1 basis-80">
              <SliderField
                label={<HintLabel tip="伴奏线性幅度 ×q/100；人声 0% 时伴奏中仍可能残留人声">伴奏</HintLabel>}
                value={p.mix.q} onChange={(v) => player.setMix({ q: v })} onCommit={persistMix}
              />
            </div>
            {bus && (
              <Tip content={`共同母线增益（防削波，保持两轨比例）；混音峰值 ${bus.peak_before.toFixed(3)}`}>
                <span className="tabular shrink-0 text-xs whitespace-nowrap text-muted">母线 ×{bus.bus_gain.toFixed(3)}</span>
              </Tip>
            )}
          </>
        ) : (
          <span className="min-w-[320px] flex-1 text-xs text-subtle">
            {sources.length > 0
              ? '人声保留比例需要人声与伴奏两条分轨（可在“注音与分离”中分离或导入）；只有原曲时无法单独降低人声'
              : '上传音频后可在此试听'}
          </span>
        )}
        <div className="w-72 shrink-0">
          <SliderField
            label={<HintLabel tip="监听音量：仅影响本机试听，不写入导出"><Volume2 className="inline size-4 align-[-3px]" /></HintLabel>}
            value={Math.round(p.monitorVolume * 100)}
            onChange={(v) => player.setMonitorVolume(v / 100)}
          />
        </div>
      </div>
    </>
  );
}

function HintLabel({ tip, children }: { tip: string; children: React.ReactNode }) {
  return (
    <Tip content={tip}>
      <span className="cursor-help underline decoration-line-strong decoration-dotted underline-offset-4">{children}</span>
    </Tip>
  );
}

// ------------------------------------------------------------------ waveform

function useOverlays(): () => Overlays {
  // read the latest state on every frame without re-rendering React
  return () => {
    const s = useApp.getState();
    const pv = s.pv;
    const out: Overlays = { units: [], candUnits: [], lineStarts: [], loop: player.loop, selectedUnitId: s.selUnitId, marks: [] };
    if (!pv) return out;
    const lines = pv.project.lyrics.lines;
    const idx = new Map(lines.map((l, i) => [l.id, i]));
    const selLine = s.step === 'calibrate' ? s.calibLineId : s.selLineId;
    if (pv.project.mode === 'lrc') {
      for (const [lid, st] of Object.entries(pv.view.effective_starts)) {
        const ln = lines[idx.get(lid) ?? -1];
        out.lineStarts.push({ id: lid, ms: st.ms, kind: st.kind, label: `${(idx.get(lid) ?? 0) + 1} ${ln?.text ?? ''}`, selected: lid === selLine });
      }
      if (s.step === 'calibrate') {
        for (const c of pv.project.calibration.checks) out.marks.push({ ms: c.marked_ms, label: '检查' });
      }
    }
    const r = resultFrom(pv, s.resultId);
    if (r) {
      const surfaces = new Map<string, string>();
      for (const ln of lines) for (const seg of ln.segments) for (const u of seg.units) surfaces.set(u.id, u.reading);
      out.units = r.units.map((u) => ({
        id: u.unit_id, start: u.start_ms, end: u.end_ms, label: surfaces.get(u.unit_id) ?? u.reading,
        color: u.manual ? 'manual' : u.status === 'ok' ? 'ok' : u.status,
        editable: s.step === 'review' && !r.stale, locked: !!u.manual?.locked,
      }));
      if (pv.project.mode !== 'lrc') {
        for (const lt of r.lines) {
          if (lt.start_ms === null) continue;
          const ln = lines[idx.get(lt.line_id) ?? -1];
          out.lineStarts.push({ id: lt.line_id, ms: lt.start_ms, kind: 'soft', label: `${(idx.get(lt.line_id) ?? 0) + 1} ${ln?.text ?? ''}`, selected: lt.line_id === selLine });
        }
      }
      const cand = s.candidateId ? r.candidates.find((c) => c.id === s.candidateId) : null;
      if (cand) {
        out.candUnits = cand.units.map((u) => ({ id: `c:${u.unit_id}`, start: u.start_ms, end: u.end_ms, label: u.reading, color: 'candidate' }));
      } else if (s.compareWithId) {
        const cmp = resultFrom(pv, s.compareWithId);
        if (cmp) out.candUnits = cmp.units.map((u) => ({ id: `r:${u.unit_id}`, start: u.start_ms, end: u.end_ms, label: u.reading, color: 'compare' }));
      }
    }
    return out;
  };
}

function WaveArea() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const waveHeight = useApp((s) => s.waveHeight);
  const pid = useApp((s) => s.pid);
  const originalId = useApp((s) => s.pv?.project.audio.find((a) => a.role === 'original')?.id ?? null);
  const getOverlays = useOverlays();
  const [scroll, setScroll] = useState({ start: 0, size: 1 });

  useEffect(() => {
    const canvas = canvasRef.current!;
    const wf = new Waveform(canvas, {
      onSeek: (ms) => player.seek(ms),
      onSelectUnit: (id) => {
        const s = useApp.getState();
        const r = resultFrom(s.pv, s.resultId);
        const u = r?.units.find((x) => x.unit_id === id);
        useApp.setState({ selUnitId: id, selLineId: u?.line_id ?? s.selLineId });
      },
      onEditUnit: (id, start, end) => void setUnitTimes(id, start, end),
      onLoop: (a, b) => player.setLoop(a, b),
      getOverlays,
      getPlayhead: () => ({ ms: player.positionMs(), playing: player.playing }),
      onViewChange: () => {
        const st = waveformRef.current?.scrollState;
        if (!st) return;
        setScroll((prev) => (Math.abs(prev.start - st.start) > 1e-4 || Math.abs(prev.size - st.size) > 1e-4 ? st : prev));
      },
    });
    waveformRef.current = wf;
    let raf = 0;
    const loop = () => {
      wf.draw();
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(raf);
      wf.dispose();
      waveformRef.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const wf = waveformRef.current;
    if (!wf) return;
    if (!pid || !originalId) {
      wf.setPeaks(null);
      wf.durationMs = 0;
      return;
    }
    let cancelled = false;
    api.get<Peaks>(`/api/projects/${pid}/audio/${originalId}/peaks?per_second=200`)
      .then((pk) => { if (!cancelled) { wf.durationMs = 0; wf.setPeaks(pk); } })
      .catch((e) => toast('error', '加载波形失败', e.message));
    return () => { cancelled = true; };
  }, [pid, originalId]);

  const zoom = useMemo(() => ({
    in: () => waveformRef.current?.zoom(1 / 1.5),
    out: () => waveformRef.current?.zoom(1.5),
    all: () => waveformRef.current?.zoomAll(),
  }), []);

  return (
    <div className="relative px-4 pb-3">
      <div className="relative overflow-hidden rounded-xl ring-1 ring-line">
        <canvas ref={canvasRef} className="block w-full" style={{ height: waveHeight }} />
        <div className="absolute top-7 right-2 flex flex-col gap-1 rounded-lg bg-black/45 p-1 backdrop-blur">
          <WaveBtn label="放大" onClick={zoom.in}><Plus className="size-3.5" /></WaveBtn>
          <WaveBtn label="缩小" onClick={zoom.out}><Minus className="size-3.5" /></WaveBtn>
          <WaveBtn label="全曲" onClick={zoom.all}><Maximize2 className="size-3.5" /></WaveBtn>
        </div>
      </div>
      <ScrollBar state={scroll} />
      <div className="mt-1 flex flex-wrap gap-x-4 text-[11px] text-subtle">
        <span>点击定位 · 拖动选择循环区间 · 滚轮缩放 · Shift+滚轮平移 · 拖动上边缘调整高度</span>
        <span>在“人工检查”中选中单元后可拖动两端修改起止</span>
      </div>
    </div>
  );
}

function WaveBtn({ label, onClick, children }: { label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <Tip content={label} side="left">
      <button onClick={onClick} aria-label={label} className="grid size-6 place-items-center rounded-md text-white/80 hover:bg-white/15 hover:text-white">
        {children}
      </button>
    </Tip>
  );
}

function ScrollBar({ state }: { state: { start: number; size: number } }) {
  const ref = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x0: number; s0: number } | null>(null);
  useEffect(() => {
    const move = (e: MouseEvent) => {
      if (!drag.current || !ref.current) return;
      const w = ref.current.clientWidth;
      waveformRef.current?.scrollTo(drag.current.s0 + (e.clientX - drag.current.x0) / w);
    };
    const up = () => { drag.current = null; };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
    return () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); };
  }, []);
  if (state.size >= 0.999) return <div className="mt-1.5 h-1.5" />;
  return (
    <div
      ref={ref}
      className="relative mt-1.5 h-1.5 cursor-pointer rounded-full bg-surface-3"
      onMouseDown={(e) => {
        const rect = e.currentTarget.getBoundingClientRect();
        const frac = (e.clientX - rect.left) / rect.width - state.size / 2;
        waveformRef.current?.scrollTo(frac);
      }}
    >
      <div
        className="absolute inset-y-0 rounded-full bg-line-strong hover:bg-accent/60"
        style={{ left: `${state.start * 100}%`, width: `${Math.max(2, state.size * 100)}%` }}
        onMouseDown={(e) => { e.stopPropagation(); drag.current = { x0: e.clientX, s0: state.start }; }}
      />
    </div>
  );
}
