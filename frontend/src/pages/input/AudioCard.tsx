// Audio assets: original upload, stem import (with sync check report).

import { AlertTriangle, FileAudio, Film, Mic, Music2, Music4 } from 'lucide-react';
import { useState } from 'react';
import { api } from '@/lib/api';
import { fmtMs, ROLE_LABEL } from '@/lib/format';
import type { AudioAsset, ProjectView, Role } from '@/lib/types';
import { ppath, run, setPV, toast, useProject, useView } from '@/store/app';
import { Badge, Card, CardBody, CardHeader, DropZone, Tip } from '@/components/ui';

const AUDIO_ACCEPT = 'audio/*,.wav,.flac,.mp3,.m4a,.aac,.ogg,.opus,.aiff,.aif';
// the original may also be a video: its audio track is extracted and used
const MEDIA_ACCEPT = `${AUDIO_ACCEPT},video/*,.mp4,.mov,.m4v,.mkv,.webm,.avi,.flv,.ts,.mts,.m2ts,.wmv,.mpg,.mpeg,.3gp`;

const ROLE_ICON: Record<Role, typeof Music2> = { original: Music2, vocals: Mic, instrumental: Music4 };

const SOURCE_LABEL: Record<string, string> = { upload: '上传', separation: '人声分离', import: '导入分轨', mix: '混音' };

export function AudioCard() {
  const project = useProject()!;
  const view = useView()!;
  const [busy, setBusy] = useState<Role | null>(null);

  const upload = (role: Role, file: File) => run(async () => {
    setBusy(role);
    try {
      const fd = new FormData();
      fd.append('file', file, file.name);
      fd.append('role', role);
      const pv = await api.post<ProjectView>(ppath('/audio'), fd);
      setPV(pv);
      toast('ok', `已添加${ROLE_LABEL[role]}`, file.name);
    } finally {
      setBusy(null);
    }
  }, '上传音频失败');

  const asset = (role: Role) => project.audio.find((a) => a.role === role) ?? null;
  const original = asset('original');

  return (
    <Card>
      <CardHeader
        title="音频"
        icon={<FileAudio className="size-4" />}
        description="原曲用于对齐与试听；已有的人声 / 同源伴奏可作为分轨导入，导入时会检查与原曲的时间同步。音频不会被移动或剪切。"
      />
      <CardBody className="space-y-5">
        <AssetSlot
          role="original"
          asset={original}
          available={!!view.audio.original?.available}
          busy={busy === 'original'}
          onFile={(f) => upload('original', f)}
          emptyTitle="拖入或点击选择原曲（音频或视频）"
          emptyHint="wav / flac / mp3 / m4a 或 mp4 / mov / mkv 等视频（自动提取音轨）；时间以音轨起点为 0"
        />
        <div className="grid gap-4 md:grid-cols-2">
          {(['vocals', 'instrumental'] as Role[]).map((role) => (
            <AssetSlot
              key={role}
              role={role}
              asset={asset(role)}
              available={!!view.audio[role]?.available}
              busy={busy === role}
              onFile={(f) => upload(role, f)}
              emptyTitle={`导入已有${ROLE_LABEL[role]}分轨（可选）`}
              emptyHint={original ? '导入后自动检查同步；相同时长不代表同步' : '建议先上传原曲，以便检查同步'}
              compact
            />
          ))}
        </div>
      </CardBody>
    </Card>
  );
}

