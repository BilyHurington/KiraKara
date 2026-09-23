// Step 7: exports. alignment.json is the complete standard output; other
// formats may lose information and show explicit loss warnings.

import { Archive, Check, Copy, Download, Eye, FileJson, FileSpreadsheet, FileText, Film, Music2, Package, Sparkles } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { api } from '@/lib/api';
import { cn, copyText, fmtMs, fmtRelative, ROLE_LABEL } from '@/lib/format';
import type { ExportInline, Job } from '@/lib/types';
import { player } from '@/audio/player';
import { ppath, run, toast, trackJob, useApp, useJob, useProject, useView } from '@/store/app';
import {
  Badge, Button, Callout, Card, CardBody, CardHeader, Dialog, EmptyState, Field, PageHeader, Segmented, Select, SliderField,
  Spinner, Tip,
} from '@/components/ui';

const FORMAT_META: Record<string, { icon: typeof FileJson; group: 'result' | 'lyrics' | 'project'; note?: string }> = {
  alignment: { icon: FileJson, group: 'result', note: '完整标准输出：单元起止、未解决项、原始预测与人工覆盖、模型与输入快照' },
  csv: { icon: FileSpreadsheet, group: 'result' },
  'lrc-line': { icon: FileText, group: 'result', note: '模型对齐后聚合的行级 LRC' },
  'lrc-unit': { icon: FileText, group: 'result', note: '模型对齐后的逐单元（增强）LRC' },
  prepared: { icon: FileJson, group: 'lyrics', note: '可编辑的歌词与读音，可重新导入' },
  'lrc-calibrated': { icon: FileText, group: 'lyrics', note: '仅校准原锚点（整体平移）的 LRC，不含模型结果；已清除 [offset]' },
  project: { icon: Package, group: 'project' },
};
const NEEDS_RESULT = new Set(['alignment', 'csv', 'lrc-line', 'lrc-unit']);

export function ExportPage() {
  const project = useProject()!;
  const view = useView()!;
  const currentId = useApp((s) => s.resultId);
  const [rid, setRid] = useState<string | null>(currentId ?? project.active_result_id);
  useEffect(() => {
    if (!rid || !view.results.some((r) => r.id === rid)) setRid(currentId ?? project.active_result_id);
  }, [rid, currentId, project.active_result_id, view.results]);
  const sum = view.results.find((r) => r.id === rid) ?? null;

  return (
    <>
      <PageHeader
        eyebrow="第 7 步"
        title="导出"
        description="对外时间统一为原音频起点起算的整数毫秒，导出不再叠加任何偏移。不能表达读音映射、终点、间隙或失败信息的格式会提示损失。"
      />
      <div className="space-y-6">
        <Card>
          <CardHeader
            title="导出的结果"
            description="基于该结果生成结果类格式（JSON / CSV / 对齐 LRC）"
            actions={view.results.length > 0 && (
              <Select className="w-72" value={rid ?? ''} onChange={(e) => setRid(e.target.value)}>
                {[...view.results].reverse().map((r) => (
                  <option key={r.id} value={r.id}>
                    {fmtRelative(r.created)} · {r.mode === 'lrc' ? 'LRC' : '普通'} · {ROLE_LABEL[r.audio_role] ?? r.audio_role}
                    {r.id === project.active_result_id ? ' · 当前' : ''}{r.stale ? ' · 已过期' : ''}{r.coverage.full ? '' : ' · 局部'}
                  </option>
                ))}
              </Select>
            )}
          />
          <CardBody className="space-y-3">
            {!sum && <Callout tone="info">还没有对齐结果；歌词类格式（prepared.json、校准后 LRC）和项目文件仍可导出。</Callout>}
            {sum?.stale && <Callout tone="warn" title="该结果已过期">{sum.stale_reason}。导出内容对应生成时的输入。</Callout>}
            {sum && !sum.coverage.full && (
              <Callout tone="info" title="局部结果">
                只覆盖 {sum.coverage.line_ids.length} 行{sum.coverage.from_ms !== null && `（${fmtMs(sum.coverage.from_ms)} – ${fmtMs(sum.coverage.to_ms)}）`}，导出文件会标明覆盖范围。
              </Callout>
            )}
            {sum && (
              <div className="tabular flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted">
                <span>单元 <b className="text-fg">{sum.n_units}</b></span>
                <span>无时间 <b className={sum.n_failed ? 'text-warn' : 'text-fg'}>{sum.n_failed}</b></span>
                <span>提示 <b className="text-fg">{sum.n_issues}</b></span>
                <span>人工修改 <b className="text-fg">{sum.n_manual}</b></span>
              </div>
            )}
          </CardBody>
        </Card>

        <FormatSection title="对齐结果" group="result" rid={rid} hasResult={!!sum} />
        <FormatSection title="歌词与校准" group="lyrics" rid={rid} hasResult={!!sum} />

        <div className="grid gap-6 lg:grid-cols-2">
          <MixCard />
          <div className="space-y-6">
            <StemsCard />
            <PackageCard />
          </div>
        </div>
      </div>
    </>
  );
}

