// 极简模式首页：选模式 → 拖入视频 → 粘贴链接或歌词 → 字幕样式 → 开始；下方是任务队列。
// 字幕样式和视频设置在添加任务时绑定到任务上（排队中的任务不受之后修改影响），
// 并自动记住，下一首从同样的选择开始。

import {
  AlertTriangle, ArrowRight, Check, CircleDashed, Crosshair, Download, Film, Hand, Link2, ListMusic, Loader2, Play,
  RotateCcw, Settings2, Sparkles, Trash2, X,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { cn, fmtRelative } from '@/lib/format';
import type { Mode, PipelineStage, PipelineTask, TaskStyleOptions } from '@/lib/types';
import { run, toast } from '@/store/app';
import {
  addTask, forgetOwnTask, hasActiveTasks, markOwnTask, openInDetail, saveSettings, setSimplePage, taskAction, useSimple,
} from '@/store/simple';
import { Badge, Button, Card, CardBody, CardHeader, DropZone, EmptyState, Input, Progress, Segmented, Textarea } from '@/components/ui';
import { MEDIA_ACCEPT } from '@/pages/input/AudioCard';
import { CalibrateDialog } from './CalibrateDialog';
import { TaskStyleStep } from './TaskStyleStep';

const PROVIDER_LABEL = { none: '', claude: 'Claude Code', codex: 'Codex', openai: 'API' } as const;

/** What the pasted text looks like (mirrors the server's link detection). */
export function detectLyrics(text: string): { kind: 'empty' | 'link' | 'lrc' | 'text'; label: string } {
  const t = text.trim();
  if (!t) return { kind: 'empty', label: '' };
  const lines = t.split('\n').filter((l) => l.trim());
  const url = /https?:\/\/\S+/i.test(t) || /^\s*(netease|ncm|163|wyy|qq|qqmusic)\s*[:：]/i.test(t);
  const timed = lines.filter((l) => /^\s*\[\d+:\d+/.test(l)).length;
  if (url && lines.length <= 3 && !timed) {
    const where = /163|netease|ncm|wyy/i.test(t) ? '网易云音乐' : /qq/i.test(t) ? 'QQ 音乐' : '音乐';
    return { kind: 'link', label: `${where}链接 · 会自动获取歌词` };
  }
  if (timed > 0) return { kind: 'lrc', label: `LRC 歌词 · ${timed} 行带时间` };
  return { kind: 'text', label: `纯文本歌词 · ${lines.length} 行（没有时间）` };
}

export function SimpleHome() {
  const settings = useSimple((s) => s.settings);
  const tasks = useSimple((s) => s.tasks);
  const [mode, setMode] = useState<Mode>(settings?.simple.default_mode ?? 'lrc');
  const [file, setFile] = useState<File | null>(null);
  const [lyrics, setLyrics] = useState('');
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [styleOpts, setStyleOpts] = useState<TaskStyleOptions | null>(settings?.simple.task_style ?? null);
  const styleDirty = useRef(false);
  const [calibrating, setCalibrating] = useState<string | null>(null);
  const own = useSimple((s) => s.ownTasks);
  const detected = useMemo(() => detectLyrics(lyrics), [lyrics]);
  const active = hasActiveTasks(tasks);

  useEffect(() => {
    if (settings) setMode(settings.simple.default_mode);
  }, [settings?.simple.default_mode]); // eslint-disable-line react-hooks/exhaustive-deps

  // the subtitle choices start from the last ones used, and are saved as they change
  useEffect(() => {
    if (settings && !styleOpts) setStyleOpts(settings.simple.task_style);
  }, [settings]); // eslint-disable-line react-hooks/exhaustive-deps
  const pendingStyle = useRef<TaskStyleOptions | null>(null);
  useEffect(() => {
    if (!styleOpts || !styleDirty.current) return;
    pendingStyle.current = styleOpts;
    const t = setTimeout(() => {
      pendingStyle.current = null;
      void saveSettings({ simple: { task_style: styleOpts } }).catch(() => undefined);
    }, 500);
    return () => clearTimeout(t);
  }, [styleOpts]);
  useEffect(() => () => {  // leaving the page (e.g. to the detailed mode): save what is still pending now
    const p = pendingStyle.current;
    if (p) void saveSettings({ simple: { task_style: p } }).catch(() => undefined);
  }, []);
  const changeStyle = (next: TaskStyleOptions) => { styleDirty.current = true; setStyleOpts(next); };

  useEffect(() => {
    if (calibrating) return;
    const ready = tasks.find((t) => t.status === 'waiting' && own.includes(t.id));
    if (ready) {
      forgetOwnTask(ready.id);
      setCalibrating(ready.id);
    }
  }, [tasks, calibrating, own]);
  const calTask = tasks.find((t) => t.id === calibrating && t.status === 'waiting' && t.calibration);

  const chooseMode = (m: Mode) => {
    setMode(m);
    void run(() => saveSettings({ simple: { default_mode: m } }));
  };

  const start = () => run(async () => {
    if (!file) return;
    setBusy(true);
    try {
      const t = await addTask(file, lyrics, mode, name, styleOpts ?? undefined);
      markOwnTask(t.id);
      toast('ok', '已开始', mode === 'lrc' ? '读取视频和歌词后请确认开头位置，之后全部自动完成' : active ? '前面的任务完成后自动继续' : '马上开始');
      setFile(null);
      setLyrics('');
      setName('');
    } finally {
      setBusy(false);
    }
  }, '无法开始');

  const s = settings?.simple;
  const ai = settings?.ai.provider && settings.ai.provider !== 'none' && s?.ai_readings ? PROVIDER_LABEL[settings.ai.provider] : null;
  const summary = s ? [
    ai ? `AI 注音：${ai}` : 'AI 注音：关',
    `人声分离：${s.separate ? '开' : '关'}`,
    s.auto_export ? '完成后生成视频' : '不自动生成视频',
  ] : [];

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader icon={<Sparkles className="size-4" />} title="做一首卡拉OK" description="放入视频和歌词，其余全部自动完成：注音、人声分离、对齐、生成带字幕的视频。" />
        <CardBody className="space-y-6">
          <StepBlock n={1} title="模式">
            <Segmented<Mode> value={mode} onChange={chooseMode} options={[
              { value: 'lrc', label: 'LRC 增强（推荐）', title: '使用歌词里的行时间，长前奏和重复副歌更稳' },
              { value: 'plain', label: '普通', title: '只用歌词文字' },
            ]} />
            <p className="mt-1.5 text-xs text-muted">
              {mode === 'lrc'
                ? '使用歌词里的行时间定位每一行。开始后几秒内会请你确认第一句从哪里开始唱（视频和歌词常常差几百毫秒），之后全部自动完成；歌词没有时间会自动改用普通模式。'
                : '只用歌词文字，不需要时间，开始后全部自动完成。'}
            </p>
          </StepBlock>

          <StepBlock n={2} title="视频或音频">
            {file ? (
              <div className="flex items-center gap-3 rounded-xl border border-line bg-surface-2/50 px-3 py-2.5">
                <Film className="size-5 shrink-0 text-accent" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px] font-medium">{file.name}</div>
                  <div className="text-xs text-muted">{(file.size / 1024 / 1024).toFixed(1)} MB</div>
                </div>
                <Button size="xs" variant="ghost" icon={<X className="size-3.5" />} onClick={() => setFile(null)}>换一个</Button>
              </div>
            ) : (
              <DropZone accept={MEDIA_ACCEPT} onFile={setFile} title="拖入视频（或音频），也可以点击选择"
                hint="MP4 / MOV / MKV / MP3 / FLAC …；视频会保留画面，字幕直接烧录上去" />
            )}
          </StepBlock>

          <StepBlock n={3} title="歌词">
            <Textarea value={lyrics} onChange={(e) => setLyrics(e.target.value)} className="h-32"
              aria-label="音乐链接或歌词"
              placeholder={'粘贴网易云音乐 / QQ 音乐的歌曲链接（或分享文字），\n或者直接粘贴 LRC / 纯文本歌词'} />
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {detected.kind !== 'empty' && (
                <Badge tone={detected.kind === 'link' ? 'accent' : detected.kind === 'lrc' ? 'ok' : 'neutral'}>
                  {detected.kind === 'link' ? <Link2 className="mr-1 inline size-3" /> : <ListMusic className="mr-1 inline size-3" />}
                  {detected.label}
                </Badge>
              )}
              <Input value={name} onChange={(e) => setName(e.target.value)} className="h-8 max-w-64 flex-1 text-[13px]"
                placeholder="歌曲名（可选，链接会自动识别）" />
            </div>
          </StepBlock>

          {s && styleOpts && (
            <StepBlock n={4} title="字幕与视频">
              <TaskStyleStep value={styleOpts} onChange={changeStyle} settings={s} />
            </StepBlock>
          )}

          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-5">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
              {summary.map((x) => <span key={x}>{x}</span>)}
              <button className="focus-ring flex items-center gap-1 rounded text-accent hover:underline" onClick={() => setSimplePage('settings')}>
                <Settings2 className="size-3.5" />更改设置
              </button>
            </div>
            <Button variant="primary" size="lg" icon={<Play className="size-4" />} loading={busy}
              disabled={!file || detected.kind === 'empty' || (styleOpts?.source === 'saved' && !styleOpts.saved_id)} onClick={start}>
              开始制作
            </Button>
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader icon={<ListMusic className="size-4" />} title="任务" description="按顺序逐个处理；完成的任务点开可以进入详细模式继续调整。" />
        <CardBody className="p-0">
          {tasks.length === 0 ? (
            <EmptyState className="m-4 py-10" title="还没有任务" description="上面放入视频和歌词，点“开始制作”" />
          ) : (
            <ul className="divide-y divide-line">
              {tasks.map((t) => <TaskRow key={t.id} task={t} onCalibrate={() => setCalibrating(t.id)}
                ahead={tasks.filter((x) => (x.status === 'queued' || x.status === 'running') && x.created < t.created).length} />)}
            </ul>
          )}
        </CardBody>
      </Card>
      {calTask && <CalibrateDialog key={calTask.calibration!.line_id} task={calTask} onClose={() => setCalibrating(null)} />}
    </div>
  );
}