function AssetSlot({ role, asset, available, busy, onFile, emptyTitle, emptyHint, compact }: {
  role: Role; asset: AudioAsset | null; available: boolean; busy: boolean; onFile: (f: File) => void;
  emptyTitle: string; emptyHint: string; compact?: boolean;
}) {
  const Icon = ROLE_ICON[role];
  const accept = role === 'original' ? MEDIA_ACCEPT : AUDIO_ACCEPT;
  const video = useProject()?.video;
  if (!asset) {
    return (
      <div>
        <div className="mb-2 text-[13px] font-medium">{ROLE_LABEL[role]}</div>
        <DropZone accept={accept} onFile={onFile} title={emptyTitle} hint={emptyHint} compact={compact} busy={busy} />
      </div>
    );
  }
  const src = asset.source;
  return (
    <div className="rounded-xl border border-line bg-surface-2/40 p-4">
      <div className="flex items-start gap-3">
        <div className="grid size-10 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent">
          <Icon className="size-5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[13px] font-semibold">{ROLE_LABEL[role]}</span>
            <Badge tone="neutral">{SOURCE_LABEL[src.kind] ?? src.kind}</Badge>
            {!available && <Badge tone="danger" dot>文件缺失</Badge>}
          </div>
          <div className="mt-0.5 truncate text-[13px] text-fg" title={src.filename ?? ''}>{src.filename ?? '（未命名）'}</div>
          <div className="tabular mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted">
            <span>{fmtMs(asset.duration_ms)}</span>
            <span>{asset.sample_rate} Hz</span>
            <span>{asset.channels} 声道</span>
            <Tip content={asset.sha256}><span className="font-mono">sha256 {asset.sha256.slice(0, 12)}…</span></Tip>
            {src.model && <span>模型 {src.model}</span>}
          </div>
          {role === 'original' && video && video.audio_sha256 === asset.sha256 && (
            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-0.5 rounded-lg bg-info-soft px-2.5 py-1.5 text-xs text-fg">
              <Film className="size-3.5 text-info" />
              <span className="font-medium">来自视频 {video.filename}</span>
              <span className="tabular text-muted">
                {video.width}×{video.height}{video.fps ? ` · ${video.fps} fps` : ''} · {video.video_codec} · {fmtMs(video.duration_ms)}
              </span>
              <span className="text-muted">可在“导出”中生成降低人声的视频</span>
            </div>
          )}
          {src.notes?.length > 0 && (
            <ul className="mt-2 space-y-0.5 text-xs text-warn">
              {src.notes.map((n) => <li key={n} className="flex gap-1.5"><AlertTriangle className="mt-0.5 size-3 shrink-0" />{n}</li>)}
            </ul>
          )}
        </div>
      </div>
      {!available && (
        <div className="mt-3 rounded-lg bg-danger-soft px-3 py-2 text-xs text-fg">
          项目中找不到该音频文件（例如从不含音频的项目包导入）。请重新上传同一文件（sha256 {asset.sha256.slice(0, 12)}…）。
        </div>
      )}
      {asset.sync_report && <SyncReport report={asset.sync_report} />}
      <div className="mt-3">
        <DropZone
          accept={accept}
          onFile={onFile}
          compact
          busy={busy}
          title={available ? `替换${ROLE_LABEL[role]}` : `重新上传${ROLE_LABEL[role]}`}
          hint={role === 'original' ? '可选音频或视频；更换原曲后，基于旧原曲的分轨与结果会被标记' : undefined}
        />
      </div>
    </div>
  );
}

function SyncReport({ report }: { report: Record<string, any> }) {
  const ok = report.ok === true;
  const num = (v: unknown, d = 1) => (typeof v === 'number' ? v.toFixed(d) : '—');
  return (
    <div className="mt-3 rounded-lg border border-line bg-surface px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-medium">同步检查</span>
        <Badge tone={ok ? 'ok' : 'warn'} dot>{ok ? '与原曲同步' : '未能确认同步'}</Badge>
      </div>
      <div className="tabular mt-1.5 grid grid-cols-3 gap-2 text-xs">
        <Metric label="时间偏差" value={`${num(report.lag_ms)} ms`} />
        <Metric label="相关系数" value={num(report.correlation, 3)} />
        <Metric label="V+I 残差" value={typeof report.sum_residual_db === 'number' ? `${num(report.sum_residual_db)} dB` : '—'} />
      </div>
      {report.message && <div className="mt-1.5 text-xs text-muted">{report.message}</div>}
      {typeof report.length_diff_samples === 'number' && report.length_diff_samples !== 0 && (
        <div className="mt-1 text-xs text-warn">长度差 {report.length_diff_samples} 个采样（不会被拉伸）</div>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-surface-2/70 px-2 py-1.5">
      <div className="text-[11px] text-subtle">{label}</div>
      <div className="font-medium">{value}</div>
    </div>
  );
}
