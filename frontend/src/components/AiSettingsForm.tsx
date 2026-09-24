// AI provider settings (shared by the settings page and the AI tab):
// Claude Code / Codex CLI, or an OpenAI-compatible API.  Saved immediately.

import { Bot, CheckCircle2, KeyRound, PlugZap, RefreshCw, TerminalSquare, XCircle } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { api } from '@/lib/api';
import { cn } from '@/lib/format';
import type { AiProviderId, AppSettings } from '@/lib/types';
import { run, toast } from '@/store/app';
import { loadProviders, saveSettings, useSimple } from '@/store/simple';
import { Badge, Button, Field, Input } from '@/components/ui';

const CHOICES: { id: AiProviderId; label: string; hint: string; icon: ReactNode }[] = [
  { id: 'none', label: '不使用', hint: '只用规则读音；也可以手动网页聊天往返', icon: <XCircle className="size-4" /> },
  { id: 'claude', label: 'Claude Code', hint: '本机 claude 命令（需已登录）', icon: <TerminalSquare className="size-4" /> },
  { id: 'codex', label: 'Codex', hint: '本机 codex 命令（需已登录）', icon: <TerminalSquare className="size-4" /> },
  { id: 'openai', label: 'OpenAI 兼容 API', hint: '任意 /chat/completions 接口', icon: <PlugZap className="size-4" /> },
];

export function AiSettingsForm({ compact }: { compact?: boolean }) {
  const settings = useSimple((s) => s.settings);
  const providers = useSimple((s) => s.providers);
  const [test, setTest] = useState<{ ok: boolean; text: string } | null>(null);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    if (!providers) void run(() => loadProviders(), '检测 AI 工具失败');
  }, [providers]);

  if (!settings) return null;
  const ai = settings.ai;
  const save = (patch: Partial<AppSettings['ai']> & { api_key?: string; clear_api_key?: boolean }) =>
    run(async () => { await saveSettings({ ai: patch }); setTest(null); }, '保存设置失败');
  const avail = (id: AiProviderId) => (id === 'none' || id === 'openai' ? null : providers?.find((p) => p.id === id));

  const runTest = () => run(async () => {
    setTesting(true);
    try {
      const r = await api.post<{ ok: boolean; reply?: string; error?: string; model?: string; elapsed_s?: number; cost_usd?: number | null }>('/api/ai/test', {});
      setTest(r.ok
        ? { ok: true, text: `回复「${r.reply}」 · ${r.elapsed_s} 秒${r.model ? ` · ${r.model}` : ''}${r.cost_usd != null ? ` · $${r.cost_usd.toFixed(4)}` : ''}` }
        : { ok: false, text: r.error ?? '失败' });
    } finally {
      setTesting(false);
    }
  }, '测试失败');

  return (
    <div className="space-y-4">
      <div role="radiogroup" aria-label="AI 提供方" className={cn('grid gap-2', compact ? 'sm:grid-cols-4' : 'sm:grid-cols-2')}>
        {CHOICES.map((c) => {
          const on = ai.provider === c.id;
          const info = avail(c.id);
          return (
            <button key={c.id} type="button" role="radio" aria-checked={on} onClick={() => save({ provider: c.id })}
              className={cn('focus-ring flex items-start gap-2.5 rounded-xl border px-3 py-2.5 text-left transition',
                on ? 'border-accent bg-accent-soft/60 ring-1 ring-accent' : 'border-line hover:border-line-strong hover:bg-surface-2')}>
              <span className={cn('mt-0.5', on ? 'text-accent' : 'text-muted')}>{c.icon}</span>
              <span className="min-w-0">
                <span className="flex flex-wrap items-center gap-1.5 text-[13px] font-semibold">
                  {c.label}
                  {info && <Badge tone={info.available ? 'ok' : 'warn'}>{info.available ? '已安装' : '未找到'}</Badge>}
                </span>
                {!compact && <span className="mt-0.5 block text-xs text-muted">{info?.available && info.version ? info.version : c.hint}</span>}
              </span>
            </button>
          );
        })}
      </div>

      {ai.provider !== 'none' && (
        <div className="space-y-3">
          {ai.provider === 'openai' ? (
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="接口地址（Base URL）">
                <Input defaultValue={ai.base_url} key={`u-${ai.base_url}`} placeholder="https://api.openai.com/v1"
                  onBlur={(e) => e.target.value !== ai.base_url && save({ base_url: e.target.value.trim() })} />
              </Field>
              <Field label="模型">
                <Input defaultValue={ai.model} key={`m-${ai.model}`} placeholder="例如 gpt-4.1-mini"
                  onBlur={(e) => e.target.value !== ai.model && save({ model: e.target.value.trim() })} />
              </Field>
              <Field label={<span className="flex items-center gap-1.5"><KeyRound className="size-3.5" />API Key</span>}
                hint={ai.has_api_key ? '已保存在本机（不会写进项目或导出包）' : ai.env_key_present ? `未保存；将使用环境变量 ${ai.api_key_env}` : `可留空并设置环境变量 ${ai.api_key_env}`}>
                <div className="flex gap-2">
                  <Input type="password" autoComplete="off" placeholder={ai.has_api_key ? '••••••••（已保存）' : 'sk-…'}
                    onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
                    onBlur={(e) => { const v = e.target.value.trim(); if (v) { void save({ api_key: v }); e.target.value = ''; toast('ok', '已保存 API Key'); } }} />
                  {ai.has_api_key && <Button variant="ghost" size="sm" onClick={() => save({ clear_api_key: true })}>清除</Button>}
                </div>
              </Field>
              <Field label="环境变量名" hint="没有保存 Key 时从这个环境变量读取">
                <Input defaultValue={ai.api_key_env} key={`e-${ai.api_key_env}`}
                  onBlur={(e) => e.target.value !== ai.api_key_env && save({ api_key_env: e.target.value.trim() || 'OPENAI_API_KEY' })} />
              </Field>
            </div>
          ) : (
            <Field label="模型（可选）" hint={ai.provider === 'claude' ? '留空使用 Claude Code 的默认模型；例如 sonnet、haiku 更便宜' : '留空使用 Codex 的默认模型'}>
              <Input defaultValue={ai.model} key={`m-${ai.provider}-${ai.model}`} placeholder="留空使用默认"
                onBlur={(e) => e.target.value !== ai.model && save({ model: e.target.value.trim() })} />
            </Field>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <Button size="sm" variant="secondary" icon={<Bot className="size-4" />} loading={testing} onClick={runTest}>测试连接</Button>
            {ai.provider !== 'openai' && (
              <Button size="sm" variant="ghost" icon={<RefreshCw className="size-3.5" />} onClick={() => run(() => loadProviders(true))}>重新检测</Button>
            )}
            {test && (
              <span className={cn('flex items-center gap-1.5 text-xs', test.ok ? 'text-ok' : 'text-danger')}>
                {test.ok ? <CheckCircle2 className="size-4" /> : <XCircle className="size-4" />}{test.text}
              </span>
            )}
          </div>
          <p className="text-xs text-subtle">使用 AI 时，歌词会发送给所选的服务（和网页聊天一样）。命令行工具在空的临时目录中运行，不能使用任何工具，也看不到你的文件。</p>
        </div>
      )}
    </div>
  );
}
