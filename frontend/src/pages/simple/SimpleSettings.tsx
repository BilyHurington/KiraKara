// 设置页：极简模式的一键流程用到的全部选项。每项修改立即保存到本机。

import { ArrowUpCircle, Bot, Crosshair, Film, Scissors, Subtitles } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import type { AppSettings, FontFamily, KaraokeStyle } from '@/lib/types';
import { loadProjects, run, toast, useApp } from '@/store/app';
import { loadSettings, saveSettings, useSimple } from '@/store/simple';
import { loadUpdate, useUpdate } from '@/store/update';
import { updateHowTo } from '@/components/shell/UpdateBadge';
import { AiSettingsForm } from '@/components/AiSettingsForm';
import { StylePanel } from '@/components/karaoke/StylePanel';
import { Button, Callout, Card, CardBody, CardHeader, Field, Segmented, Select, Spinner, Switch } from '@/components/ui';

type Simple = AppSettings['simple'];

export function SimpleSettings() {
  const settings = useSimple((s) => s.settings);
  const settingsError = useSimple((s) => s.settingsError);
  const info = useApp((s) => s.info);
  const [retrying, setRetrying] = useState(false);
  if (!settings) {
    const retry = async () => {
      setRetrying(true);
      try { await run(() => loadSettings(), '读取设置失败'); } finally { setRetrying(false); }
    };
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-semibold tracking-tight">设置</h1>
        {settingsError ? (
          <Callout tone="danger" title="读取设置失败" actions={<Button size="sm" loading={retrying} onClick={() => void retry()}>重试</Button>}>
            {settingsError}
          </Callout>
        ) : (
          <div className="flex items-center gap-2 text-sm text-muted" role="status"><Spinner />正在读取设置…</div>
        )}
      </div>
    );
  }
  const s = settings.simple;
  const save = (patch: Partial<Simple>) => run(() => saveSettings({ simple: patch }), '保存设置失败');

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">设置</h1>
        <p className="mt-1 text-sm text-muted">“开始制作”会按这里的选项自动完成每一步。修改立即保存，只对之后添加的任务生效（已在队列里的任务按添加时的选项完成）。</p>
      </div>

      <Card>
        <CardHeader icon={<Bot className="size-4" />} title="AI 注音" description="用 AI 检查每个字的读音（例如「今君」读 いま きみ，「真新」读 まっさら）。读音越准，对齐越准。" />
        <CardBody className="space-y-4">
          <AiSettingsForm />
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
                <Select value={s.separation_preset} disabled={!info} onChange={(e) => save({ separation_preset: e.target.value })}>
                  {/* the saved choice is always listed, also while the list loads or when it is no longer offered */}
                  {!info?.separation_presets.some((p) => p.name === s.separation_preset) && (
                    <option value={s.separation_preset}>{s.separation_preset}{info ? '（不可用）' : '（读取模型列表中…）'}</option>
                  )}
                  {(info?.separation_presets ?? []).map((p) => <option key={p.name} value={p.name}>{p.name}（{p.architecture}）</option>)}
                </Select>
              </Field>
              <Group label="运行设备">
                <Segmented<'auto' | 'cpu'> label="运行设备" value={s.separation_device} onChange={(v) => save({ separation_device: v })}
                  options={[{ value: 'auto', label: '自动（GPU / MPS）' }, { value: 'cpu', label: '仅 CPU' }]} />
              </Group>
            </div>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader icon={<Crosshair className="size-4" />} title="歌词开头对齐" description="视频的声音经常和 LRC 歌词里的时间差一段（片头、不同版本）。每个 LRC 任务需要先找出这段偏移。" />
        <CardBody className="space-y-3">
          <Group label="偏移怎么确定">
            <Segmented<Simple['calibration']> label="偏移怎么确定" value={s.calibration} onChange={(v) => save({ calibration: v })}
              options={[{ value: 'manual', label: '手动标记第一句' }, { value: 'auto', label: '自动检测' }]} />
          </Group>
          <p className="text-xs text-muted">
            {s.calibration === 'manual'
              ? '添加任务后马上请你在波形上标出第一句开始唱的位置，最准。'
              : '人声分离后先试对齐整首歌，用大多数歌词行一致的偏移，不需要原曲音频。没把握时（行数太少、只有部分行对得上、视频是别的速度或剪辑过）仍会请你确认，标记会放在检测到的位置。'}
          </p>
          {s.calibration === 'auto' && !s.separate && (
            <Callout tone="warn">没有分离人声时，自动检测在原曲上进行，伴奏会让它更容易没把握。</Callout>
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
              <p className="text-xs text-muted">视频里的声音（原声 / 降低人声 / 无声）每首歌在“制作”页第 4 步选择，并会记住上次的选择。</p>
              <Group label="画质">
                <Segmented<Simple['quality']> label="画质" value={s.quality} onChange={(v) => save({ quality: v })}
                  options={[{ value: 'standard', label: '标准（较快）' }, { value: 'high', label: '高' }]} />
              </Group>
            </>
          )}
        </CardBody>
      </Card>

      <UpdateCard enabled={settings.check_updates ?? true} />
    </div>
  );
}

/** The version, and whether a newer one is out (asked from GitHub; can be turned off). */
function UpdateCard({ enabled }: { enabled: boolean }) {
  const info = useUpdate((s) => s.info);
  const current = useApp((s) => s.info?.version);
  const [checking, setChecking] = useState(false);
  const checkNow = () => run(async () => {
    setChecking(true);
    try {
      const r = await loadUpdate(true);
      if (r.error && !r.latest) toast('warn', '无法检查新版本', '连不上 GitHub，稍后再试');
      else toast(r.newer ? 'info' : 'ok', r.newer ? `有新版本 ${r.latest}` : '已经是最新版本', r.newer ? updateHowTo(r) : undefined, r.newer ? 8000 : 3000);
    } finally {
      setChecking(false);
    }
  }, '检查失败');
  return (
    <Card>
      <CardHeader icon={<ArrowUpCircle className="size-4" />} title="更新" description={`当前版本 v${current ?? '…'}${info?.newer && info.latest ? ` · 最新 v${info.latest}` : ''}`} />
      <CardBody className="space-y-3">
        <Switch checked={enabled} onChange={(v) => void run(() => saveSettings({ check_updates: v }), '保存设置失败')}
          label="打开时检查新版本（只读取 GitHub 上最新版本的版本号）" />
        <div className="flex flex-wrap items-center gap-3">
          <Button size="sm" variant="secondary" loading={checking} onClick={() => void checkNow()}>立即检查</Button>
          {info?.newer && <span className="text-xs text-muted">{updateHowTo(info)}</span>}
        </div>
      </CardBody>
    </Card>
  );
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
      <CardHeader icon={<Subtitles className="size-4" />} title="字幕样式（第 4 步的“设置里的样式”）"
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
