// Copy a diagnostic report (the system, a failed task's / job's details, the end of the log) for a bug report.

import { ClipboardCopy } from 'lucide-react';
import { useState } from 'react';
import { api } from '@/lib/api';
import { run, toast } from '@/store/app';
import { Button } from '@/components/ui';

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement('textarea');  // (older browsers, or no permission)
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
  }
}

export function DiagnosticsButton({ taskId, jobId, label = '复制诊断信息', size = 'xs' }: {
  taskId?: string; jobId?: string; label?: string; size?: 'xs' | 'sm';
}) {
  const [busy, setBusy] = useState(false);
  const copy = () => run(async () => {
    setBusy(true);
    try {
      const q = new URLSearchParams();
      if (taskId) q.set('task', taskId);
      if (jobId) q.set('job', jobId);
      const r = await api.get<{ text: string }>(`/api/diagnostics${q.size ? `?${q}` : ''}`);
      await copyText(r.text);
      toast('ok', '已复制诊断信息', '可以粘贴到 GitHub Issues 或发给作者（不含 API Key，用户名已隐去）', 5000);
    } finally {
      setBusy(false);
    }
  }, '读取诊断信息失败');
  return (
    <Button size={size} variant="ghost" loading={busy} icon={<ClipboardCopy className="size-3.5" />} onClick={() => void copy()}>
      {label}
    </Button>
  );
}
