// Parse preview: detected format, warnings, blocking errors, routing of
// non-lyrics JSON, and the line table. Nothing changes until "应用".

import { ArrowRight, Check, FileJson, X } from 'lucide-react';
import { api } from '@/lib/api';
import { fmtMs } from '@/lib/format';
import type { ProjectView } from '@/lib/types';
import { loadProjects, openProject, ppath, run, setPV, setStep, toast, useProject } from '@/store/app';
import { Badge, Button, Callout, Stat, Table, Td, Th } from '@/components/ui';
import type { PendingPreview } from './LyricsInputCard';

const FORMAT_LABEL: Record<string, string> = {
  lrc: 'LRC',
  plain: '纯文本',
  'json-prepared': 'prepared.json',
  'json-project': '项目 JSON',
  'json-alignment': '对齐结果 JSON',
  'json-reading-patch': 'AI 注音补丁',
  unknown: '无法识别',
};

export const KIND_LABEL: Record<string, string> = {
  lyric: '歌词',
  translation: '翻译',
  romanization: '音译',
  meta: '作者信息',
  blank: '空行',
};

export function PreviewPanel({ pending, busy, onApply, onDiscard, onReparse, onEditTimes }: {
  pending: PendingPreview; busy: boolean; onApply: () => void; onDiscard: () => void; onReparse: () => void; onEditTimes: () => void;
}) {
  const project = useProject()!;
  const { preview } = pending;
  const doc = preview.doc;
  const lines = doc?.lines ?? [];
  const sung = lines.filter((l) => l.sing && l.kind === 'lyric');
  const timed = lines.filter((l) => l.imported_start_ms !== null);
  const modeError = !!preview.error && !preview.route && project.mode === 'lrc';

  const switchToPlain = () => run(async () => {
    setPV(await api.patch<ProjectView>(ppath(''), { mode: 'plain' }));
    toast('ok', '已切换到普通模式', '重新解析中…');
    onReparse();
  });

  const route = () => run(async () => {
    const text = pending.text ?? '';
    if (preview.route === 'json-project') {
      const fd = new FormData();
      fd.append('file', new File([text], pending.filename ?? 'project.json', { type: 'application/json' }));
      const pv = await api.post<ProjectView>('/api/projects/import', fd);
      await loadProjects();
      await openProject(pv.project.id);
      toast('ok', '已导入项目', pv.project.name);
    } else if (preview.route === 'json-alignment') {
      const pv = await api.post<ProjectView>(ppath('/results/import'), { text });
      setPV(pv);
      toast('ok', '已导入对齐结果', '作为非当前结果保存，可在“对齐”中查看');
      onDiscard();
    } else if (preview.route === 'json-reading-patch') {
      try { sessionStorage.setItem('kara.aiPaste', text); } catch { /* ignore */ }
      setStep('enhance');
      toast('info', '已转到 AI 注音', '回传内容已填入“粘贴 AI 结果”');
    }
  }, '处理失败');

  const routeLabel = {
    'json-project': '作为项目导入',
    'json-alignment': '导入为对齐结果',
    'json-reading-patch': '转到 AI 注音并校验',
  }[preview.route ?? 'json-project'];

  return (
    <div className="animate-slide-up rounded-2xl border border-accent/30 bg-accent-soft/20 p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="text-[13px] font-semibold">解析预览</span>
        <Badge tone="accent">{FORMAT_LABEL[preview.detected] ?? preview.detected}</Badge>
        {pending.filename && <Badge>{pending.filename}</Badge>}
        {pending.origin === 'link' && <Badge>音乐链接</Badge>}
        <span className="ml-auto flex gap-2">
          <Button size="sm" variant="ghost" onClick={onDiscard} icon={<X className="size-4" />}>放弃</Button>
          <Button size="sm" variant="primary" disabled={!preview.preview_id} loading={busy} onClick={onApply} icon={<Check className="size-4" />}>
            应用到项目
          </Button>
        </span>
      </div>

      <div className="space-y-2">
        {preview.route && (
          <Callout tone="info" title="这不是歌词文本" actions={<Button size="sm" variant="primary" onClick={route} icon={<FileJson className="size-4" />}>{routeLabel}</Button>}>
            {preview.error}
          </Callout>
        )}
        {preview.error && !preview.route && (
          <Callout
            tone="danger"
            title="无法应用"
            actions={modeError ? (
              <>
                <Button size="sm" onClick={onEditTimes}>补充时间</Button>
                <Button size="sm" variant="primary" onClick={switchToPlain} icon={<ArrowRight className="size-4" />}>切换到普通模式</Button>
              </>
            ) : undefined}
          >
            {preview.error}
            {modeError && <div className="mt-1 text-xs">不会静默降级：请补充带时间的 LRC，或主动切换到普通模式。</div>}
          </Callout>
        )}
        {preview.warnings.map((w) => <Callout key={w} tone="warn">{w}</Callout>)}
        {Object.keys(preview.extra_tracks ?? {}).length > 0 && (
          <Callout tone="info">
            该歌曲还提供 {Object.keys(preview.extra_tracks).map((k) => KIND_LABEL[k] ?? k).join('、')} 轨；应用后可在下方配对（默认不参与对齐）。
          </Callout>
        )}
      </div>

      {doc && (
        <>
          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="总行数" value={lines.length} />
            <Stat label="参与对齐" value={sung.length} tone="accent" />
            <Stat label="带时间" value={timed.length} hint={doc.embedded_offset_raw ? `[offset:${doc.embedded_offset_raw}]` : undefined} />
            <Stat label="语言" value={doc.language} hint={doc.meta.title ?? undefined} />
          </div>
          {doc.embedded_offset_raw && (
            <p className="mt-2 text-xs text-muted">
              内嵌 [offset:{doc.embedded_offset_raw}] → 规范化平移 {doc.embedded_shift_ms > 0 ? '+' : ''}{doc.embedded_shift_ms} ms（只应用一次）。{doc.embedded_offset_note}
            </p>
          )}
          <Table className="mt-3 max-h-80">
            <thead>
              <tr><Th className="w-12">#</Th><Th className="w-28">时间</Th><Th className="w-24">类型</Th><Th className="w-20">对齐</Th><Th>文本</Th></tr>
            </thead>
            <tbody>
              {lines.map((l, i) => (
                <tr key={l.id} className={l.sing && l.kind === 'lyric' ? '' : 'text-muted'}>
                  <Td className="tabular text-subtle">{i + 1}</Td>
                  <Td className="tabular font-mono text-xs">{l.imported_start_ms !== null ? fmtMs(l.imported_start_ms) : '—'}</Td>
                  <Td><Badge tone={l.kind === 'lyric' ? 'neutral' : 'warn'}>{KIND_LABEL[l.kind] ?? l.kind}</Badge></Td>
                  <Td>{l.sing && l.kind === 'lyric' ? <Check className="size-4 text-ok" /> : <span className="text-xs text-subtle">否</span>}</Td>
                  <Td className="max-w-0 truncate" title={l.text}>{l.text || <span className="text-subtle">（空）</span>}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </>
      )}
    </div>
  );
}
