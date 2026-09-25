// Subtitle style panel, shared by the detailed 卡拉OK字幕 page and the simple-mode
// settings.  Top: saved styles (预设).  Then collapsible sections:
//   配色 (every colour in one place) · 歌词 · 注音 · 翻译 · 布局 · 时间 · 特效
// Each section shows a one-line summary while closed.  Every control edits the
// style; the parent decides how to save it.

import {
  Check, ChevronDown, Download, ExternalLink, Film, Languages, Palette, Save, Sparkles, Timer, Trash2, Type, X,
  LayoutTemplate, CaseSensitive,
} from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { cn } from '@/lib/format';
import type { FontFamily, KaraokeStyle } from '@/lib/types';
import { run, toast } from '@/store/app';
import {
  deleteEffect, deleteStyle, importEffect, loadEffects, loadSavedStyles, sameLook, saveStyle, useLibrary,
} from '@/store/styles';
import { Badge, Button, DropZone, Input, Segmented, Select, SliderField, Switch } from '@/components/ui';

export type SectionId = 'colors' | 'text' | 'ruby' | 'translation' | 'layout' | 'timing' | 'effects';
type Patch = (fn: (s: KaraokeStyle) => void) => void;

export interface TranslationInfo {
  /** lines of the current lyrics that have a translation (undefined: no project, e.g. the simple-mode settings) */
  lines?: number;
  /** fetch the translation from the lyrics' music platform */
  onFetch?: () => void;
}

// ------------------------------------------------------------------ small building blocks

function Row({ label, hint, children }: { label: ReactNode; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <div className="text-[13px] font-medium">{label}</div>
      {children}
      {hint && <div className="text-xs text-muted">{hint}</div>}
    </div>
  );
}

function Num({ name, value, onChange, max, min = 0, step = 1, unit = 'px' }: {
  name: string; value: number; onChange: (v: number) => void; max: number; min?: number; step?: number; unit?: string;
}) {
  return <SliderField name={name} value={value} onChange={onChange} min={min} max={max} step={step} unit={unit} trackClassName="min-w-24" />;
}

export function ColorField({ label, value, onChange, disabled }: { label: string; value: string; onChange: (v: string) => void; disabled?: boolean }) {
  return (
    <label className={cn('flex items-center gap-2 text-[13px]', disabled && 'opacity-45')}>
      <span className="relative size-7 shrink-0 overflow-hidden rounded-md ring-1 ring-line-strong" style={{ background: value }}>
        <input type="color" aria-label={label} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value.toUpperCase())}
          className="absolute inset-0 cursor-pointer opacity-0" />
      </span>
      <span className="min-w-0 flex-1 text-muted">{label}</span>
      <span className="font-mono text-xs text-subtle">{value}</span>
    </label>
  );
}

function FontSelect({ label, value, fonts, fallback, onChange, followLabel }: {
  label: string; value: string; fonts: FontFamily[]; fallback: string; onChange: (v: string) => void; followLabel?: string;
}) {
  return (
    <Select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
      <option value="">{followLabel ?? `默认（${fallback || '系统日文字体'}）`}</option>
      {fonts.map((f) => (
        <option key={f.family} value={f.family}>
          {f.family}{f.names.length > 1 && f.names[1] !== f.family ? ` · ${f.names.find((n) => /[぀-ヿ一-鿿]/.test(n)) ?? ''}` : ''}
        </option>
      ))}
    </Select>
  );
}

function Section({ id, icon, title, summary, open, onToggle, children }: {
  id: SectionId; icon: ReactNode; title: string; summary: ReactNode; open: boolean; onToggle: () => void; children: ReactNode;
}) {
  return (
    <section className="border-b border-line last:border-b-0" data-section={id}>
      <button type="button" onClick={onToggle} aria-expanded={open}
        className="focus-ring flex w-full items-center gap-2.5 px-1 py-3 text-left">
        <span className={cn('grid size-7 shrink-0 place-items-center rounded-lg', open ? 'bg-accent-soft text-accent' : 'bg-surface-2 text-muted')}>{icon}</span>
        <span className="text-[14px] font-semibold">{title}</span>
        {!open && <span className="min-w-0 flex-1 truncate text-xs text-muted">{summary}</span>}
        <ChevronDown className={cn('ml-auto size-4 shrink-0 text-subtle transition', open && 'rotate-180')} />
      </button>
      {open && <div className="space-y-4 px-1 pb-5">{children}</div>}
    </section>
  );
}

