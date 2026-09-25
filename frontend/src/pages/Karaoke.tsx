// Step 7: karaoke subtitles — presets, settings, a live libass preview at any
// moment, ASS download and one-click burn-in (see docs/karaoke.md).

import { ArrowRight, ChevronLeft, ChevronRight, Crosshair, Download, Film, Flame, Loader2, Sparkles, Subtitles } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { fmtMs, parseTime } from '@/lib/format';
import type { FontFamily, Job, KaraokeStyle, ProjectView } from '@/lib/types';
import { player } from '@/audio/player';
import { revealOnWaveform } from '@/audio/waveformRef';
import { ppath, run, setPV, setStep, toast, trackJob, useApp, useJob, useProject, useResult } from '@/store/app';
import {
  Badge, Button, Callout, Card, CardBody, CardHeader, EmptyState, Input, PageHeader, Segmented, Select, SliderField, Tip,
} from '@/components/ui';
import { StylePanel } from '@/components/karaoke/StylePanel';
import { saveSettings } from '@/store/simple';

interface LineSpan { id: string; index: number; text: string; start: number; end: number }

export function KaraokePage() {
  const project = useProject()!;
  const result = useResult();
  const [style, setStyle] = useState<KaraokeStyle | null>(project.karaoke ?? null);
  const [fonts, setFonts] = useState<{ default: string; families: FontFamily[] }>({ default: '', families: [] });
  const dirty = useRef(false);

  useEffect(() => {
    void run(async () => {
      const [f, k] = await Promise.all([
        api.get<{ default: string; families: FontFamily[] }>('/api/fonts'),
        api.get<KaraokeStyle>(ppath('/karaoke')),
      ]);
      setFonts(f);
      setStyle(k);
    }, '加载字幕设置失败');
  }, [project.id]);

  // autosave (debounced); exports and burn-in always use the saved style
  useEffect(() => {
    if (!style || !dirty.current) return;
    const t = setTimeout(() => {
      void run(() => api.put(ppath('/karaoke'), style), '保存字幕样式失败');
    }, 600);
    return () => clearTimeout(t);
  }, [style]);

  const patch = (fn: (s: KaraokeStyle) => void) => {
    setStyle((prev) => {
      if (!prev) return prev;
      const next = structuredClone(prev);
      fn(next);
      dirty.current = true;
      return next;
    });
  };

  const change = (next: KaraokeStyle) => {
    dirty.current = true;
    setStyle(next);
  };

  const translated = useMemo(() => project.lyrics.lines.filter((l) => l.sing && l.kind === 'lyric' && l.translation?.trim()).length,
    [project.lyrics.lines]);
  const canFetch = project.sources.some((x) => (x.origin === 'netease' || x.origin === 'qq') && x.platform_song_id);
  const fetchTranslation = () => run(async () => {
    const pv = await api.post<ProjectView & { paired: number }>(ppath('/lyrics/fetch-translation'));
    setPV(pv);
    toast('ok', `已获取翻译：${pv.paired} 行`);
  }, '获取翻译失败');

  const lines: LineSpan[] = useMemo(() => {
    if (!result) return [];
    const text = new Map(project.lyrics.lines.map((l, i) => [l.id, { t: l.text, i }]));
    return result.lines
      .filter((l) => l.start_ms !== null && l.end_ms !== null)
      .map((l) => ({ id: l.line_id, index: (text.get(l.line_id)?.i ?? 0) + 1, text: text.get(l.line_id)?.t ?? '', start: l.start_ms!, end: l.end_ms! }))
      .sort((a, b) => a.start - b.start);
  }, [result, project.lyrics.lines]);

  if (!result) {
    return (
      <>
        <Header />
        <EmptyState icon={<Subtitles className="size-5" />} title="还没有对齐结果"
          description="卡拉OK字幕使用逐发音单元时间，请先完成对齐。"
          action={<Button variant="primary" onClick={() => setStep('align')} icon={<ArrowRight className="size-4" />}>去对齐</Button>} />
      </>
    );
  }
  if (!style) return <><Header /><div className="flex items-center gap-2 text-sm text-muted"><Loader2 className="size-4 animate-spin" />加载中…</div></>;

  return (
    <>
      <Header />
      {result.stale && <Callout tone="warn" className="mb-4" title="当前对齐结果已过期">{result.stale_reason}。字幕仍按该结果生成。</Callout>}
      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="min-w-0 space-y-6">
          <PreviewCard style={style} lines={lines} />
          <BurnCard style={style} patch={patch} />
        </div>
        <div className="space-y-6 xl:sticky xl:top-4">
          <Card>
            <CardHeader title="字幕样式" actions={
              <Tip content="极简模式新建的任务使用这套样式（含布局、注音、时间与特效）">
                <Button size="xs" variant="ghost" icon={<Sparkles className="size-3.5" />}
                  onClick={() => run(async () => { await saveSettings({ simple: { karaoke: style } }); toast('ok', '已设为极简模式默认样式'); }, '保存失败')}>
                  设为极简默认
                </Button>
              </Tip>
            } />
            <CardBody className="pt-3">
              <StylePanel style={style} onChange={change} fonts={fonts.families} defaultFont={fonts.default}
                defaultOpen={['colors', 'text']} storageKey="detail"
                translation={{ lines: translated, onFetch: canFetch ? fetchTranslation : undefined }} />
            </CardBody>
          </Card>
        </div>
      </div>
    </>
  );
}

