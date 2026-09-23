// All diagnostics of the result grouped by severity. Diagnostics only — never
// a claimed probability of correctness.

import { useState } from 'react';
import { cn } from '@/lib/format';
import type { Issue } from '@/lib/types';
import { Badge, EmptyState, Segmented } from '@/components/ui';
import { SEVERITY_LABEL, SEVERITY_TONE } from './helpers';
import { CheckCircle2 } from 'lucide-react';

type Sev = 'all' | Issue['severity'];

export function IssuesPanel({ issues, lineIndex, onJump }: {
  issues: Issue[];
  lineIndex: Map<string, number>;
  onJump: (issue: Issue) => void;
}) {
  const [sev, setSev] = useState<Sev>('all');
  const count = (s: Issue['severity']) => issues.filter((i) => i.severity === s).length;
  const shown = issues
    .filter((i) => sev === 'all' || i.severity === sev)
    .sort((a, b) => (lineIndex.get(a.line_id ?? '') ?? -1) - (lineIndex.get(b.line_id ?? '') ?? -1));

  if (!issues.length) {
    return <EmptyState icon={<CheckCircle2 className="size-5 text-ok" />} title="没有异常提示" description="检查覆盖了覆盖率、区间长度、句首偏差、窗口边缘、跨句冲突与上下文稳定性" />;
  }

  return (
    <div className="space-y-3">
      <Segmented<Sev>
        size="sm"
        value={sev}
        onChange={setSev}
        options={[
          { value: 'all', label: `全部 ${issues.length}` },
          { value: 'error', label: `错误 ${count('error')}`, disabled: !count('error') },
          { value: 'warning', label: `警告 ${count('warning')}`, disabled: !count('warning') },
          { value: 'info', label: `提示 ${count('info')}`, disabled: !count('info') },
        ]}
      />
      <ul className="max-h-80 divide-y divide-line overflow-y-auto rounded-xl border border-line">
        {shown.map((i, k) => (
          <li key={k}>
            <button
              onClick={() => onJump(i)}
              className={cn('focus-ring flex w-full items-start gap-3 px-3 py-2.5 text-left transition hover:bg-surface-2')}
            >
              <Badge tone={SEVERITY_TONE[i.severity]} className="mt-0.5">{SEVERITY_LABEL[i.severity]}</Badge>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <code className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-muted">{i.code}</code>
                  {i.line_id && <span className="text-xs text-muted">第 {(lineIndex.get(i.line_id) ?? -1) + 1} 行</span>}
                </div>
                <div className="mt-1 text-[13px] leading-5">{i.message}</div>
              </div>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
