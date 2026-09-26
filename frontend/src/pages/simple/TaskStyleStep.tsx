// Step 4 of a simple-mode task: the subtitle look of this song — a colour template
// with one or two theme colours, a saved style or the settings' default — plus the
// switches that change from song to song.  The choices are bound to the task when
// it is added and remembered for the next one.

import { Plus, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import type { AppSettings, KaraokeStyle, TaskStyleOptions, ThemePreview } from '@/lib/types';
import { loadSavedStyles, useLibrary } from '@/store/styles';
import { Segmented, Select, SliderField, Switch } from '@/components/ui';
import { ColorRow } from '@/components/karaoke/ThemeColors';

type RubyChoice = Exclude<TaskStyleOptions['ruby'], 'style'>;
const TEMPLATE_HINT = {
  plain: '朴素：只有扫光变色和描边，干净清楚',
  glow: '荧光：带荧光边缘、翻译发光和字幕后面的小星光',
};

export function TaskStyleStep({ value: o, onChange, settings }: {
  value: TaskStyleOptions;
  onChange: (next: TaskStyleOptions) => void;
  settings: AppSettings['simple'];
}) {
  const saved = useLibrary((s) => s.saved);
  const [theme, setTheme] = useState<KaraokeStyle | null>(null);
  const set = (patch: Partial<TaskStyleOptions>) => onChange({ ...o, ...patch });

  useEffect(() => { if (!saved) void loadSavedStyles().catch(() => undefined); }, [saved]);
  useEffect(() => {
    if (saved && o.saved_id && !saved.some((x) => x.id === o.saved_id)) set({ saved_id: '' });
  }, [saved, o.saved_id]); // eslint-disable-line react-hooks/exhaustive-deps
  // the template's colours come from the server's palette algorithm
  useEffect(() => {
    if (o.source !== 'template') return;
    let stop = false;
    const t = setTimeout(async () => {
      try {
        const r = await api.post<ThemePreview>('/api/karaoke/theme', { template: o.template, color: o.color, secondary: o.secondary || null });
        if (!stop) setTheme(r.style);
      } catch { /* keep the last preview */ }
    }, 150);
    return () => { stop = true; clearTimeout(t); };
  }, [o.source, o.template, o.color, o.secondary]);

  const chosen = o.source === 'saved' ? saved?.find((x) => x.id === o.saved_id)?.style ?? null : null;
  const base: KaraokeStyle | null = o.source === 'template' ? theme : o.source === 'saved' ? chosen : settings.karaoke;
  const translation = o.translation ?? base?.translation.enabled ?? false;
  const songInfo = o.song_info ?? base?.info.enabled ?? false;
  const ruby: RubyChoice = o.ruby === 'style' ? (base && !base.ruby.enabled ? 'off' : base?.ruby.script ?? 'hiragana') : o.ruby;
  const rubyTarget = o.ruby_target ?? base?.ruby.target ?? 'all';
  const audio = o.video_audio ?? settings.video_audio;
  const vocal = o.vocal_keep_pct ?? settings.vocal_keep_pct;
  const shown = useMemo(() => base && {
    ...base,
    translation: { ...base.translation, enabled: translation },
    info: { ...base.info, enabled: songInfo },
  }, [base, translation, songInfo]);

  return (
    <div className="space-y-4">
      <Segmented<TaskStyleOptions['source']> value={o.source}
        onChange={(v) => set({ source: v, translation: null, song_info: null, ruby: 'style', ruby_target: null })} options={[
        { value: 'template', label: '模版配色' },
        { value: 'saved', label: '保存的预设' },
        { value: 'default', label: '设置里的样式' },
      ]} />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,18rem)]">
        <div className="min-w-0 space-y-3">
          {o.source === 'template' && (
            <>
              <div className="space-y-1.5">
                <Segmented<TaskStyleOptions['template']> size="sm" value={o.template} onChange={(v) => set({ template: v })} options={[
                  { value: 'plain', label: '朴素' }, { value: 'glow', label: '荧光' },
                ]} />
                <p className="text-xs text-muted">{TEMPLATE_HINT[o.template]}</p>
              </div>
              <ColorRow label="主色" value={o.color} onChange={(c) => set({ color: c })} />
              {o.secondary ? (
                <ColorRow label="辅色" value={o.secondary} onChange={(c) => set({ secondary: c })}
                  extra={<button type="button" aria-label="去掉辅色" onClick={() => set({ secondary: '' })}
                    className="focus-ring grid size-6 place-items-center rounded-full text-subtle hover:bg-surface-2 hover:text-fg"><X className="size-3.5" /></button>} />
              ) : (
                <button type="button" onClick={() => set({ secondary: '#F5C400' })}
                  className="focus-ring flex items-center gap-1 rounded text-xs text-accent hover:underline">
                  <Plus className="size-3.5" />加一个辅色（双色：例如橙色扫光 + 黄色荧光）
                </button>
              )}
              <p className="text-xs text-subtle">其余颜色（未唱、描边、阴影、翻译、荧光）按主题色自动搭配，并保证文字在描边上足够清楚。</p>
            </>
          )}
          {o.source === 'saved' && (
            <Select aria-label="选择预设" value={o.saved_id} onChange={(e) => set({ saved_id: e.target.value })}>
              <option value="">选择一个预设…</option>
              {(saved ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}{s.builtin ? '（内置）' : ''}</option>)}
            </Select>
          )}
          {o.source === 'default' && (
            <p className="text-[13px] text-muted">使用「设置 → 卡拉OK字幕样式」里的完整样式。</p>
          )}

          <div className="space-y-3 border-t border-line pt-3">
            <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
              <Switch checked={translation} onChange={(v) => set({ translation: v })} label="显示翻译" />
              <Switch checked={songInfo} onChange={(v) => set({ song_info: v })} label="开头显示歌曲信息" />
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[13px]">
              <span className="w-14 shrink-0 text-muted">注音</span>
              <Segmented<RubyChoice> size="sm" value={ruby} onChange={(v) => set({ ruby: v })} options={[
                { value: 'off', label: '无' }, { value: 'hiragana', label: '平假名' },
                { value: 'katakana', label: '片假名' }, { value: 'romaji', label: '罗马音' },
              ]} />
              {ruby !== 'off' && (
                <Switch checked={rubyTarget === 'kanji'} onChange={(v) => set({ ruby_target: v ? 'kanji' : 'all' })} label="仅汉字" />
              )}
            </div>
            {settings.auto_export && (
              <div className="space-y-2 text-[13px]">
                <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                  <span className="w-14 shrink-0 text-muted">视频声音</span>
                  <Segmented<'original' | 'mix' | 'none'> size="sm" value={audio} onChange={(v) => set({ video_audio: v })} options={[
                    { value: 'original', label: '原声' },
                    { value: 'mix', label: '降低人声', disabled: !settings.separate, title: settings.separate ? undefined : '需要在设置里开启人声分离' },
                    { value: 'none', label: '无声' },
                  ]} />
                </div>
                {audio === 'mix' && !settings.separate && (
                  <p className="pl-[4.5rem] text-xs text-warn">人声分离已在设置里关闭，视频会使用原声。</p>
                )}
                {audio === 'mix' && settings.separate && (
                  <div className="max-w-md pl-[4.5rem]">
                    <SliderField name="人声保留" label={<span className="text-muted">人声保留</span>} value={vocal}
                      onChange={(v) => set({ vocal_keep_pct: v })} min={0} max={100} step={1} unit="%" trackClassName="min-w-32" />
                    <p className="mt-1 text-xs text-subtle">0% 为纯伴奏；需要人声分离（设置里开启）。</p>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
        <StyleMock style={shown} />
      </div>
    </div>
  );
}

/** A small CSS mock of the look (colours, outline, glow, translation, title card); the real
 * rendering is the libass preview in the detailed mode. */
function StyleMock({ style }: { style: KaraokeStyle | null }) {
  if (!style) return <div className="grid min-h-36 place-items-center rounded-xl bg-surface-2 text-xs text-subtle">选择样式后显示示意</div>;
  const T = style.text;
  const G = style.glow;
  const Tr = style.translation;
  const text = (color: string, glow?: string): React.CSSProperties => ({
    color, WebkitTextStroke: `4px ${T.outline_color}`, paintOrder: 'stroke fill',
    textShadow: glow ? `0 0 5px ${glow}, 0 0 10px ${glow}` : `1px 1px 0 ${T.shadow_color}80`,
  });
  const line = 'きみと歩いた空';
  return (
    <div className="relative min-h-36 overflow-hidden rounded-xl bg-[linear-gradient(135deg,#1c2436,#3a2f45_55%,#1a1a22)] p-3 font-bold"
      aria-label="字幕示意" role="img">
      {style.info.enabled && (
        <div className="mb-2 flex gap-1.5 text-[11px] leading-tight">
          <span className="w-0.5 rounded" style={{ background: style.info.accent || T.color_sung }} />
          <span style={{ color: style.info.color || T.color_unsung, WebkitTextStroke: `2px ${T.outline_color}`, paintOrder: 'stroke fill' }}>
            歌名<br /><span className="font-normal opacity-90">歌手</span>
          </span>
        </div>
      )}
      {Tr.enabled && (
        <div className="mb-3 text-center text-[13px]" style={{ ...text(Tr.color, G.enabled && Tr.glow ? G.color_unsung : undefined), WebkitTextStroke: `3px ${Tr.outline_color}` }}>
          和你一起走过的天空
        </div>
      )}
      <div className="absolute inset-x-3 bottom-4 text-center leading-none" style={{ fontSize: Math.round(22 * T.size / 88) }}>
        <span className="relative inline-block">
          <span style={text(T.color_unsung, G.enabled ? G.color_unsung : undefined)}>{line}</span>
          <span aria-hidden className="absolute inset-0" style={{ ...text(T.color_sung, G.enabled ? G.color_sung : undefined), clipPath: 'inset(0 45% 0 0)' }}>{line}</span>
          {style.effects.kind !== 'none' && (
            <span aria-hidden className="absolute -top-2 left-[52%] text-[11px]" style={{ color: style.effects.color || (G.enabled ? G.color_sung : T.color_sung) }}>✦ ✧</span>
          )}
        </span>
      </div>
    </div>
  );
}