function Header() {
  return (
    <PageHeader
      eyebrow="第 7 步（可选）"
      title="卡拉OK字幕"
      description="选择样式并预览任意时刻的画面；导出 ASS 字幕，或一键烧录成视频（没有视频时使用纯黑背景）。设置会自动保存。"
      actions={<Button onClick={() => setStep('export')} icon={<ArrowRight className="size-4" />}>下一步：导出</Button>}
    />
  );
}

// ------------------------------------------------------------------ preview

function PreviewCard({ style, lines }: { style: KaraokeStyle; lines: LineSpan[] }) {
  const project = useProject()!;
  const [lineIdx, setLineIdx] = useState(0);
  const [pct, setPct] = useState(40);
  const [custom, setCustom] = useState<number | null>(null);
  const [bg, setBg] = useState<'auto' | 'black'>('auto');
  const [url, setUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [timeDraft, setTimeDraft] = useState<string | null>(null);

  const line = lines[Math.min(lineIdx, Math.max(0, lines.length - 1))];
  const t = custom ?? (line ? Math.round(line.start + (line.end - line.start) * pct / 100) : 0);
  // the burn-in audio setting lives in the style but does not change the picture
  const lookKey = JSON.stringify({ ...style, output: undefined });
  const w = project.video?.width ?? 1920;
  const h = project.video?.height ?? 1080;

  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        const res = await fetch(ppath('/karaoke/preview'), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ style, t_ms: t, background: bg }),
        });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? `HTTP ${res.status}`);
        const blob = await res.blob();
        if (cancelled) return;
        setUrl((old) => { if (old) URL.revokeObjectURL(old); return URL.createObjectURL(blob); });
        setError(null);
      } catch (e: any) {
        if (!cancelled) setError(e.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 350);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [lookKey, t, bg]); // eslint-disable-line react-hooks/exhaustive-deps

  const go = (i: number) => { setCustom(null); setLineIdx(Math.max(0, Math.min(lines.length - 1, i))); };
  const followPlayhead = () => {
    const ms = Math.round(player.positionMs());
    setCustom(ms);
    const i = lines.findIndex((l) => ms < l.end);
    if (i >= 0) setLineIdx(i);
  };
  const listen = () => {
    if (!line) return;
    revealOnWaveform(line.start, line.end);
    player.playRange(line.start, line.end, { loop: true, padMs: 400 });
  };

  return (
    <Card>
      <CardHeader icon={<Subtitles className="size-4" />} title="预览"
        description={`${w}×${h} · ${project.video ? '原视频画面' : '纯黑背景'} · 与烧录使用同一渲染器（libass）`}
        actions={project.video ? (
          <Segmented size="sm" value={bg} onChange={setBg} options={[{ value: 'auto', label: '视频画面' }, { value: 'black', label: '纯黑' }]} />
        ) : undefined} />
      <CardBody className="space-y-4">
        <div className="relative overflow-hidden rounded-xl bg-black ring-1 ring-line" style={{ aspectRatio: `${w} / ${h}` }}>
          {url && <img src={url} alt={`${fmtMs(t)} 的字幕预览`} className="absolute inset-0 size-full object-contain" />}
          {loading && (
            <div className="absolute top-3 right-3 flex items-center gap-1.5 rounded-full bg-black/60 px-2.5 py-1 text-xs text-white/80">
              <Loader2 className="size-3.5 animate-spin" />渲染中
            </div>
          )}
          {error && <div className="absolute inset-x-3 bottom-3 rounded-lg bg-danger/90 px-3 py-2 text-xs text-white">{error}</div>}
          <div className="absolute bottom-3 left-3 rounded-md bg-black/60 px-2 py-0.5 font-mono text-xs text-white/85">{fmtMs(t)}</div>
        </div>

        {lines.length > 0 && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" variant="ghost" icon={<ChevronLeft className="size-4" />} disabled={lineIdx <= 0 && custom === null} onClick={() => go(lineIdx - 1)}>上一行</Button>
              <Select className="min-w-0 flex-1" value={String(lineIdx)} onChange={(e) => go(Number(e.target.value))} aria-label="预览歌词行">
                {lines.map((l, i) => <option key={l.id} value={i}>{l.index}. {l.text}（{fmtMs(l.start, false)}）</option>)}
              </Select>
              <Button size="sm" variant="ghost" onClick={() => go(lineIdx + 1)} disabled={lineIdx >= lines.length - 1}>下一行<ChevronRight className="size-4" /></Button>
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
              <div className="min-w-64 flex-1">
                <SliderField name="行内进度" label={<span className="text-muted">行内进度</span>} value={pct}
                  onChange={(v) => { setCustom(null); setPct(v); }} />
              </div>
              {timeDraft === null ? (
                <Tip content="输入具体时间（如 1:02.345）">
                  <button className="focus-ring rounded-md px-1.5 py-1 font-mono text-sm hover:bg-surface-2" onClick={() => setTimeDraft(fmtMs(t))}>{fmtMs(t)}</button>
                </Tip>
              ) : (
                <Input autoFocus className="h-8 w-32 font-mono" value={timeDraft} aria-label="预览时间"
                  onChange={(e) => setTimeDraft(e.target.value)}
                  onBlur={(e) => { const v = parseTime(e.currentTarget.value); setTimeDraft(null); if (v !== null) setCustom(v); }}
                  onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); if (e.key === 'Escape') setTimeDraft(null); }} />
              )}
              <Tip content="使用播放器当前位置"><Button size="sm" variant="ghost" icon={<Crosshair className="size-4" />} onClick={followPlayhead}>播放头</Button></Tip>
              <Button size="sm" variant="ghost" onClick={listen}>试听本行</Button>
            </div>
          </div>
        )}
        {style.ruby.enabled && style.ruby.script === 'romaji' && (
          <p className="text-xs text-muted">罗马音按固定的平文式规则生成（与对齐使用的拼写一致）。</p>
        )}
      </CardBody>
    </Card>
  );
}

