// 设置页：极简模式的一键流程用到的全部选项。每项修改立即保存到本机。

import { Bot, Film, RotateCcw, Scissors, Subtitles } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { cn } from '@/lib/format';
import type { AppSettings, KaraokePreset, KaraokeStyle } from '@/lib/types';
import { loadProjects, run, toast, useApp } from '@/store/app';
import { saveSettings, useSimple } from '@/store/simple';
import { AiSettingsForm } from '@/components/AiSettingsForm';
import { Button, Callout, Card, CardBody, CardHeader, Field, Segmented, Select, SliderField, Switch } from '@/components/ui';

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
              <Group label="运行设备">
                <Segmented<'auto' | 'cpu'> value={s.separation_device} onChange={(v) => save({ separation_device: v })}
                  options={[{ value: 'auto', label: '自动（GPU / MPS）' }, { value: 'cpu', label: '仅 CPU' }]} />
              </Group>
            </div>
          )}
        </CardBody>
      </Card>

      <DefaultStyleCard style={s.karaoke} presets={presets} />

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
function DefaultStyleCard({ style, presets }: { style: KaraokeStyle; presets: KaraokePreset[] }) {
  const projects = useApp((s) => s.projects);
  useEffect(() => { if (!projects.length) void run(() => loadProjects()); }, [projects.length]);
  const edit = (fn: (k: KaraokeStyle) => void) => run(async () => {
    const next = structuredClone(style);
    fn(next);
    await saveSettings({ simple: { karaoke: next } });
  }, '保存设置失败');
  const copyFrom = (pid: string) => run(async () => {
    const k = await api.get<KaraokeStyle>(`/api/projects/${pid}/karaoke`);
    await saveSettings({ simple: { karaoke: k } });
    toast('ok', '已使用该项目的字幕样式');
  }, '读取项目样式失败');
  const t = style.text;
  const r = style.ruby;
  const L = style.layout;
  const M = style.timing;
  const script = { hiragana: '平假名', katakana: '片假名', romaji: '罗马音' }[r.script];
  const summary = [
    `${L.position === 'bottom' ? '靠底' : '靠顶'} · ${L.lines} 行${L.lines > 1 ? (L.arrangement === 'alternate' ? '左右交替' : '居中') : ''}`,
    r.enabled ? `注音：${script}（${r.target === 'all' ? '全部' : '仅汉字'}）` : '不注音',
    `提前 ${M.lead_in_ms / 1000} 秒出现 · 唱完停留 ${M.hold_ms / 1000} 秒${M.advance_ms ? ` · 扫光提前 ${M.advance_ms} ms` : ''}`,
    L.show_translation ? `翻译：${{ opposite: L.position === 'bottom' ? '画面顶部' : '画面底部', block: '歌词旁', line: '每行下方' }[L.translation_position]}` : '不显示翻译',
  ];
  const sample = r.script === 'romaji' ? ['hatsu', 'koi', 'no', 'shirushi'] : r.script === 'katakana' ? ['ハツ', 'コイ', 'ノ', 'シルシ'] : ['はつ', 'こい', 'の', 'しるし'];
  const words = ['初', '恋', 'の', '印'];
  return (
    <Card>
      <CardHeader icon={<Subtitles className="size-4" />} title="卡拉OK字幕"
        description="新任务使用的完整字幕样式（布局、配色、注音、出现与停留时间）。"
        actions={<Button size="xs" variant="ghost" icon={<RotateCcw className="size-3.5" />}
          onClick={() => run(() => saveSettings({ simple: { reset_karaoke: true } }))}>恢复默认</Button>} />
      <CardBody className="space-y-5">
        <div className="flex flex-wrap items-center gap-4">
          <div aria-label="样式预览" className="flex h-20 min-w-56 items-end justify-center gap-1 rounded-xl bg-[#0b0d14] px-5 pb-3 font-bold"
            style={{ WebkitTextStroke: `1px ${t.outline_color}` }}>
            {words.map((w, i) => {
              const sung = i < 2;
              const show = r.enabled && (r.target === 'all' || i !== 2);  // の is kana
              return (
                <span key={i} className="flex flex-col items-center">
                  <span className="text-[10px] leading-3" style={{ WebkitTextStroke: '0', color: sung ? (r.follow_colors ? t.color_sung : r.color_sung) : (r.follow_colors ? t.color_unsung : r.color_unsung), visibility: show ? 'visible' : 'hidden' }}>{sample[i]}</span>
                  <span className="text-[26px] leading-8" style={{ color: sung ? t.color_sung : t.color_unsung }}>{w}</span>
                </span>
              );
            })}
          </div>
          <ul className="space-y-1 text-[13px] text-muted">{summary.map((x) => <li key={x}>{x}</li>)}</ul>
        </div>

        <Group label="换配色" hint="只换颜色与描边，布局、注音和时间保持不变">
          <div role="radiogroup" aria-label="配色" className="flex flex-wrap gap-2">
            {presets.map((p) => (
              <button key={p.name} type="button" role="radio" aria-checked={style.preset === p.name}
                onClick={() => edit((k) => {
                  const look = p.style.text;
                  Object.assign(k.text, { color_unsung: look.color_unsung, color_sung: look.color_sung, outline_color: look.outline_color,
                    outline: look.outline, shadow: look.shadow, shadow_color: look.shadow_color, shadow_opacity: look.shadow_opacity });
                  Object.assign(k.ruby, { color_unsung: look.color_unsung, color_sung: look.color_sung, outline_color: look.outline_color });
                  k.preset = p.name;
                })}
                className={cn('focus-ring flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-[13px] transition hover:border-line-strong',
                  style.preset === p.name ? 'border-accent ring-1 ring-accent' : 'border-line')}>
                <span className="rounded bg-[#0b0d14] px-1.5 text-sm font-bold" style={{ WebkitTextStroke: `0.5px ${p.style.text.outline_color}` }}>
                  <span style={{ color: p.style.text.color_sung }}>歌</span><span style={{ color: p.style.text.color_unsung }}>詞</span>
                </span>
                {p.label}
              </button>
            ))}
          </div>
        </Group>

        <div className="flex flex-wrap items-center gap-4">
          <Switch checked={r.enabled} onChange={(v) => edit((k) => { k.ruby.enabled = v; })} label="标注读音" />
          {r.enabled && (
            <>
              <Segmented<KaraokeStyle['ruby']['script']> size="sm" value={r.script} onChange={(v) => edit((k) => { k.ruby.script = v; })}
                options={[{ value: 'hiragana', label: '平假名' }, { value: 'katakana', label: '片假名' }, { value: 'romaji', label: '罗马音' }]} />
              <Segmented<KaraokeStyle['ruby']['target']> size="sm" value={r.target} onChange={(v) => edit((k) => { k.ruby.target = v; })}
                options={[{ value: 'kanji', label: '仅汉字' }, { value: 'all', label: '全部' }]} />
            </>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-4">
          <Switch checked={L.show_translation} onChange={(v) => edit((k) => { k.layout.show_translation = v; })} label="加入翻译字幕（歌词有翻译时）" />
          {L.show_translation && (
            <Segmented<KaraokeStyle['layout']['translation_position']> size="sm" value={L.translation_position}
              onChange={(v) => edit((k) => { k.layout.translation_position = v; })}
              options={[
                { value: 'opposite', label: L.position === 'bottom' ? '画面顶部' : '画面底部' },
                { value: 'block', label: '歌词旁' },
                { value: 'line', label: '每行下方' },
              ]} />
          )}
        </div>

        <div className="flex flex-wrap items-center gap-4">
          <Switch checked={M.advance_ms > 0} onChange={(v) => edit((k) => { k.timing.advance_ms = v ? 150 : 0; })} label="歌词提前显示（扫光比实际演唱早一点）" />
          {M.advance_ms > 0 && (
            <div className="min-w-72 flex-1">
              <TimingSlider name="歌词提前" value={M.advance_ms} max={1000} min={10} step={10} onCommit={(v) => edit((k) => { k.timing.advance_ms = v; })} />
            </div>
          )}
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="提前出现" hint="歌词至少在开唱前这么久出现">
            <TimingSlider name="提前出现" value={M.lead_in_ms} max={8000} onCommit={(v) => edit((k) => { k.timing.lead_in_ms = v; })} />
          </Field>
          <Field label="唱完停留" hint="唱完后继续显示这么久">
            <TimingSlider name="唱完停留" value={M.hold_ms} max={5000} onCommit={(v) => edit((k) => { k.timing.hold_ms = v; })} />
          </Field>
        </div>

        <div className="flex flex-wrap items-center gap-3 border-t border-line pt-4 text-[13px]">
          <span className="text-muted">使用某个项目调好的样式</span>
          <Select aria-label="从项目复制样式" value="" className="max-w-72" onChange={(e) => e.target.value && copyFrom(e.target.value)}>
            <option value="">选择项目…</option>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </Select>
        </div>
        <p className="text-xs text-subtle">字号、位置、描边等更细的调整：在详细模式的“卡拉OK字幕”页调好后，点“设为极简模式默认”。</p>
      </CardBody>
    </Card>
  );
}

function TimingSlider({ name, value, max, onCommit, min = 0, step = 100 }: {
  name: string; value: number; max: number; onCommit: (v: number) => void; min?: number; step?: number;
}) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return <SliderField name={name} value={v} onChange={setV} onCommit={onCommit} min={min} max={max} step={step} unit="ms" />;
}

/** Like Field, but for button groups (a <label> would rename the first button). */
function Group({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div role="group" aria-label={label} className="flex flex-col items-start gap-1.5">
      <span className="text-[13px] font-medium text-fg">{label}</span>
      {children}
      {hint && <span className="text-xs text-muted">{hint}</span>}
    </div>
  );
}