const Dot = ({ c }: { c: string }) => <span className="inline-block size-2.5 rounded-full ring-1 ring-line-strong align-middle" style={{ background: c }} />;

// ------------------------------------------------------------------ the panel

export function StylePanel({ style, onChange, fonts, defaultFont, defaultOpen = ['colors'], storageKey, translation }: {
  style: KaraokeStyle;
  onChange: (next: KaraokeStyle) => void;
  fonts: FontFamily[];
  defaultFont: string;
  defaultOpen?: SectionId[];
  /** remember which sections are open (per place the panel is used) */
  storageKey?: string;
  translation?: TranslationInfo;
}) {
  const [open, setOpen] = useState<Set<SectionId>>(() => {
    try {
      const v = storageKey ? localStorage.getItem(`kara.style.${storageKey}`) : null;
      if (v) return new Set(JSON.parse(v) as SectionId[]);
    } catch { /* ignore */ }
    return new Set(defaultOpen);
  });
  const toggle = (id: SectionId) => setOpen((o) => {
    const n = new Set(o);
    if (n.has(id)) n.delete(id); else n.add(id);
    try { if (storageKey) localStorage.setItem(`kara.style.${storageKey}`, JSON.stringify([...n])); } catch { /* ignore */ }
    return n;
  });
  const patch: Patch = (fn) => {
    const next = structuredClone(style);
    fn(next);
    onChange(next);
  };
  const { layout: L, text: T, ruby: R, translation: Tr, glow: G, timing: M, effects: E } = style;
  const posLabel = { opposite: L.position === 'bottom' ? '画面顶部' : '画面底部', block: '歌词旁', line: '每行下方' };
  const sec = (id: SectionId) => ({ id, open: open.has(id), onToggle: () => toggle(id) });

  return (
    <div>
      <PresetBar style={style} onChange={onChange} />
      <div className="mt-2">
        <Section {...sec('colors')} icon={<Palette className="size-4" />} title="配色"
          summary={<span className="flex items-center gap-1.5">歌词 <Dot c={T.color_unsung} /><Dot c={T.color_sung} /><Dot c={T.outline_color} />
            {G.enabled && <>· 荧光 <Dot c={G.color_unsung} /><Dot c={G.color_sung} /></>}</span>}>
          <ColorGroup title="歌词">
            <ColorField label="未唱" value={T.color_unsung} onChange={(v) => patch((s) => { s.text.color_unsung = v; })} />
            <ColorField label="已唱（扫光）" value={T.color_sung} onChange={(v) => patch((s) => { s.text.color_sung = v; })} />
            <ColorField label="描边" value={T.outline_color} onChange={(v) => patch((s) => { s.text.outline_color = v; })} />
            <ColorField label="阴影" value={T.shadow_color} onChange={(v) => patch((s) => { s.text.shadow_color = v; })} />
          </ColorGroup>
          <ColorGroup title="注音" action={<Switch checked={R.follow_colors} onChange={(v) => patch((s) => { s.ruby.follow_colors = v; })} label={<span className="text-xs text-muted">跟随歌词</span>} />}>
            {!R.follow_colors ? (
              <>
                <ColorField label="未唱" value={R.color_unsung} onChange={(v) => patch((s) => { s.ruby.color_unsung = v; })} />
                <ColorField label="已唱" value={R.color_sung} onChange={(v) => patch((s) => { s.ruby.color_sung = v; })} />
                <ColorField label="描边" value={R.outline_color} onChange={(v) => patch((s) => { s.ruby.outline_color = v; })} />
              </>
            ) : <p className="text-xs text-subtle">与歌词相同的颜色和描边</p>}
          </ColorGroup>
          <ColorGroup title="翻译">
            <ColorField label="文字" value={Tr.color} onChange={(v) => patch((s) => { s.translation.color = v; })} />
            <ColorField label="描边" value={Tr.outline_color} onChange={(v) => patch((s) => { s.translation.outline_color = v; })} />
          </ColorGroup>
          <ColorGroup title="荧光边缘" action={!G.enabled ? <span className="text-xs text-subtle">在“特效”中开启</span> : undefined}>
            <ColorField label="未唱时" value={G.color_unsung} disabled={!G.enabled} onChange={(v) => patch((s) => { s.glow.color_unsung = v; })} />
            <ColorField label="唱过后" value={G.color_sung} disabled={!G.enabled} onChange={(v) => patch((s) => { s.glow.color_sung = v; })} />
          </ColorGroup>
        </Section>

        <Section {...sec('text')} icon={<Type className="size-4" />} title="歌词"
          summary={`${T.font || defaultFont || '默认字体'} · ${T.size}px${T.bold ? ' · 粗体' : ''} · 描边 ${T.outline}`}>
          <Row label="字体"><FontSelect label="歌词字体" value={T.font} fonts={fonts} fallback={defaultFont} onChange={(v) => patch((s) => { s.text.font = v; })} /></Row>
          <Row label="字号"><Num name="字号" value={T.size} min={24} max={200} onChange={(v) => patch((s) => { s.text.size = v; })} /></Row>
          <Switch checked={T.bold} onChange={(v) => patch((s) => { s.text.bold = v; })} label="粗体" />
          <Row label="描边宽度"><Num name="描边宽度" value={T.outline} max={16} step={0.5} onChange={(v) => patch((s) => { s.text.outline = v; })} /></Row>
          <Row label="阴影距离"><Num name="阴影距离" value={T.shadow} max={16} step={0.5} onChange={(v) => patch((s) => { s.text.shadow = v; })} /></Row>
          <Row label="阴影不透明度"><Num name="阴影不透明度" unit="%" value={T.shadow_opacity} max={100} onChange={(v) => patch((s) => { s.text.shadow_opacity = v; })} /></Row>
          <Row label="高亮方式">
            <Segmented value={M.highlight} onChange={(v) => patch((s) => { s.timing.highlight = v; })}
              options={[{ value: 'sweep', label: '平滑扫光' }, { value: 'instant', label: '逐字变色' }]} />
          </Row>
        </Section>

        <Section {...sec('ruby')} icon={<CaseSensitive className="size-4" />} title="注音"
          summary={R.enabled ? `${{ hiragana: '平假名', katakana: '片假名', romaji: '罗马音' }[R.script]} · ${R.target === 'kanji' ? '仅汉字' : '全部'} · ${R.size_pct}%` : '关闭'}>
          <Switch checked={R.enabled} onChange={(v) => patch((s) => { s.ruby.enabled = v; })} label="显示注音" />
          <div className={cn('space-y-4', !R.enabled && 'pointer-events-none opacity-45')}>
            <Row label="文字">
              <Segmented value={R.script} onChange={(v) => patch((s) => { s.ruby.script = v; })}
                options={[{ value: 'hiragana', label: '平假名' }, { value: 'katakana', label: '片假名' }, { value: 'romaji', label: '罗马音' }]} />
            </Row>
            <Row label="标注位置" hint={R.target === 'kanji' ? '只在汉字上方标注，送假名不重复标注' : '所有假名也标注（平假名注音在平假名上会自动省略）'}>
              <Segmented value={R.target} onChange={(v) => patch((s) => { s.ruby.target = v; })}
                options={[{ value: 'kanji', label: '仅汉字' }, { value: 'all', label: '全部' }]} />
            </Row>
            <Row label="注音过宽时">
              <Segmented value={R.fit} onChange={(v) => patch((s) => { s.ruby.fit = v; })}
                options={[{ value: 'widen', label: '加宽歌词' }, { value: 'overflow', label: '允许超出' }]} />
            </Row>
            <Row label="字号（相对歌词）"><Num name="注音字号" unit="%" min={20} value={R.size_pct} max={80} onChange={(v) => patch((s) => { s.ruby.size_pct = v; })} /></Row>
            <Row label="与歌词的间距"><Num name="注音间距" value={R.gap} min={-20} max={60} onChange={(v) => patch((s) => { s.ruby.gap = v; })} /></Row>
            <Row label="字体"><FontSelect label="注音字体" value={R.font} fonts={fonts} fallback={defaultFont} followLabel="跟随歌词字体" onChange={(v) => patch((s) => { s.ruby.font = v; })} /></Row>
            {!R.follow_colors && (
              <Row label="描边宽度"><Num name="注音描边" value={R.outline} max={12} step={0.5} onChange={(v) => patch((s) => { s.ruby.outline = v; })} /></Row>
            )}
          </div>
        </Section>

        <Section {...sec('translation')} icon={<Languages className="size-4" />} title="翻译"
          summary={Tr.enabled ? `${posLabel[Tr.position]} · ${Tr.size_pct}%` : '关闭'}>
          <Switch checked={Tr.enabled} onChange={(v) => patch((s) => { s.translation.enabled = v; })} label="显示翻译字幕（歌词有翻译时）" />
          {Tr.enabled && translation?.lines === 0 && (
            <div className="flex flex-wrap items-center gap-2 rounded-lg bg-warn-soft px-3 py-2 text-xs text-warn">
              这首歌的歌词还没有翻译，预览里不会出现。
              {translation.onFetch && <Button size="xs" variant="secondary" icon={<Download className="size-3.5" />} onClick={translation.onFetch}>从音乐平台获取翻译</Button>}
            </div>
          )}
          <div className={cn('space-y-4', !Tr.enabled && 'pointer-events-none opacity-45')}>
            <Row label="位置" hint={{
              opposite: `一次一行，显示在${posLabel.opposite}，跟随正在唱的歌词`,
              block: `一次一行，紧挨在歌词${L.position === 'bottom' ? '上方' : '下方'}`,
              line: '每行歌词下方各自显示（占用更多高度）',
            }[Tr.position]}>
              <Segmented value={Tr.position} onChange={(v) => patch((s) => { s.translation.position = v; })}
                options={[{ value: 'opposite', label: posLabel.opposite }, { value: 'block', label: '歌词旁' }, { value: 'line', label: '每行下方' }]} />
            </Row>
            <Row label="字号（相对歌词）"><Num name="翻译字号" unit="%" min={20} value={Tr.size_pct} max={100} onChange={(v) => patch((s) => { s.translation.size_pct = v; })} /></Row>
            <Row label="字体"><FontSelect label="翻译字体" value={Tr.font} fonts={fonts} fallback={defaultFont} followLabel="跟随歌词字体" onChange={(v) => patch((s) => { s.translation.font = v; })} /></Row>
            <Switch checked={Tr.bold} onChange={(v) => patch((s) => { s.translation.bold = v; })} label="粗体" />
            <Row label="描边宽度"><Num name="翻译描边" value={Tr.outline} max={12} step={0.5} onChange={(v) => patch((s) => { s.translation.outline = v; })} /></Row>
            <Row label="阴影距离"><Num name="翻译阴影" value={Tr.shadow} max={12} step={0.5} onChange={(v) => patch((s) => { s.translation.shadow = v; })} /></Row>
            <Switch checked={Tr.glow} onChange={(v) => patch((s) => { s.translation.glow = v; })} label="开启荧光边缘时，翻译也发光" />
          </div>
        </Section>

        <Section {...sec('layout')} icon={<LayoutTemplate className="size-4" />} title="布局"
          summary={`${L.position === 'bottom' ? '靠底' : '靠顶'} · ${L.lines} 行${L.lines > 1 ? (L.arrangement === 'alternate' ? '左右交替' : '居中') : ''} · 边距 ${L.margin_v}`}>
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
          <Row label="与画面边缘的距离"><Num name="纵向边距" value={L.margin_v} max={400} onChange={(v) => patch((s) => { s.layout.margin_v = v; })} /></Row>
          {L.lines > 1 && <Row label="行与行的间距"><Num name="行间距" value={L.line_spacing} max={200} onChange={(v) => patch((s) => { s.layout.line_spacing = v; })} /></Row>}
          <Row label="左右边距" hint="歌词最宽可以用到的范围：画面宽度 − 两侧边距">
            <Num name="左右边距" value={L.margin_h} max={600} onChange={(v) => patch((s) => { s.layout.margin_h = v; })} />
          </Row>
          {L.lines > 1 && L.arrangement === 'alternate' && (
            <Row label="交替行向中间缩进" hint="短句不会分到两端；放不下的长句自动退回边距处">
              <Num name="向中间缩进" value={L.alternate_indent} max={800} onChange={(v) => patch((s) => { s.layout.alternate_indent = v; })} />
            </Row>
          )}
          <Switch checked={L.shrink_long_lines} onChange={(v) => patch((s) => { s.layout.shrink_long_lines = v; })} label="过长的行自动缩小，保证不超出边距" />
          <p className="text-xs text-subtle">像素值以 1080p 画面为准，其他分辨率按比例缩放。</p>
        </Section>

        <Section {...sec('timing')} icon={<Timer className="size-4" />} title="时间"
          summary={`提前 ${M.lead_in_ms / 1000}s · 停留 ${M.hold_ms / 1000}s · 淡入淡出 ${M.fade_in_ms}/${M.fade_out_ms}ms${M.advance_ms ? ` · 扫光提前 ${M.advance_ms}ms` : ''}`}>
          <Row label="提前出现" hint="歌词至少在开唱前这么久出现">
            <Num name="提前出现" unit="ms" value={M.lead_in_ms} max={8000} step={100} onChange={(v) => patch((s) => { s.timing.lead_in_ms = v; })} />
          </Row>
          <Row label="唱完后停留"><Num name="唱完后停留" unit="ms" value={M.hold_ms} max={5000} step={100} onChange={(v) => patch((s) => { s.timing.hold_ms = v; })} /></Row>
          <div className="grid gap-4 sm:grid-cols-2">
            <Row label="淡入（缓进）"><Num name="淡入" unit="ms" value={M.fade_in_ms} max={1500} step={50} onChange={(v) => patch((s) => { s.timing.fade_in_ms = v; })} /></Row>
            <Row label="淡出（缓出）"><Num name="淡出" unit="ms" value={M.fade_out_ms} max={1500} step={50} onChange={(v) => patch((s) => { s.timing.fade_out_ms = v; })} /></Row>
          </div>
          <Switch checked={M.early_show} onChange={(v) => patch((s) => { s.timing.early_show = v; })} label="位置空出后尽早显示下一行" />
          {M.early_show && (
            <Row label="最多提前" hint="长间奏时不会过早出现">
              <Num name="最多提前" unit="ms" value={M.early_max_ms} min={1000} max={10000} step={500} onChange={(v) => patch((s) => { s.timing.early_max_ms = v; })} />
            </Row>
          )}
          <Switch checked={M.advance_ms > 0} onChange={(v) => patch((s) => { s.timing.advance_ms = v ? 150 : 0; })} label="歌词提前显示（扫光比实际演唱早一点）" />
          {M.advance_ms > 0 && (
            <Row label="提前多少" hint="一般 100–200 ms 看起来更跟手；同样作用于导出的 LRC（alignment.json / CSV 保持原始时间）">
              <Num name="歌词提前" unit="ms" value={M.advance_ms} min={10} max={1000} step={10} onChange={(v) => patch((s) => { s.timing.advance_ms = v; })} />
            </Row>
          )}
        </Section>

        <Section {...sec('effects')} icon={<Sparkles className="size-4" />} title="特效"
          summary={[G.enabled && '荧光边缘', E.particles !== 'none' && { sakura: '樱花花瓣', snow: '雪花', stars: '星光' }[E.particles], E.overlay && '动效视频']
            .filter(Boolean).join(' · ') || '无'}>
          <EffectsEditor style={style} patch={patch} />
        </Section>
      </div>
    </div>
  );
}