// ------------------------------------------------------------------ export / burn

function BurnCard({ style, patch }: { style: KaraokeStyle; patch: (fn: (s: KaraokeStyle) => void) => void }) {
  const project = useProject()!;
  const view = useApp((s) => s.pv?.view);
  const job = useJob('burn');
  const [background, setBackground] = useState<'auto' | 'black'>('auto');
  const [audio, setAudio] = useState<'original' | 'mix' | 'none'>('original');
  const [quality, setQuality] = useState<'standard' | 'high'>('standard');
  const [out, setOut] = useState<{ url: string; filename: string; warnings: string[] } | null>(null);
  const canMix = !!view?.audio.vocals?.available && !!view?.audio.instrumental?.available;
  const running = job && (job.status === 'queued' || job.status === 'running');
  const vocalPct = style.output?.vocal_keep_pct ?? 20;
  const setVocalPct = (v: number) => patch((s) => { s.output = { ...s.output, vocal_keep_pct: v }; });

  const start = () => run(async () => {
    setOut(null);
    const j = await api.post<Job>(ppath('/karaoke/burn'), { background, audio, quality, vocal_keep_pct: vocalPct });
    trackJob(j, { label: '字幕烧录', onDone: (d) => { if (d.status === 'succeeded' && d.output) setOut(d.output); } });
  }, '无法开始烧录');

  return (
    <Card>
      <CardHeader icon={<Film className="size-4" />} title="导出字幕 / 烧录视频"
        description="ASS 可用于任何支持 ASS 的播放器或剪辑软件；烧录会把字幕画进画面（H.264 MP4）。" />
      <CardBody className="space-y-5">
        <div className="flex flex-wrap items-center gap-3">
          <a href={ppath('/export/karaoke-ass?download=1')} download>
            <Button variant="outline" icon={<Download className="size-4" />}>下载 ASS 字幕</Button>
          </a>
          <span className="text-xs text-muted">{project.video ? '时间已与原视频对齐（含音轨起点偏移）' : '时间从音频起点开始'}</span>
        </div>

        <div className="grid gap-4 rounded-xl border border-line p-4 md:grid-cols-3">
          <div className="space-y-1.5">
            <div className="text-[13px] font-medium">背景</div>
            <Segmented size="sm" value={project.video ? background : 'black'} onChange={setBackground}
              options={[{ value: 'auto', label: '原视频', disabled: !project.video }, { value: 'black', label: '纯黑' }]} />
            {!project.video && <div className="text-xs text-subtle">上传视频作为原曲即可使用原视频画面</div>}
          </div>
          <div className="space-y-1.5">
            <div className="text-[13px] font-medium">音频</div>
            <Segmented size="sm" value={audio} onChange={setAudio} options={[
              { value: 'original', label: '原声' },
              { value: 'mix', label: '降低人声', disabled: !canMix, title: canMix ? undefined : '需要先分离人声' },
              { value: 'none', label: '无' },
            ]} />
          </div>
          <div className="space-y-1.5">
            <div className="text-[13px] font-medium">画质</div>
            <Segmented size="sm" value={quality} onChange={setQuality} options={[{ value: 'standard', label: '标准（较快）' }, { value: 'high', label: '高' }]} />
          </div>
          {audio === 'mix' && canMix && (
            <div className="space-y-1.5 md:col-span-3">
              <div className="text-[13px] font-medium">人声保留</div>
              <SliderField name="人声保留" value={vocalPct} onChange={setVocalPct} min={0} max={100} step={1} unit="%"
                trackClassName="min-w-40" />
              <div className="text-xs text-subtle">0% 为纯伴奏；伴奏保持 100%。只用于这里的烧录，不影响“导出”页的混音。</div>
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center justify-end gap-3">
          {running && (
            <div className="flex min-w-60 flex-1 items-center gap-3">
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-3">
                <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${Math.max(2, (job!.progress ?? 0) * 100)}%` }} />
              </div>
              <span className="text-xs text-muted">{job!.message}</span>
            </div>
          )}
          <Button variant="primary" onClick={start} loading={!!running} icon={<Flame className="size-4" />}>一键烧录</Button>
        </div>
        {job?.status === 'failed' && <Callout tone="danger" title="烧录失败">{job.error ?? job.message}</Callout>}
        {out && (
          <Callout tone="ok" title={out.filename}
            actions={<a href={out.url} download><Button size="sm" variant="primary" icon={<Download className="size-4" />}>下载视频</Button></a>}>
            {out.warnings.length ? out.warnings.join('；') : '烧录完成。'}
          </Callout>
        )}
        <p className="text-xs text-subtle">烧录耗时约为歌曲时长的 0.3–1 倍，可以离开本页，任务在后台继续（右上角可查看进度或取消）。</p>
        {!project.video && <Badge tone="neutral">无视频：输出 1920×1080 纯黑背景视频</Badge>}
      </CardBody>
    </Card>
  );
}

