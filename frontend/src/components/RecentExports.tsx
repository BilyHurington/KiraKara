// Files in the project's exports folder (and outputs of this run's operations not listed yet),
// newest first: the 导出 page shows all kinds, the 卡拉OK字幕 page its videos.

import { ChevronDown, Download, History } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { fmtBytes, fmtRelative } from '@/lib/format';
import type { ExportFile } from '@/lib/types';
import { useFinishedJobs, useProject } from '@/store/app';
import { DownloadButton } from '@/components/DownloadButton';
import { Card, CardHeader } from '@/components/ui';

const KIND = (name: string) => (/-karaoke/.test(name) ? '带字幕的视频' : /\.wav$/i.test(name) ? '混音 WAV'
  : /\.mp4$/i.test(name) ? '降低人声的视频' : '导出文件');

export function RecentExports({ jobKinds, match, title = '最近导出', description, first = 10 }: {
  /** operations whose outputs belong here */
  jobKinds: string[];
  /** which files of the folder are shown */
  match: (filename: string) => boolean;
  title?: string;
  description?: string;
  /** shown at once; the rest behind 显示更多 (folded) */
  first?: number;
}) {
  const pid = useProject()?.id;
  const done = useFinishedJobs(jobKinds).filter((j) => j.output?.url && match(j.output.filename));
  const [files, setFiles] = useState<ExportFile[] | null>(null);
  const [all, setAll] = useState(false);
  const latest = done[0]?.id;
  useEffect(() => {
    if (!pid) return;
    let stop = false;
    api.get<ExportFile[]>(`/api/projects/${pid}/exports`)
      .then((f) => { if (!stop) setFiles(Array.isArray(f) ? f : []); }).catch(() => undefined);
    return () => { stop = true; };
  }, [pid, latest]);  // listed again when a new export finishes
  const listed = (files ?? []).filter((f) => match(f.filename));
  const fromJobs: ExportFile[] = done
    .filter((j) => !listed.some((f) => f.filename === j.output.filename))
    .map((j) => ({ filename: j.output.filename, url: j.output.url, size: -1, modified: j.finished ?? j.created }));
  const shown = [...fromJobs, ...listed];
  if (!shown.length) return null;
  const visible = all ? shown : shown.slice(0, first);
  return (
    <Card>
      <CardHeader icon={<History className="size-4" />} title={title} description={description} />
      <div className="p-2">
        {visible.map((f) => (
          <div key={f.filename} className="flex items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-surface-2">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2 text-[13px] font-medium">
                {KIND(f.filename)}<span className="min-w-0 truncate font-mono text-xs font-normal text-muted" title={f.filename}>{f.filename}</span>
              </div>
              <div className="truncate text-xs text-muted">{f.size >= 0 ? `${fmtRelative(f.modified)} · ${fmtBytes(f.size)}` : fmtRelative(f.modified)}</div>
            </div>
            <DownloadButton href={f.url} big filename={f.filename} size="xs" variant="outline" icon={<Download className="size-3.5" />}>下载</DownloadButton>
          </div>
        ))}
        {shown.length > first && (
          <button type="button" aria-expanded={all} onClick={() => setAll((v) => !v)}
            className="focus-ring flex w-full items-center justify-center gap-1.5 rounded-lg py-2 text-[13px] text-muted hover:bg-surface-2 hover:text-fg">
            <ChevronDown className={`size-4 transition ${all ? 'rotate-180' : ''}`} />
            {all ? '收起' : `显示更多（还有 ${shown.length - first} 个）`}
          </button>
        )}
      </div>
    </Card>
  );
}
