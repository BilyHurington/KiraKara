// AI reading round trip through any web chat: copy prompt → paste reply →
// validate → preview diff → apply selected lines. No LLM API, no key.

import { ArrowRight, Bot, ClipboardCopy, ClipboardPaste, FileCheck2, History, RotateCcw } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { cn, copyText, fmtRelative, readFileText } from '@/lib/format';
import type { PatchLine, ProjectView } from '@/lib/types';
import { ppath, run, setPV, toast, useProject } from '@/store/app';
import {
  Badge, Button, Callout, Card, CardBody, CardHeader, DropZone, Segmented, Textarea,
} from '@/components/ui';

interface Report {
  ok: boolean;
  snapshot: string | null;
  roundtrip_id: string | null;
  errors: string[];
  warnings: string[];
  lines: (PatchLine & { diff: (PatchLine['diff'][number] & { changed?: boolean; locked?: boolean })[] })[];
  missing_line_ids: string[];
}

const STATUS: Record<string, { label: string; tone: 'ok' | 'warn' | 'danger' | 'neutral' | 'info' }> = {
  ok: { label: '可应用', tone: 'ok' },
  unchanged: { label: '无变化', tone: 'neutral' },
  stale_text: { label: '原文已变', tone: 'warn' },
  stale_reading: { label: '读音已变', tone: 'warn' },
  unknown_line: { label: '未知行', tone: 'danger' },
  locked_skipped: { label: '已锁定跳过', tone: 'info' },
  invalid: { label: '无效', tone: 'danger' },
  duplicate: { label: '重复', tone: 'danger' },
};

const RT_STATUS: Record<string, string> = { prompted: '已生成提示词', validated: '已校验', applied: '已应用', rejected: '已拒绝' };

