// 设置页：极简模式的一键流程用到的全部选项。每项修改立即保存到本机。

import { Bot, Film, Scissors, Subtitles } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import type { AppSettings, FontFamily, KaraokeStyle } from '@/lib/types';
import { loadProjects, run, toast, useApp } from '@/store/app';
import { saveSettings, useSimple } from '@/store/simple';
import { AiSettingsForm } from '@/components/AiSettingsForm';
import { StylePanel } from '@/components/karaoke/StylePanel';
import { Callout, Card, CardBody, CardHeader, Field, Segmented, Select, SliderField, Switch } from '@/components/ui';

type Simple = AppSettings['simple'];

export function SimpleSettings() {
  const settings = useSimple((s) => s.settings);
  const info = useApp((s) => s.info);
  if (!settings) return null;
  const s = settings.simple;
  const save = (patch: Partial<Simple>) => run(() => saveSettings({ simple: patch }), '保存设置失败');

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">设置</h1>
        <p className="mt-1 text-sm text-muted">“开始制作”会按这里的选项自动完成每一步。修改立即保存，只对之后开始的任务生效。</p>
      </div>

      <Card>
        <CardHeader icon={<Bot className="size-4" />} title="AI 注音" description="用 AI 检查每个字的读音（例如「今君」读 いま きみ，「真新」读 まっさら）。读音越准，对齐越准。" />
        <CardBody className="space-y-4">
          <AiSettingsForm />
          {settings.ai.provider !== 'none' && (
            <Switch checked={s.ai_readings} onChange={(v) => save({ ai_readings: v })} label="制作时自动用 AI 注音" />
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader icon={<Scissors className="size-4" />} title="人声分离" description="先把人声和伴奏分开：对齐更准，也能生成降低人声的伴唱视频。最耗时的一步（GPU / Apple 芯片约 1–3 分钟）。" />
        <CardBody className="space-y-4">
          {info && !info.separation_available && (
            <Callout tone="warn" title="未安装分离组件">会跳过这一步，使用原曲对齐。安装后重启服务：<code className="font-mono text-xs">uv pip install -e ".[separation]"</code></Callout>
          )}
          <Switch checked={s.separate} onChange={(v) => save({ separate: v })} label="分离人声" />
          {s.separate && (
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="模型">
                <Select value={s.separation_preset} onChange={(e) => save({ separation_preset: e.target.value })}>
                  {(info?.separation_presets ?? []).map((p) => <option key={p.name} value={p.name}>{p.name}（{p.architecture}）</option>)}
                </Select>
              </Field>
              <Group label="运行设备">
                <Segmented<'auto' | 'cpu'> value={s.separation_device} onChange={(v) => save({ separation_device: v })}
                  options={[{ value: 'auto', label: '自动（GPU / MPS）' }, { value: 'cpu', label: '仅 CPU' }]} />
              </Group>
            </div>
          )}
        </CardBody>
      </Card>

      <DefaultStyleCard style={s.karaoke} />

      <Card>
        <CardHeader icon={<Film className="size-4" />} title="输出视频" description="任务完成后自动把字幕烧录进视频（没有视频时生成纯黑背景的视频）。" />
        <CardBody className="space-y-4">
          <Switch checked={s.auto_export} onChange={(v) => save({ auto_export: v })} label="完成后自动生成视频" />
          {s.auto_export && (
            <>
              <Group label="视频里的声音">
                <Segmented<Simple['video_audio']> value={s.video_audio} onChange={(v) => save({ video_audio: v })} options={[
                  { value: 'original', label: '原声' },
                  { value: 'mix', label: '降低人声（伴唱）', disabled: !s.separate, title: s.separate ? undefined : '需要开启人声分离' },
                  { value: 'none', label: '无声' },
                ]} />
              </Group>
              {s.video_audio === 'mix' && s.separate && (
                <Field label="人声保留" hint="0% 为纯伴奏；伴奏保持 100%">
                  <VocalLevel value={s.vocal_keep_pct} onCommit={(v) => save({ vocal_keep_pct: v })} />
                </Field>
              )}
              <Group label="画质">
                <Segmented<Simple['quality']> value={s.quality} onChange={(v) => save({ quality: v })}
                  options={[{ value: 'standard', label: '标准（较快）' }, { value: 'high', label: '高' }]} />
              </Group>
            </>
          )}
        </CardBody>
      </Card>
    </div>
  );
}

function VocalLevel({ value, onCommit }: { value: number; onCommit: (v: number) => void }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  // follows the thumb while dragging; saved on release or when a typed value is confirmed
  return <SliderField name="人声保留" value={v} onChange={setV} onCommit={onCommit} min={0} max={100} step={1} unit="%" />;
}

/** The complete default subtitle style of new tasks, with the few knobs people change most. */
/** The complete subtitle style of new tasks (the same panel as the detailed mode). */
function DefaultStyleCard({ style }: { style: KaraokeStyle }) {
  const projects = useApp((s) => s.projects);
  const [draft, setDraft] = useState(style);
  const [fonts, setFonts] = useState<{ default: string; families: FontFamily[] }>({ default: '', families: [] });
  const pending = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => { void run(async () => setFonts(await api.get('/api/fonts'))); }, []);
  useEffect(() => { if (!projects.length) void run(() => loadProjects()); }, [projects.length]);
  // follow the saved settings unless an edit is still on its way
  useEffect(() => { if (!pending.current) setDraft(style); }, [style]);

  const change = (next: KaraokeStyle) => {
    setDraft(next);
    if (pending.current) clearTimeout(pending.current);
    pending.current = setTimeout(() => {
      pending.current = null;
      void run(() => saveSettings({ simple: { karaoke: next } }), '保存设置失败');
    }, 500);
  };
  const copyFrom = (pid: string) => run(async () => {
    const k = await api.get<KaraokeStyle>(`/api/projects/${pid}/karaoke`);
    change({ ...k, output: draft.output });
    toast('ok', '已使用该项目的字幕样式');
  }, '读取项目样式失败');

  return (
    <Card>
      <CardHeader icon={<Subtitles className="size-4" />} title="卡拉OK字幕样式"
        description="第 4 步选“设置里的样式”时完整使用这套样式；选“模版配色”时使用它的布局、字号、时间等，配色和荧光由模版决定。可以保存成预设，随时切换。" />
      <CardBody className="space-y-3 pt-3">
        <StylePanel style={draft} onChange={change} fonts={fonts.families} defaultFont={fonts.default}
          defaultOpen={['colors']} storageKey="simple" />
        <div className="flex flex-wrap items-center gap-3 border-t border-line pt-3 text-[13px]">
          <span className="text-muted">使用某个项目调好的样式</span>
          <Select aria-label="从项目复制样式" value="" className="max-w-72" onChange={(e) => e.target.value && copyFrom(e.target.value)}>
            <option value="">选择项目…</option>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </Select>
        </div>
      </CardBody>
    </Card>
  );
}

function Group({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div role="group" aria-label={label} className="flex flex-col items-start gap-1.5">
      <span className="text-[13px] font-medium text-fg">{label}</span>
      {children}
      {hint && <span className="text-xs text-muted">{hint}</span>}
    </div>
  );
}
