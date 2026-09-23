import * as Popover from '@radix-ui/react-popover';
import { Activity, Download, Redo2, Undo2, X } from 'lucide-react';
import { cn } from '@/lib/format';
import { cancelJob, ppath, run, STEPS, useApp } from '@/store/app';
import { redo, undo } from '@/store/edits';
import { Badge, Button, IconButton, Kbd, Progress, Tip } from '@/components/ui';

export function Topbar() {
  const pv = useApp((s) => s.pv);
  const step = useApp((s) => s.step);
  const nUndo = useApp((s) => s.undo.length);
  const nRedo = useApp((s) => s.redo.length);
  const stepLabel = STEPS.find((s) => s.id === step)?.label;
  const staleCount = pv?.view.results.filter((r) => r.stale).length ?? 0;

  return (
    <header className="sticky top-0 z-10 flex h-14 shrink-0 items-center gap-3 border-b border-line bg-canvas/80 px-6 backdrop-blur-md">
      <div className="flex min-w-0 items-center gap-2 text-sm">
        {pv ? (
          <>
            <span className="truncate font-medium">{pv.project.name}</span>
            <span className="text-subtle">/</span>
            <span className="text-muted">{stepLabel}</span>
            <Badge tone={pv.project.mode === 'lrc' ? 'accent' : 'neutral'} className="ml-1">{pv.project.mode === 'lrc' ? 'LRC 增强' : '普通模式'}</Badge>
            {staleCount > 0 && <Badge tone="warn" dot title="输入已修改，这些结果仍可查看但不再对应当前设置">{staleCount} 个结果已过期</Badge>}
          </>
        ) : (
          <span className="font-medium">项目</span>
        )}
      </div>
      <div className="ml-auto flex items-center gap-1.5">
        {pv && (
          <>
            <IconButton label={`撤销人工修改（${nUndo}） ⌘Z`} disabled={!nUndo} onClick={() => void undo()}><Undo2 className="size-4" /></IconButton>
            <IconButton label={`重做（${nRedo}） ⌘⇧Z`} disabled={!nRedo} onClick={() => void redo()}><Redo2 className="size-4" /></IconButton>
            <span className="mx-1 h-5 w-px bg-line" />
          </>
        )}
        <JobsIndicator />
        {pv && (
          <Tip content="下载便携项目包（含音频），可在另一台电脑导入">
            <a href={ppath('/package?include_audio=1')} download>
              <Button size="sm" variant="ghost" icon={<Download className="size-4" />}>项目包</Button>
            </a>
          </Tip>
        )}
      </div>
    </header>
  );
}

function JobsIndicator() {
  const jobs = Object.values(useApp((s) => s.jobs));
  const active = jobs.filter((j) => j.status === 'queued' || j.status === 'running');
  if (!jobs.length) {
    return (
      <Tip content={<span>快捷键：<Kbd>Space</Kbd> 播放 <Kbd>L</Kbd> 循环 <Kbd>M</Kbd> 标记</span>}>
        <span className="hidden items-center gap-1.5 px-2 text-xs text-subtle lg:flex"><Activity className="size-3.5" />空闲</span>
      </Tip>
    );
  }
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button className={cn('focus-ring flex h-8 items-center gap-2 rounded-lg px-2.5 text-xs font-medium transition',
          active.length ? 'bg-accent-soft text-accent' : 'text-muted hover:bg-surface-2')}>
          {active.length ? <span className="relative flex size-2"><span className="absolute inline-flex size-full animate-ping rounded-full bg-accent opacity-60" /><span className="relative inline-flex size-2 rounded-full bg-accent" /></span> : <Activity className="size-3.5" />}
          {active.length ? `${active.length} 个任务运行中` : '任务'}
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content align="end" sideOffset={8} className="z-50 w-80 animate-in rounded-xl border border-line bg-surface p-2 shadow-[var(--shadow-pop)]">
          <div className="px-2 pt-1 pb-2 text-xs font-semibold text-muted">任务</div>
          <div className="space-y-1">
            {jobs.map((j) => {
              const live = j.status === 'queued' || j.status === 'running';
              return (
                <div key={j.id} className="rounded-lg px-2 py-2 hover:bg-surface-2">
                  <div className="flex items-center gap-2 text-[13px]">
                    <span className="font-medium">{j.label ?? j.kind}</span>
                    <Badge tone={{ queued: 'neutral', running: 'accent', succeeded: 'ok', failed: 'danger', cancelled: 'neutral' }[j.status] as any}>
                      {{ queued: '排队中', running: '运行中', succeeded: '完成', failed: '失败', cancelled: '已取消' }[j.status]}
                    </Badge>
                    {live && (
                      <button className="ml-auto text-muted hover:text-danger" title="取消" onClick={() => run(() => cancelJob(j.id))}>
                        <X className="size-3.5" />
                      </button>
                    )}
                  </div>
                  {live && <Progress value={j.progress} className="mt-2" />}
                  <div className={cn('mt-1 truncate text-xs', j.status === 'failed' ? 'text-danger' : 'text-muted')} title={j.error ?? j.message}>
                    {j.error ?? j.message}
                  </div>
                </div>
              );
            })}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