export function AiRoundtripCard() {
  const project = useProject()!;
  const sung = useMemo(() => project.lyrics.lines.filter((l) => l.sing && l.kind === 'lyric'), [project.lyrics.lines]);
  const uncertainIds = useMemo(
    () => sung.filter((l) => l.segments.some((s) => s.uncertain && !s.confirmed)).map((l) => l.id),
    [sung],
  );

  // step 1
  const [scope, setScope] = useState<'all' | 'uncertain'>('all');
  const [prompt, setPrompt] = useState<{ prompt: string; snapshot_id: string; copied: boolean } | null>(null);
  const [busyPrompt, setBusyPrompt] = useState(false);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  // step 2
  const [reply, setReply] = useState('');
  const [busyValidate, setBusyValidate] = useState(false);
  // step 3
  const [report, setReport] = useState<{ id: string; report: Report } | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busyApply, setBusyApply] = useState(false);

  // text routed here from the lyrics page
  useEffect(() => {
    try {
      const t = sessionStorage.getItem('kara.aiPaste');
      if (t) {
        setReply(t);
        sessionStorage.removeItem('kara.aiPaste');
        toast('info', '已填入 AI 注音补丁', '请点击“校验”');
      }
    } catch { /* ignore */ }
  }, []);

  const makePrompt = () => run(async () => {
    setBusyPrompt(true);
    try {
      const body = scope === 'uncertain' ? { line_ids: uncertainIds } : {};
      const out = await api.post<{ prompt: string; snapshot_id: string }>(ppath('/ai/prompt'), body);
      const copied = await copyText(out.prompt);
      setPrompt({ ...out, copied });
      toast(copied ? 'ok' : 'warn', copied ? '提示词已复制' : '无法自动复制', copied ? '粘贴到任意网页聊天中' : '请在下方文本框中手动全选复制');
    } finally {
      setBusyPrompt(false);
    }
  }, '生成提示词失败');

  const validate = () => run(async () => {
    setBusyValidate(true);
    try {
      const out = await api.post<{ report_id: string; report: Report }>(ppath('/ai/validate'), { text: reply });
      setReport({ id: out.report_id, report: out.report });
      setSelected(new Set(out.report.lines.filter((l) => l.status === 'ok').map((l) => l.line_id)));
    } finally {
      setBusyValidate(false);
    }
  }, '校验失败');

  const apply = () => run(async () => {
    if (!report) return;
    setBusyApply(true);
    try {
      const pv = await api.post<ProjectView & { summary?: { applied?: string[]; unchanged?: string[]; skipped?: string[] } }>(
        ppath('/ai/apply'), { report_id: report.id, line_ids: [...selected] },
      );
      setPV(pv);
      const s = pv.summary ?? {};
      toast('ok', 'AI 注音已应用', `应用 ${s.applied?.length ?? 0} 行 · 无变化 ${s.unchanged?.length ?? 0} · 跳过 ${s.skipped?.length ?? 0}`);
      setReport(null);
      setReply('');
    } finally {
      setBusyApply(false);
    }
  }, '应用失败');

  const lineText = (id: string) => project.lyrics.lines.find((l) => l.id === id)?.text ?? id;
  const lineIndex = (id: string) => project.lyrics.lines.findIndex((l) => l.id === id) + 1;

  return (
    <Card>
      <CardHeader
        icon={<Bot className="size-4" />}
        title="AI 注音（网页聊天往返）"
        description="程序生成包含行 ID、原文、已有读音和返回格式的提示词；你把它粘贴到任意网页聊天，再把得到的 JSON 贴回来。回传只作为注音补丁，校验并预览后才会应用。"
      />
      <CardBody className="space-y-6">
        <Callout tone="info">不接入任何 LLM API，也不需要密钥；程序不会自动操作第三方网页。AI 不能修改时间、偏移或锁定的读音。</Callout>

        {/* step 1 */}
        <Step n={1} title="生成并复制提示词" done={!!prompt}>
          <div className="flex flex-wrap items-center gap-3">
            <Segmented<'all' | 'uncertain'>
              size="sm"
              value={scope}
              onChange={setScope}
              options={[
                { value: 'all', label: `全部 ${sung.length} 行` },
                { value: 'uncertain', label: `仅待确认 ${uncertainIds.length} 行`, disabled: uncertainIds.length === 0 },
              ]}
            />
            <Button variant="primary" size="sm" icon={<ClipboardCopy className="size-4" />} loading={busyPrompt}
              disabled={sung.length === 0} onClick={makePrompt}>
              复制 AI 提示词
            </Button>
            {prompt && (
              <span className="text-xs text-muted">
                快照 <code className="rounded bg-surface-2 px-1 font-mono">{prompt.snapshot_id}</code>
                {prompt.copied ? ' · 已复制到剪贴板' : ''}
              </span>
            )}
          </div>
          {prompt && !prompt.copied && (
            <div className="mt-3 space-y-2">
              <Callout tone="warn">浏览器不允许自动复制，请手动全选下方内容并复制。</Callout>
              <Textarea ref={promptRef} readOnly value={prompt.prompt} className="h-48" onFocus={(e) => e.currentTarget.select()} />
              <Button size="xs" onClick={() => { promptRef.current?.focus(); promptRef.current?.select(); }}>全选</Button>
            </div>
          )}
          {prompt?.copied && (
            <details className="mt-3 text-xs text-muted">
              <summary className="cursor-pointer select-none hover:text-fg">查看提示词内容</summary>
              <Textarea readOnly value={prompt.prompt} className="mt-2 h-40" />
            </details>
          )}
        </Step>

        {/* step 2 */}
        <Step n={2} title="粘贴 AI 返回的结果" done={!!report}>
          <div className="grid gap-3 lg:grid-cols-[1fr_260px]">
            <Textarea
              value={reply}
              onChange={(e) => setReply(e.target.value)}
              placeholder={'把网页聊天的回复整段粘贴到这里（可以包含说明文字或 ```json 代码块）'}
              className="h-40"
            />
            <div className="flex flex-col gap-3">
              <DropZone accept=".json,.txt,application/json,text/plain" compact
                title="或上传 JSON / 文本文件"
                onFile={(f) => run(async () => setReply(await readFileText(f)), '读取文件失败')} />
              <Button variant="primary" icon={<FileCheck2 className="size-4" />} loading={busyValidate}
                disabled={!reply.trim()} onClick={validate}>
                校验
              </Button>
              {reply && <Button variant="ghost" size="sm" icon={<RotateCcw className="size-4" />} onClick={() => { setReply(''); setReport(null); }}>清空</Button>}
            </div>
          </div>
        </Step>

        {/* step 3 */}
        <Step n={3} title="预览并应用" last>
          {!report ? (
            <p className="text-[13px] text-subtle">校验后在这里逐行预览读音变化，选择要应用的行。</p>
          ) : (
            <ReportView
              report={report.report}
              selected={selected}
              setSelected={setSelected}
              lineText={lineText}
              lineIndex={lineIndex}
              busy={busyApply}
              onApply={apply}
            />
          )}
        </Step>

        {project.ai_roundtrips.length > 0 && (
          <div>
            <div className="mb-2 flex items-center gap-2 text-[13px] font-medium"><History className="size-4 text-muted" />往返记录</div>
            <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line text-[13px]">
              {[...project.ai_roundtrips].reverse().slice(0, 8).map((rt) => (
                <li key={rt.id} className="flex items-center gap-3 px-4 py-2">
                  <span className="text-muted">{fmtRelative(rt.created)}</span>
                  <code className="font-mono text-xs text-subtle">{rt.snapshot_id}</code>
                  <span className="text-muted">{rt.line_ids.length} 行</span>
                  <Badge className="ml-auto" tone={rt.status === 'applied' ? 'ok' : rt.status === 'rejected' ? 'danger' : 'neutral'}>
                    {RT_STATUS[rt.status] ?? rt.status}
                  </Badge>
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

function Step({ n, title, done, last, children }: { n: number; title: string; done?: boolean; last?: boolean; children: React.ReactNode }) {
  return (
    <section className="relative flex gap-4">
      {!last && <span className="absolute top-8 bottom-[-18px] left-[13px] w-px bg-line" aria-hidden />}
      <span className={cn('relative z-[1] grid size-7 shrink-0 place-items-center rounded-full text-xs font-semibold',
        done ? 'bg-ok text-white' : 'bg-accent-soft text-accent')}>{n}</span>
      <div className="min-w-0 flex-1 pt-0.5">
        <h4 className="mb-3 text-sm font-semibold">{title}</h4>
        {children}
      </div>
    </section>
  );
}

function ReportView({ report, selected, setSelected, lineText, lineIndex, busy, onApply }: {
  report: Report; selected: Set<string>; setSelected: (s: Set<string>) => void;
  lineText: (id: string) => string; lineIndex: (id: string) => number; busy: boolean; onApply: () => void;
}) {
  const applicable = report.lines.filter((l) => l.status === 'ok');
  const toggle = (id: string) => {
    const s = new Set(selected);
    if (s.has(id)) s.delete(id); else s.add(id);
    setSelected(s);
  };
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={report.ok ? 'ok' : 'danger'} dot>{report.ok ? '格式有效' : '存在错误'}</Badge>
        {report.snapshot && <Badge tone="neutral">快照 {report.snapshot}</Badge>}
        <Badge tone="accent">{applicable.length} / {report.lines.length} 行可应用</Badge>
      </div>
      {report.errors.map((e) => <Callout key={e} tone="danger">{e}</Callout>)}
      {report.warnings.map((w) => <Callout key={w} tone="warn">{w}</Callout>)}
      {report.missing_line_ids?.length > 0 && (
        <Callout tone="warn" title={`AI 结果缺少 ${report.missing_line_ids.length} 行`}>
          可能用“同上”省略了重复副歌；这些行保持原读音。
        </Callout>
      )}

      {report.lines.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-line">
          <div className="flex items-center gap-3 border-b border-line bg-surface-2 px-4 py-2 text-xs text-muted">
            <input type="checkbox" className="size-4 accent-[var(--c-accent)]"
              checked={applicable.length > 0 && applicable.every((l) => selected.has(l.line_id))}
              onChange={(e) => setSelected(new Set(e.target.checked ? applicable.map((l) => l.line_id) : []))} />
            全选可应用的行
          </div>
          <ul className="max-h-[480px] divide-y divide-line overflow-y-auto">
            {report.lines.map((l) => {
              const st = STATUS[l.status] ?? { label: l.status, tone: 'neutral' as const };
              const can = l.status === 'ok';
              return (
                <li key={l.line_id} className={cn('flex gap-3 px-4 py-3', !can && 'bg-surface-2/40')}>
                  <input type="checkbox" className="mt-1 size-4 accent-[var(--c-accent)]" disabled={!can}
                    checked={selected.has(l.line_id)} onChange={() => toggle(l.line_id)} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="tabular text-xs text-subtle">#{lineIndex(l.line_id) || '?'}</span>
                      <span className="text-[13px] font-medium">{lineText(l.line_id)}</span>
                      <Badge tone={st.tone}>{st.label}</Badge>
                    </div>
                    {l.reasons.length > 0 && <div className="mt-1 text-xs text-muted">{l.reasons.join('；')}</div>}
                    {l.diff.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {l.diff.map((d, i) => <DiffChip key={i} d={d} />)}
                      </div>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      <div className="flex items-center justify-end gap-3">
        <span className="text-xs text-muted">已选 {selected.size} 行；原文或读音已变化的行会被跳过</span>
        <Button variant="primary" icon={<ClipboardPaste className="size-4" />} loading={busy} disabled={selected.size === 0} onClick={onApply}>
          应用所选 {selected.size} 行
        </Button>
      </div>
    </div>
  );
}

function DiffChip({ d }: { d: Report['lines'][number]['diff'][number] }) {
  const changed = d.changed ?? (d.old_units.join('/') !== d.new_units.join('/'));
  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-lg border px-2 py-1 text-xs',
      changed ? 'border-accent/40 bg-accent-soft' : 'border-line bg-surface-2 text-muted')}>
      <span className="font-medium text-fg">{d.surface}</span>
      {changed ? (
        <>
          <span className="text-subtle line-through">{d.old_units.join('/') || '—'}</span>
          <ArrowRight className="size-3 text-accent" />
          <span className="font-medium text-accent">{d.new_units.join('/') || '—'}</span>
        </>
      ) : (
        <span>{d.new_units.join('/') || d.old_units.join('/')}</span>
      )}
      {d.locked && <Badge tone="info">锁定</Badge>}
    </span>
  );
}
