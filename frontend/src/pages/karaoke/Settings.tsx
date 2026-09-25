// Karaoke style settings: 布局 / 歌词 / 注音 / 时间. Every control edits a draft
// style; the page saves it (debounced) and refreshes the preview.

import { useState, type ReactNode } from 'react';
import { cn } from '@/lib/format';
import type { FontFamily, KaraokeStyle } from '@/lib/types';
import { Segmented, Select, SliderField, Switch, Tabs } from '@/components/ui';

type Patch = (fn: (s: KaraokeStyle) => void) => void;

function Row({ label, hint, children }: { label: ReactNode; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <div className="text-[13px] font-medium">{label}</div>
      {children}
      {hint && <div className="text-xs text-muted">{hint}</div>}
    </div>
  );
}

function Px({ name, value, onChange, max, min = 0, step = 1, unit = 'px' }: {
  name: string; value: number; onChange: (v: number) => void; max: number; min?: number; step?: number; unit?: string;
}) {
  return <SliderField name={name} value={value} onChange={onChange} min={min} max={max} step={step} unit={unit} trackClassName="min-w-24" />;
}

export function ColorField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="flex items-center gap-2 text-[13px]">
      <span className="relative size-7 shrink-0 overflow-hidden rounded-md ring-1 ring-line-strong" style={{ background: value }}>
        <input type="color" aria-label={label} value={value} onChange={(e) => onChange(e.target.value.toUpperCase())}
          className="absolute inset-0 cursor-pointer opacity-0" />
      </span>
      <span className="min-w-0 flex-1 text-muted">{label}</span>
      <span className="font-mono text-xs text-subtle">{value}</span>
    </label>
  );
}

function FontSelect({ value, fonts, fallback, onChange, allowFollow }: {
  value: string; fonts: FontFamily[]; fallback: string; onChange: (v: string) => void; allowFollow?: string;
}) {
  return (
    <Select value={value} onChange={(e) => onChange(e.target.value)} aria-label="字体">
      <option value="">{allowFollow ?? `默认（${fallback}）`}</option>
      {fonts.map((f) => (
        <option key={f.family} value={f.family}>
          {f.family}{f.names.length > 1 && f.names[1] !== f.family ? ` · ${f.names.find((n) => /[぀-ヿ一-鿿]/.test(n)) ?? ''}` : ''}
        </option>
      ))}
    </Select>
  );
}