// ------------------------------------------------------------------ text formats

function FormatSection({ title, group, rid, hasResult }: { title: string; group: 'result' | 'lyrics'; rid: string | null; hasResult: boolean }) {
  const info = useApp((s) => s.info);
  const formats = Object.entries(info?.export_formats ?? {}).filter(([fmt]) => (FORMAT_META[fmt]?.group ?? 'project') === group);
  const [preview, setPreview] = useState<{ fmt: string; data: ExportInline | null } | null>(null);
  if (!formats.length) return null;

  const url = (fmt: string, download: boolean) =>
    ppath(`/export/${fmt}?${download ? 'download=1&' : ''}${rid && NEEDS_RESULT.has(fmt) ? `result_id=${rid}` : ''}`);

  const open = (fmt: string) => {
    setPreview({ fmt, data: null });
    void run(async () => {
      const data = await api.get<ExportInline>(url(fmt, false));
      setPreview({ fmt, data });
      return data;
    }, '生成预览失败').then((r) => { if (r === undefined) setPreview(null); });
  };

  return (
    <section>
      <h2 className="mb-3 text-sm font-semibold text-muted">{title}</h2>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {formats.map(([fmt, f]) => {
          const meta = FORMAT_META[fmt];
          const Icon = meta?.icon ?? FileText;
          const disabled = NEEDS_RESULT.has(fmt) && !hasResult;
          const primary = fmt === 'alignment';
          return (
            <Card key={fmt} className={cn('flex flex-col p-4 transition', primary && 'ring-1 ring-accent/40', disabled && 'opacity-55')}>
              <div className="flex items-start gap-3">
                <div className={cn('grid size-10 shrink-0 place-items-center rounded-xl', primary ? 'bg-accent text-accent-fg' : 'bg-surface-2 text-muted')}>
                  <Icon className="size-5" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="truncate font-mono text-[13px] font-semibold">{f.filename}</span>
                    {primary && <Badge tone="accent"><Sparkles className="size-3" />完整</Badge>}
                  </div>
                  <p className="mt-1 text-xs leading-5 text-muted">{meta?.note ?? f.description}</p>
                </div>
              </div>
              <div className="mt-4 flex gap-2">
                {disabled ? (
                  <Button size="sm" disabled className="flex-1" icon={<Download className="size-4" />}>需要对齐结果</Button>
                ) : (
                  <a href={url(fmt, true)} download className="flex-1">
                    <Button size="sm" variant={primary ? 'primary' : 'secondary'} className="w-full" icon={<Download className="size-4" />}>下载</Button>
                  </a>
                )}
                <Button size="sm" variant="outline" disabled={disabled} onClick={() => open(fmt)} icon={<Eye className="size-4" />}>预览</Button>
              </div>
            </Card>
          );
        })}
      </div>
      <PreviewDialog preview={preview} onClose={() => setPreview(null)} downloadUrl={preview ? url(preview.fmt, true) : ''} />
    </section>
  );
}

function PreviewDialog({ preview, onClose, downloadUrl }: {
  preview: { fmt: string; data: ExportInline | null } | null; onClose: () => void; downloadUrl: string;
}) {
  const [copied, setCopied] = useState(false);
  const data = preview?.data;
  const copy = async () => {
    if (!data) return;
    if (await copyText(data.content)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } else {
      toast('warn', '无法写入剪贴板', '请在预览框中手动选择并复制');
    }
  };
  const long = (data?.content.length ?? 0) > 400_000;
  return (
    <Dialog
      open={!!preview}
      onOpenChange={(v) => !v && onClose()}
      wide
      title={data?.filename ?? '生成中…'}
      description={data ? `${data.media_type} · ${(data.content.length / 1024).toFixed(1)} KB` : undefined}
      footer={<>
        <Button variant="ghost" onClick={onClose}>关闭</Button>
        <Button onClick={copy} disabled={!data} icon={copied ? <Check className="size-4" /> : <Copy className="size-4" />}>{copied ? '已复制' : '复制'}</Button>
        <a href={downloadUrl} download><Button variant="primary" disabled={!data} icon={<Download className="size-4" />}>下载</Button></a>
      </>}
    >
      {!data ? (
        <div className="flex items-center gap-2 py-10 text-sm text-muted"><Spinner />正在生成…</div>
      ) : (
        <div className="space-y-3">
          {data.warnings.map((w) => <Callout key={w} tone="warn">{w}</Callout>)}
          <pre className="max-h-[50vh] overflow-auto rounded-xl border border-line bg-surface-2 p-4 font-mono text-xs leading-5 whitespace-pre">
            {long ? `${data.content.slice(0, 400_000)}\n…（预览已截断，复制与下载为完整内容）` : data.content}
          </pre>
        </div>
      )}
    </Dialog>
  );
}