function ColorGroup({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <div className="rounded-xl border border-line p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-xs font-semibold tracking-wide text-muted">{title}</span>
        {action}
      </div>
      <div className="grid gap-2.5 sm:grid-cols-2">{children}</div>
    </div>
  );
}

// ------------------------------------------------------------------ saved styles

function PresetBar({ style, onChange }: { style: KaraokeStyle; onChange: (s: KaraokeStyle) => void }) {
  const saved = useLibrary((s) => s.saved);
  const [naming, setNaming] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  useEffect(() => { if (!saved) void run(() => loadSavedStyles(), '读取预设失败'); }, [saved]);
  const current = saved?.find((x) => x.name === style.preset) ?? null;
  const modified = !!current && !sameLook(current.style, style);

  const pick = (id: string) => {
    const s = saved?.find((x) => x.id === id);
    if (!s) return;
    onChange({ ...structuredClone(s.style), output: style.output });
    setConfirmDelete(false);
  };
  const saveAs = (name: string) => run(async () => {
    const s = await saveStyle(name.trim(), style);
    onChange({ ...style, preset: s.name });
    setNaming(null);
    toast('ok', `已保存预设「${s.name}」`);
  }, '保存预设失败');
  const overwrite = () => current && run(async () => {
    await saveStyle(current.name, style, current.id);
    toast('ok', `已更新预设「${current.name}」`);
  }, '保存预设失败');
  const remove = () => current && run(async () => {
    await deleteStyle(current.id);
    onChange({ ...style, preset: '' });
    setConfirmDelete(false);
    toast('ok', `已删除预设「${current.name}」`);
  }, '删除预设失败');

  return (
    <div className="rounded-xl bg-surface-2/70 p-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[13px] font-medium text-muted">预设</span>
        <Select aria-label="预设" className="h-8 min-w-36 flex-1 text-[13px]" value={current?.id ?? ''} onChange={(e) => pick(e.target.value)}>
          {!current && <option value="">（未保存的样式）</option>}
          {(saved ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}{s.builtin ? '（内置）' : ''}</option>)}
        </Select>
        {modified && <Badge tone="warn">已修改</Badge>}
        {current && !current.builtin && modified && (
          <Button size="xs" variant="primary" icon={<Save className="size-3.5" />} onClick={overwrite}>保存</Button>
        )}
        <Button size="xs" variant="secondary" icon={<Save className="size-3.5" />} onClick={() => setNaming(naming === null ? '' : null)}>另存为</Button>
        {current && !current.builtin && (confirmDelete ? (
          <Button size="xs" variant="danger" icon={<Trash2 className="size-3.5" />} onClick={remove}>确认删除</Button>
        ) : (
          <Button size="xs" variant="ghost" aria-label="删除预设" icon={<Trash2 className="size-3.5" />} onClick={() => setConfirmDelete(true)} />
        ))}
      </div>
      {naming !== null && (
        <div className="mt-2 flex items-center gap-2">
          <Input autoFocus aria-label="预设名称" className="h-8 flex-1 text-[13px]" placeholder="给这套样式起个名字" value={naming}
            onChange={(e) => setNaming(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && naming.trim()) void saveAs(naming); if (e.key === 'Escape') setNaming(null); }} />
          <Button size="xs" variant="primary" icon={<Check className="size-3.5" />} disabled={!naming.trim()} onClick={() => saveAs(naming)}>保存</Button>
          <Button size="xs" variant="ghost" aria-label="取消" icon={<X className="size-3.5" />} onClick={() => setNaming(null)} />
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ effects

function EffectsEditor({ style, patch }: { style: KaraokeStyle; patch: Patch }) {
  const catalog = useLibrary((s) => s.effects);
  const [busy, setBusy] = useState(false);
  const [showSources, setShowSources] = useState(false);
  useEffect(() => { if (!catalog) void run(() => loadEffects(), '读取动效失败'); }, [catalog]);
  const { glow: G, effects: E } = style;
  const video = catalog?.videos.find((v) => v.id === E.overlay) ?? null;

  const upload = (f: File) => run(async () => {
    setBusy(true);
    try {
      const v = await importEffect(f);
      patch((s) => { s.effects.overlay = v.id; });
      toast('ok', `已导入动效「${v.name}」`, v.blend === 'alpha' ? '透明背景' : '黑色背景（按“滤色”叠加）');
    } finally {
      setBusy(false);
    }
  }, '导入动效失败');

  return (
    <div className="space-y-5">
      <div className="space-y-3 rounded-xl border border-line p-3">
        <Switch checked={G.enabled} onChange={(v) => patch((s) => { s.glow.enabled = v; })} label={<span className="font-medium">荧光边缘</span>} />
        {G.enabled && (
          <>
            <div className="grid gap-3 sm:grid-cols-3">
              <Row label="大小"><Num name="荧光大小" value={G.size} min={1} max={40} step={0.5} onChange={(v) => patch((s) => { s.glow.size = v; })} /></Row>
              <Row label="柔和"><Num name="荧光柔和" value={G.blur} min={0} max={30} step={0.5} onChange={(v) => patch((s) => { s.glow.blur = v; })} /></Row>
              <Row label="强度"><Num name="荧光强度" unit="%" value={G.strength} min={10} max={100} onChange={(v) => patch((s) => { s.glow.strength = v; })} /></Row>
            </div>
            <Switch checked={G.ruby} onChange={(v) => patch((s) => { s.glow.ruby = v; })} label="注音也发光" />
            <p className="text-xs text-subtle">颜色在“配色”里调整：未唱与唱过后可以用不同的光。</p>
          </>
        )}
      </div>

      <div className="space-y-3 rounded-xl border border-line p-3">
        <div className="text-[13px] font-medium">背景动效</div>
        <Segmented value={E.particles} onChange={(v) => patch((s) => { s.effects.particles = v; })}
          options={(catalog?.particles ?? [{ id: 'none', label: '无' }]).map((p) => ({ value: p.id, label: p.label }))} />
        {E.particles !== 'none' && (
          <>
            <div className="grid gap-3 sm:grid-cols-3">
              <Row label="数量"><Num name="动效数量" unit="%" value={E.density} min={5} max={200} step={5} onChange={(v) => patch((s) => { s.effects.density = v; })} /></Row>
              <Row label="大小"><Num name="动效大小" unit="%" value={E.size} min={30} max={300} step={5} onChange={(v) => patch((s) => { s.effects.size = v; })} /></Row>
              <Row label="不透明度"><Num name="动效不透明度" unit="%" value={E.opacity} min={5} max={100} onChange={(v) => patch((s) => { s.effects.opacity = v; })} /></Row>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <Switch checked={!!E.color} onChange={(v) => patch((s) => { s.effects.color = v ? '#FFFFFF' : ''; })} label="统一颜色" />
              {E.color && <ColorField label="动效颜色" value={E.color} onChange={(v) => patch((s) => { s.effects.color = v; })} />}
            </div>
          </>
        )}
      </div>

      <div className="space-y-3 rounded-xl border border-line p-3">
        <div className="flex items-center gap-2 text-[13px] font-medium"><Film className="size-4 text-muted" />动效视频（叠加在画面上）</div>
        <div className="flex flex-wrap items-center gap-2">
          <Select aria-label="动效视频" className="h-8 min-w-40 flex-1 text-[13px]" value={E.overlay ?? ''}
            onChange={(e) => patch((s) => { s.effects.overlay = e.target.value || null; })}>
            <option value="">无</option>
            {(catalog?.videos ?? []).map((v) => <option key={v.id} value={v.id}>{v.name}（{v.blend === 'alpha' ? '透明背景' : '黑底'}）</option>)}
          </Select>
          {video && (
            <Button size="xs" variant="ghost" aria-label="删除动效视频" icon={<Trash2 className="size-3.5" />}
              onClick={() => run(async () => { await deleteEffect(video.id); patch((s) => { s.effects.overlay = null; }); })} />
          )}
        </div>
        {E.overlay && !video && catalog && <p className="text-xs text-warn">这个动效视频已不在动效库中</p>}
        {video && (
          <Row label="不透明度"><Num name="动效视频不透明度" unit="%" value={E.overlay_opacity} min={5} max={100} onChange={(v) => patch((s) => { s.effects.overlay_opacity = v; })} /></Row>
        )}
        <DropZone compact busy={busy} accept="video/*,.mov,.mp4,.webm,.mkv"
          title="导入动效视频" hint="透明背景（MOV / WebM）或黑色背景的视频都可以；自动循环铺满画面" onFile={upload} />
        <button type="button" className="focus-ring flex items-center gap-1 rounded text-xs text-accent hover:underline" onClick={() => setShowSources(!showSources)}>
          <ChevronDown className={cn('size-3.5 transition', showSources && 'rotate-180')} />去哪里找免费动效
        </button>
        {showSources && (
          <div className="space-y-1.5">
            <p className="text-xs text-muted">这些素材可以免费用在你的视频里，但许可不允许软件自带分发：点开页面下载后在上面导入即可。</p>
            <ul className="divide-y divide-line overflow-hidden rounded-lg border border-line">
              {(catalog?.sources ?? []).map((src) => (
                <li key={src.url} className="flex items-start gap-2 px-3 py-2 text-xs">
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-fg">{src.name} <span className="font-normal text-subtle">· {src.site}</span></div>
                    <div className="text-muted">{src.format} · {src.license}</div>
                  </div>
                  <a className="flex shrink-0 items-center gap-1 text-accent hover:underline" href={src.url} target="_blank" rel="noreferrer noopener">
                    打开<ExternalLink className="size-3" />
                  </a>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
