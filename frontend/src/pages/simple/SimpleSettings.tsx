// 设置页：极简模式的一键流程用到的全部选项。每项修改立即保存到本机。

import { Bot, Film, Scissors, Subtitles } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { cn } from '@/lib/format';
import type { AppSettings, KaraokePreset } from '@/lib/types';
import { run, useApp } from '@/store/app';
import { saveSettings, useSimple } from '@/store/simple';
import { AiSettingsForm } from '@/components/AiSettingsForm';
import { Callout, Card, CardBody, CardHeader, Field, Segmented, Select, SliderField, Switch } from '@/components/ui';

type Simple = AppSettings['simple'];

export function SimpleSettings() {
  const settings = useSimple((s) => s.settings);
  const info = useApp((s) => s.info);
  const [presets, setPresets] = useState<KaraokePreset[]>([]);
  useEffect(() => { void run(async () => setPresets(await api.get<KaraokePreset[]>('/api/karaoke/presets'))); }, []);
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
              <Field label="运行设备">
                <Segmented<'auto' | 'cpu'> value={s.separation_device} onChange={(v) => save({ separation_device: v })}
                  options={[{ value: 'auto', label: '自动（GPU / MPS）' }, { value: 'cpu', label: '仅 CPU' }]} />
              </Field>
            </div>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader icon={<Subtitles className="size-4" />} title="卡拉OK字幕" description="字幕样式。之后在详细模式的“卡拉OK字幕”里还能逐项调整。" />
        <CardBody className="space-y-5">
          <div role="radiogroup" aria-label="字幕样式" className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {presets.map((p) => {
              const on = s.karaoke_preset === p.name;
              return (
                <button key={p.name} type="button" role="radio" aria-checked={on} onClick={() => save({ karaoke_preset: p.name })}
                  className={cn('focus-ring rounded-xl border p-2.5 text-left transition hover:border-line-strong',
                    on ? 'border-accent ring-1 ring-accent' : 'border-line')}>
                  <div className="flex h-10 items-center justify-center rounded-lg bg-[#0b0d14] text-[17px] font-bold"
                    style={{ WebkitTextStroke: `1px ${p.style.text.outline_color}` }}>
                    <span style={{ color: p.style.text.color_sung }}>歌</span>
                    <span style={{ color: p.style.text.color_unsung }}>詞</span>
                  </div>
                  <div className="mt-1.5 text-[13px] font-medium">{p.label}</div>
                </button>
              );
            })}
          </div>
          <div className="flex flex-wrap items-center gap-4">
            <Switch checked={s.ruby} onChange={(v) => save({ ruby: v })} label="在汉字上方标注读音" />
            {s.ruby && (
              <Segmented<Simple['ruby_script']> size="sm" value={s.ruby_script} onChange={(v) => save({ ruby_script: v })}
                options={[{ value: 'hiragana', label: '平假名' }, { value: 'katakana', label: '片假名' }, { value: 'romaji', label: '罗马音' }]} />
            )}
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader icon={<Film className="size-4" />} title="输出视频" description="任务完成后自动把字幕烧录进视频（没有视频时生成纯黑背景的视频）。" />
        <CardBody className="space-y-4">
          <Switch checked={s.auto_export} onChange={(v) => save({ auto_export: v })} label="完成后自动生成视频" />
          {s.auto_export && (
            <>
              <Field label="视频里的声音">
                <Segmented<Simple['video_audio']> value={s.video_audio} onChange={(v) => save({ video_audio: v })} options={[
                  { value: 'original', label: '原声' },
                  { value: 'mix', label: '降低人声（伴唱）', disabled: !s.separate, title: s.separate ? undefined : '需要开启人声分离' },
                  { value: 'none', label: '无声' },
                ]} />
              </Field>
              {s.video_audio === 'mix' && s.separate && (
                <Field label="人声保留" hint="0% 为纯伴奏；伴奏保持 100%">
                  <VocalLevel value={s.vocal_keep_pct} onCommit={(v) => save({ vocal_keep_pct: v })} />
                </Field>
              )}
              <Field label="画质">
                <Segmented<Simple['quality']> value={s.quality} onChange={(v) => save({ quality: v })}
                  options={[{ value: 'standard', label: '标准（较快）' }, { value: 'high', label: '高' }]} />
              </Field>
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