// ------------------------------------------------------------------ audio

function MixCard() {
  const project = useProject()!;
  const view = useView()!;
  const job = useJob('mix');
  const canMix = !!view.audio.vocals?.available && !!view.audio.instrumental?.available;
  const [p, setP] = useState(project.mix.vocal_keep_pct);
  const [q, setQ] = useState(project.mix.instrumental_pct);
  const [master, setMaster] = useState(project.mix.master);
  const [limiter, setLimiter] = useState(project.mix.limiter);
  const [gain, setGain] = useState<{ bus_gain: number; peak_before: number } | null>(null);
  const [out, setOut] = useState<{ url: string; filename: string; report: Record<string, any> } | null>(null);
  const videoJob = useJob('video');
  const [videoOut, setVideoOut] = useState<{ url: string; filename: string } | null>(null);
  const hasVideo = !!project.video;

  // the player's “自定义混音” source previews exactly these settings
  useEffect(() => { player.setMix({ p, q, master }); }, [p, q, master]);

  useEffect(() => {
    if (!canMix) return;
    const t = setTimeout(() => {
      api.post<{ bus_gain: number; peak_before: number }>(ppath('/mix/preview-gain'), { vocal_keep_pct: p, instrumental_pct: q, master, limiter })
        .then((g) => { setGain(g); player.setMix({ bus: g.bus_gain }); }).catch(() => setGain(null));
    }, 300);
    return () => clearTimeout(t);
  }, [canMix, p, q, master, limiter]);

  const videoRunning = videoJob && (videoJob.status === 'queued' || videoJob.status === 'running');
  const exportVideo = () => run(async () => {
    setVideoOut(null);
    const j = await api.post<Job>(ppath('/video/export'), { vocal_keep_pct: p, instrumental_pct: q, master, limiter });
    trackJob(j, {
      label: '视频导出',
      onDone: (done) => { if (done.status === 'succeeded' && done.output) setVideoOut(done.output); },
    });
  }, '无法导出视频');

  const running = job && (job.status === 'queued' || job.status === 'running');
  const exportMix = () => run(async () => {
    setOut(null);
    const j = await api.post<Job>(ppath('/mix/export'), { vocal_keep_pct: p, instrumental_pct: q, master, limiter });
    trackJob(j, {
      label: '混音导出',
      onDone: (done) => { if (done.status === 'succeeded' && done.output) setOut(done.output); },
    });
  }, '无法导出混音');

  return (
    <Card>
      <CardHeader icon={<Music2 className="size-4" />} title={hasVideo ? '人声保留混音（WAV / 视频）' : '人声保留混音 WAV'}
        description="正常速度、原始时长与原点；与播放器“自定义混音”试听使用同一规则，监听音量不写入导出。" />
      <CardBody className="space-y-5">
        {!canMix ? (
          <EmptyState
            title="需要人声与伴奏两条分轨"
            description="只有原曲时无法单独降低完整混音中的人声。可在“注音与分离”中分离，或在“音频与歌词”中导入已有分轨。"
          />
        ) : (
          <>
            <div className="space-y-3">
              <SliderField name="人声保留" label={<span className="inline-block w-16">人声保留</span>} value={p} onChange={setP} />
              <SliderField name="伴奏" label={<span className="inline-block w-16">伴奏</span>} value={q} onChange={setQ} />
              <SliderField name="总增益" label={<span className="inline-block w-16">总增益</span>} value={master} onChange={setMaster} min={0} max={2} step={0.01} unit="×" />
            </div>
            <div className="rounded-xl bg-surface-2/70 px-4 py-3 font-mono text-xs text-muted">
              mix = master × (p/100·V + q/100·I) = {master.toFixed(2)} × ({(p / 100).toFixed(2)}·V + {(q / 100).toFixed(2)}·I)
              <div className="mt-1 font-sans">“人声保留 {Math.round(p)}%” 表示人声线性幅度 ×{(p / 100).toFixed(2)}，不是主观响度；0% 时伴奏中仍可能残留人声。</div>
            </div>
            <Field label="防削波">
              <div className="flex flex-wrap items-center gap-3">
                <Segmented value={limiter} onChange={setLimiter}
                  options={[{ value: 'normalize_peak', label: '共同母线峰值归一' }, { value: 'none', label: '不处理' }]} />
                {gain && (
                  <Tip content="对整段混音统一施加，保持两轨相对比例">
                    <span className="tabular text-xs text-muted">母线增益 ×{gain.bus_gain.toFixed(3)} · 峰值 {gain.peak_before.toFixed(3)}</span>
                  </Tip>
                )}
              </div>
            </Field>
            <div className="flex flex-wrap justify-end gap-2">
              {hasVideo && (
                <Tip content="画面原样复制（不重新编码），声音换成当前比例的混音，并保持与画面同步">
                  <Button onClick={exportVideo} loading={!!videoRunning} icon={<Film className="size-4" />}>导出降低人声的视频</Button>
                </Tip>
              )}
              <Button variant="primary" onClick={exportMix} loading={!!running} icon={<Download className="size-4" />}>导出混音 WAV</Button>
            </div>
            {job?.status === 'failed' && <Callout tone="danger" title="导出失败">{job.error ?? job.message}</Callout>}
            {videoJob?.status === 'failed' && <Callout tone="danger" title="视频导出失败">{videoJob.error ?? videoJob.message}</Callout>}
            {videoOut && (
              <Callout tone="ok" title={videoOut.filename}
                actions={<a href={videoOut.url} download><Button size="sm" variant="primary" icon={<Download className="size-4" />}>下载视频</Button></a>}>
                人声保留 {Math.round(p)}% · 伴奏 {Math.round(q)}%；画面未重新编码。
              </Callout>
            )}
            {out && (
              <Callout tone="ok" title={out.filename}
                actions={<a href={out.url} download><Button size="sm" variant="primary" icon={<Download className="size-4" />}>下载 WAV</Button></a>}>
                <span className="tabular">
                  母线增益 ×{Number(out.report.bus_gain).toFixed(3)} · 峰值 {Number(out.report.peak_before).toFixed(3)} → {Number(out.report.peak_after).toFixed(3)}
                  {' · '}削波采样 {out.report.clipped_samples}
                </span>
              </Callout>
            )}
          </>
        )}
      </CardBody>
    </Card>
  );
}