export function StyleSettings({ style, patch, fonts, defaultFont }: {
  style: KaraokeStyle; patch: Patch; fonts: FontFamily[]; defaultFont: string;
}) {
  const { layout: L, text: T, ruby: R, timing: M } = style;
  const tabs = [
    {
      value: 'layout', label: '布局', content: (
        <div className="space-y-4">
          <Row label="位置">
            <Segmented value={L.position} onChange={(v) => patch((s) => { s.layout.position = v; })}
              options={[{ value: 'bottom', label: '靠底' }, { value: 'top', label: '靠顶' }]} />
          </Row>
          <Row label="同屏行数" hint={L.lines > 1 ? '下一行提前出现在另一行的位置，便于跟唱' : '每次只显示正在唱的一行'}>
            <Segmented value={String(L.lines)} onChange={(v) => patch((s) => { s.layout.lines = Number(v); })}
              options={[{ value: '1', label: '1 行' }, { value: '2', label: '2 行' }, { value: '3', label: '3 行' }]} />
          </Row>
          {L.lines > 1 && (
            <Row label="排列">
              <Segmented value={L.arrangement} onChange={(v) => patch((s) => { s.layout.arrangement = v; })}
                options={[{ value: 'alternate', label: '左右交替' }, { value: 'center', label: '全部居中' }]} />
            </Row>
          )}
          <Row label="与画面边缘的距离"><Px name="纵向边距" value={L.margin_v} max={400} onChange={(v) => patch((s) => { s.layout.margin_v = v; })} /></Row>
          {L.lines > 1 && <Row label="行与行的间距"><Px name="行间距" value={L.line_spacing} max={200} onChange={(v) => patch((s) => { s.layout.line_spacing = v; })} /></Row>}
          <Row label="左右边距" hint="歌词最宽可以用到的范围：画面宽度 − 两侧边距">
            <Px name="左右边距" value={L.margin_h} max={600} onChange={(v) => patch((s) => { s.layout.margin_h = v; })} />
          </Row>
          {L.lines > 1 && L.arrangement === 'alternate' && (
            <Row label="交替行向中间缩进" hint="上行从左边距再向右、下行从右边距再向左缩进这么多，短句不会分到两端；放不下的长句会自动退回边距处">
              <Px name="向中间缩进" value={L.alternate_indent} max={800} onChange={(v) => patch((s) => { s.layout.alternate_indent = v; })} />
            </Row>
          )}
          <Switch checked={L.shrink_long_lines} onChange={(v) => patch((s) => { s.layout.shrink_long_lines = v; })} label="过长的行自动缩小，保证不超出边距" />
          <Switch checked={L.show_translation} onChange={(v) => patch((s) => { s.layout.show_translation = v; })} label="显示翻译字幕（歌词有翻译时）" />
          {L.show_translation && (
            <>
              <Row label="翻译的位置" hint={{
                opposite: `一次一行，显示在画面${L.position === 'bottom' ? '顶部' : '底部'}，跟随正在唱的歌词`,
                block: `一次一行，紧挨在歌词${L.position === 'bottom' ? '上方' : '下方'}`,
                line: '每行歌词下方各自显示（占用更多高度）',
              }[L.translation_position]}>
                <Segmented value={L.translation_position} onChange={(v) => patch((s) => { s.layout.translation_position = v; })}
                  options={[
                    { value: 'opposite', label: L.position === 'bottom' ? '画面顶部' : '画面底部' },
                    { value: 'block', label: '歌词旁' },
                    { value: 'line', label: '每行下方' },
                  ]} />
              </Row>
              <Row label="翻译字号（相对歌词）"><Px name="翻译字号" unit="%" min={25} value={L.translation_size_pct} max={100} onChange={(v) => patch((s) => { s.layout.translation_size_pct = v; })} /></Row>
            </>
          )}
          <p className="text-xs text-subtle">像素值以 1080p 画面为准，其他分辨率按比例缩放。</p>
        </div>
      ),
    },
    {
      value: 'text', label: '歌词', content: (
        <div className="space-y-4">
          <Row label="字体"><FontSelect value={T.font} fonts={fonts} fallback={defaultFont} onChange={(v) => patch((s) => { s.text.font = v; })} /></Row>
          <Row label="字号"><Px name="字号" value={T.size} min={24} max={200} onChange={(v) => patch((s) => { s.text.size = v; })} /></Row>
          <Switch checked={T.bold} onChange={(v) => patch((s) => { s.text.bold = v; })} label="粗体" />
          <div className="grid gap-2.5 rounded-xl border border-line p-3">
            <ColorField label="未唱" value={T.color_unsung} onChange={(v) => patch((s) => { s.text.color_unsung = v; })} />
            <ColorField label="已唱（扫光）" value={T.color_sung} onChange={(v) => patch((s) => { s.text.color_sung = v; })} />
            <ColorField label="描边" value={T.outline_color} onChange={(v) => patch((s) => { s.text.outline_color = v; })} />
            <ColorField label="阴影" value={T.shadow_color} onChange={(v) => patch((s) => { s.text.shadow_color = v; })} />
          </div>
          <Row label="描边宽度"><Px name="描边宽度" value={T.outline} max={16} step={0.5} onChange={(v) => patch((s) => { s.text.outline = v; })} /></Row>
          <Row label="阴影距离"><Px name="阴影距离" value={T.shadow} max={16} step={0.5} onChange={(v) => patch((s) => { s.text.shadow = v; })} /></Row>
          <Row label="阴影不透明度"><Px name="阴影不透明度" unit="%" value={T.shadow_opacity} max={100} onChange={(v) => patch((s) => { s.text.shadow_opacity = v; })} /></Row>
          <Row label="高亮方式">
            <Segmented value={M.highlight} onChange={(v) => patch((s) => { s.timing.highlight = v; })}
              options={[{ value: 'sweep', label: '平滑扫光' }, { value: 'instant', label: '逐字变色' }]} />
          </Row>
        </div>
      ),
    },
    {
      value: 'ruby', label: '注音', content: (
        <div className="space-y-4">
          <Switch checked={R.enabled} onChange={(v) => patch((s) => { s.ruby.enabled = v; })} label="显示注音" />
          <div className={cn('space-y-4', !R.enabled && 'pointer-events-none opacity-45')}>
            <Row label="文字">
              <Segmented value={R.script} onChange={(v) => patch((s) => { s.ruby.script = v; })}
                options={[{ value: 'hiragana', label: '平假名' }, { value: 'katakana', label: '片假名' }, { value: 'romaji', label: '罗马音' }]} />
            </Row>
            <Row label="标注位置" hint={R.target === 'kanji' ? '只在汉字上方标注，送假名（如「舞う」的「う」）不重复标注' : '所有假名也标注（平假名注音在平假名上会自动省略）'}>
              <Segmented value={R.target} onChange={(v) => patch((s) => { s.ruby.target = v; })}
                options={[{ value: 'kanji', label: '仅汉字' }, { value: 'all', label: '全部' }]} />
            </Row>
            <Row label="注音过宽时" hint={R.fit === 'widen' ? '把歌词拉开，注音不会压到相邻的字上' : '保持歌词紧凑，注音可以超出汉字宽度'}>
              <Segmented value={R.fit} onChange={(v) => patch((s) => { s.ruby.fit = v; })}
                options={[{ value: 'widen', label: '加宽歌词' }, { value: 'overflow', label: '允许超出' }]} />
            </Row>
            <Row label="字号（相对歌词）"><Px name="注音字号" unit="%" min={20} value={R.size_pct} max={80} onChange={(v) => patch((s) => { s.ruby.size_pct = v; })} /></Row>
            <Row label="与歌词的间距"><Px name="注音间距" value={R.gap} min={-20} max={60} onChange={(v) => patch((s) => { s.ruby.gap = v; })} /></Row>
            <Row label="字体"><FontSelect value={R.font} fonts={fonts} fallback={defaultFont} allowFollow="跟随歌词字体" onChange={(v) => patch((s) => { s.ruby.font = v; })} /></Row>
            <Switch checked={R.follow_colors} onChange={(v) => patch((s) => { s.ruby.follow_colors = v; })} label="颜色与描边跟随歌词" />
            {!R.follow_colors && (
              <>
                <div className="grid gap-2.5 rounded-xl border border-line p-3">
                  <ColorField label="未唱" value={R.color_unsung} onChange={(v) => patch((s) => { s.ruby.color_unsung = v; })} />
                  <ColorField label="已唱" value={R.color_sung} onChange={(v) => patch((s) => { s.ruby.color_sung = v; })} />
                  <ColorField label="描边" value={R.outline_color} onChange={(v) => patch((s) => { s.ruby.outline_color = v; })} />
                </div>
                <Row label="描边宽度"><Px name="注音描边" value={R.outline} max={12} step={0.5} onChange={(v) => patch((s) => { s.ruby.outline = v; })} /></Row>
              </>
            )}
          </div>
        </div>
      ),
    },
    {
      value: 'timing', label: '时间', content: (
        <div className="space-y-4">
          <Row label="提前显示" hint="歌词至少在开唱前这么久出现">
            <Px name="提前显示" unit="ms" value={M.lead_in_ms} max={5000} step={100} onChange={(v) => patch((s) => { s.timing.lead_in_ms = v; })} />
          </Row>
          <Row label="唱完后停留"><Px name="唱完后停留" unit="ms" value={M.hold_ms} max={3000} step={100} onChange={(v) => patch((s) => { s.timing.hold_ms = v; })} /></Row>
          <Switch checked={M.advance_ms > 0} onChange={(v) => patch((s) => { s.timing.advance_ms = v ? 150 : 0; })} label="歌词提前显示（扫光比实际演唱早一点）" />
          {M.advance_ms > 0 && (
            <Row label="提前多少" hint="一般 100–200 ms 看起来更跟手；同样作用于导出的 LRC（alignment.json / CSV 保持原始时间）">
              <Px name="歌词提前" unit="ms" value={M.advance_ms} min={10} max={1000} step={10} onChange={(v) => patch((s) => { s.timing.advance_ms = v; })} />
            </Row>
          )}
          <Switch checked={M.early_show} onChange={(v) => patch((s) => { s.timing.early_show = v; })} label="位置空出后尽早显示下一行" />
          {M.early_show && (
            <Row label="最多提前" hint="长间奏时不会过早出现">
              <Px name="最多提前" unit="ms" value={M.early_max_ms} min={1000} max={10000} step={500} onChange={(v) => patch((s) => { s.timing.early_max_ms = v; })} />
            </Row>
          )}
          <p className="text-xs text-subtle">字幕时间来自当前对齐结果（包含人工修改）。</p>
        </div>
      ),
    },
  ];
  return <SettingsTabs tabs={tabs} />;
}

function SettingsTabs({ tabs }: { tabs: { value: string; label: ReactNode; content: ReactNode }[] }) {
  const [tab, setTab] = useState('layout');
  return <Tabs value={tab} onChange={setTab} tabs={tabs} />;
}