function StepBlock({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <section className="flex gap-4">
      <span className="grid size-7 shrink-0 place-items-center rounded-full bg-accent-soft text-xs font-semibold text-accent">{n}</span>
      <div className="min-w-0 flex-1">
        <h3 className="mb-2 text-sm font-semibold">{title}</h3>
        {children}
      </div>
    </section>
  );
}

const STATUS: Record<PipelineTask['status'], { label: string; tone: 'accent' | 'ok' | 'danger' | 'neutral' | 'warn' }> = {
  preparing: { label: '读取中', tone: 'accent' },
  waiting: { label: '等待确认', tone: 'warn' },
  queued: { label: '排队中', tone: 'neutral' },
  running: { label: '进行中', tone: 'accent' },
  succeeded: { label: '完成', tone: 'ok' },
  failed: { label: '失败', tone: 'danger' },
  cancelled: { label: '已取消', tone: 'neutral' },
  interrupted: { label: '已中断', tone: 'warn' },
};

function TaskRow({ task: t, ahead, onCalibrate }: { task: PipelineTask; ahead: number; onCalibrate: () => void }) {
  const st = STATUS[t.status];
  const live = t.status === 'running' || t.status === 'queued' || t.status === 'preparing' || t.status === 'waiting';
  const canOpen = !!t.project_id && !t.project_deleted && t.status !== 'running' && t.status !== 'preparing' && t.status !== 'waiting';
  const act = (a: 'cancel' | 'retry' | 'delete') => run(() => taskAction(t.id, a), '操作失败');
  const [confirmDel, setConfirmDel] = useState(false);
  const open = () => t.project_id && openInDetail(t.project_id, t.outputs.video ? 'karaoke' : 'review');
  return (
    <li className="px-5 py-4">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            {canOpen ? (
              <button className="focus-ring truncate rounded text-left text-[14px] font-semibold hover:text-accent" onClick={open}
                title="在详细模式中打开">
                {t.name || t.media_filename}
              </button>
            ) : <span className="truncate text-[14px] font-semibold">{t.name || t.media_filename}</span>}
            <Badge tone={st.tone} dot>{st.label}</Badge>
            <Badge tone={t.mode === 'lrc' ? 'accent' : 'neutral'}>{t.mode === 'lrc' ? 'LRC' : '普通'}</Badge>
            {t.style_label && (
              <span className="flex items-center gap-1 rounded-full border border-line px-2 py-0.5 text-[11px] text-muted" title="这个任务的字幕样式">
                {(t.style_colors ?? []).map((c) => <span key={c} className="size-2.5 rounded-full ring-1 ring-line-strong" style={{ background: c }} />)}
                {t.style_label}
              </span>
            )}
          </div>
          <div className="mt-0.5 truncate text-xs text-muted">
            {t.media_filename} · {t.lyrics_kind === 'link' ? '音乐链接' : '粘贴的歌词'} · {fmtRelative(t.created)}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {t.status === 'succeeded' && t.outputs.video && (
            <a href={t.outputs.video.url} download>
              <Button size="sm" variant="primary" icon={<Download className="size-4" />}>下载视频</Button>
            </a>
          )}
          {t.project_deleted && <Badge tone="neutral">项目已删除</Badge>}
          {!t.project_deleted && (t.status === 'failed' || t.status === 'cancelled' || t.status === 'interrupted') && (
            <Button size="sm" variant="secondary" icon={<RotateCcw className="size-4" />} onClick={() => act('retry')}
              title="从没完成的步骤继续，并再试一次出错时跳过的 AI 注音 / 人声分离。已有的分轨和对齐结果会保留；若 AI 注音这次改了读音，会重新对齐（锁定的手动时间保留），也会再次使用 AI 额度">重试</Button>
          )}
          {canOpen && <Button size="sm" variant="ghost" icon={<ArrowRight className="size-4" />} onClick={open}>详细模式</Button>}
          {live && <Button size="sm" variant="ghost" icon={<X className="size-4" />} onClick={() => act('cancel')}>取消</Button>}
          {!live && (confirmDel ? (
            <span className="flex items-center gap-1.5 rounded-lg bg-surface-2 px-2 py-1 text-xs text-muted">
              从列表移除？{t.project_id ? '项目和视频仍保留在详细模式' : ''}
              <Button size="xs" variant="danger" onClick={() => act('delete')}>移除</Button>
              <Button size="xs" variant="ghost" onClick={() => setConfirmDel(false)}>取消</Button>
            </span>
          ) : <Button size="sm" variant="ghost" icon={<Trash2 className="size-4" />} aria-label="移除任务" onClick={() => setConfirmDel(true)} />)}
        </div>
      </div>

      {t.status === 'waiting' && t.calibration && (
        <div className="mt-3 flex flex-wrap items-center gap-3 rounded-xl border border-warn/40 bg-warn-soft px-3 py-2.5">
          <Hand className="size-4 text-warn" />
          <span className="min-w-0 flex-1 text-[13px]">需要你确认第一句「{t.calibration.line_text}」从哪里开始唱，之后全部自动完成</span>
          <Button size="sm" variant="primary" icon={<Crosshair className="size-4" />} onClick={onCalibrate}>确认开头位置</Button>
        </div>
      )}
      {t.status === 'queued' && !t.stages.some((s) => s.status === 'done') ? (
        <div className="mt-2 text-xs text-muted">{ahead ? `前面还有 ${ahead} 个任务` : '即将开始'}</div>
      ) : (
        <>
          {t.status === 'queued' && <div className="mt-2 text-xs text-muted">{ahead ? `已确认，排队中（前面还有 ${ahead} 个任务）` : '即将继续'}</div>}
          {t.status === 'running' && (
            <div className="mt-3 flex items-center gap-3">
              <Progress value={t.progress} className="flex-1" />
              <span className="tabular w-10 text-right text-xs text-muted">{Math.round(t.progress * 100)}%</span>
            </div>
          )}
          <ol className="mt-3 flex flex-wrap gap-1.5" aria-label="处理步骤">
            {t.stages.map((s) => <StageChip key={s.key} s={s} />)}
          </ol>
          {(t.status === 'running' || t.status === 'preparing') && t.message && <div className="mt-2 text-xs text-muted">{t.message}</div>}
        </>
      )}
      {t.error && <div className="mt-2 rounded-lg bg-danger-soft px-3 py-2 text-xs break-words text-danger">{t.error}</div>}
      {t.warnings.length > 0 && (
        <ul className="mt-2 space-y-0.5">
          {t.warnings.map((w) => (
            <li key={w} className="flex items-start gap-1.5 text-xs text-warn"><AlertTriangle className="mt-0.5 size-3.5 shrink-0" />{w}</li>
          ))}
        </ul>
      )}
    </li>
  );
}

function StageChip({ s }: { s: PipelineStage }) {
  const icon = {
    done: <Check className="size-3" strokeWidth={3} />,
    running: <Loader2 className="size-3 animate-spin" />,
    failed: <X className="size-3" strokeWidth={3} />,
    skipped: <CircleDashed className="size-3" />,
    waiting: <Hand className="size-3" />,
    pending: null,
  }[s.status];
  const tip = s.status === 'running' ? `${Math.round(s.progress * 100)}% ${s.message}` : s.message || (s.status === 'skipped' ? '已跳过' : '');
  return (
    <li title={tip}
      className={cn('flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium',
        { done: 'border-ok/40 bg-ok-soft text-ok', running: 'border-accent/50 bg-accent-soft text-accent',
          failed: 'border-danger/40 bg-danger-soft text-danger', skipped: 'border-dashed border-line-strong text-subtle',
          waiting: 'border-warn/50 bg-warn-soft text-warn',
          pending: 'border-line text-subtle' }[s.status])}>
      {icon}{s.label}
      {s.status === 'done' && s.message && <span className="font-normal opacity-80">· {s.message}</span>}
    </li>
  );
}