function StemsCard() {
  const project = useProject()!;
  const view = useView()!;
  const assets = project.audio.filter((a) => a.role !== 'mix');
  return (
    <Card>
      <CardHeader icon={<Archive className="size-4" />} title="音轨" description="解码后的 PCM WAV（与对齐使用同一解码器，时间原点一致）" />
      <div className="p-2">
        {assets.length === 0 ? (
          <p className="px-3 py-3 text-[13px] text-muted">还没有音频。</p>
        ) : assets.map((a) => {
          const ok = !!view.audio[a.role as 'original']?.available;
          return (
            <Row key={a.id}
              title={ROLE_LABEL[a.role] ?? a.role}
              sub={`${a.source.filename ?? a.sha256.slice(0, 12)} · ${fmtMs(a.duration_ms, false)} · ${a.sample_rate} Hz${a.source.kind === 'separation' ? ` · 分离：${a.source.model ?? ''}` : ''}`}
              action={ok
                ? <a href={ppath(`/audio/${a.id}/playback.wav`)} download><Button size="xs" variant="outline" icon={<Download className="size-3.5" />}>WAV</Button></a>
                : <Badge tone="warn">文件缺失</Badge>}
            />
          );
        })}
      </div>
    </Card>
  );
}

function PackageCard() {
  return (
    <Card>
      <CardHeader icon={<Package className="size-4" />} title="项目" description="歌词、校准、AI 往返、人工修改与结果都在项目文件中；不含模型权重或本机路径。" />
      <div className="p-2">
        <Row title="便携项目包（含音频）" sub=".kara.zip，可在另一台电脑直接导入"
          action={<a href={ppath('/package?include_audio=1')} download><Button size="xs" variant="primary" icon={<Download className="size-3.5" />}>下载</Button></a>} />
        <Row title="项目包（不含音频）" sub="体积小；导入后按内容指纹重新上传音频"
          action={<a href={ppath('/package?include_audio=0')} download><Button size="xs" variant="outline" icon={<Download className="size-3.5" />}>下载</Button></a>} />
      </div>
    </Card>
  );
}

function Row({ title, sub, action }: { title: ReactNode; sub: ReactNode; action: ReactNode }) {
  return (
    <div className="flex items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-surface-2">
      <div className="min-w-0 flex-1">
        <div className="text-[13px] font-medium">{title}</div>
        <div className="truncate text-xs text-muted">{sub}</div>
      </div>
      {action}
    </div>
  );
}
