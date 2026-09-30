// "有新版本" next to the version: how to update (the portable package's updater, or git pull).

import { ArrowUpCircle } from 'lucide-react';
import { cn } from '@/lib/format';
import { useUpdate } from '@/store/update';
import { Tip } from '@/components/ui';

export function updateHowTo(info: { portable?: boolean; updater?: string | null }) {
  return info.portable
    ? `关闭 MiliKara 后，双击文件夹里的 ${info.updater ?? '更新.bat'} 即可更新：只下载有变化的部分，项目和设置都会保留。`
    : '在项目目录运行 git pull，再重新执行一次安装命令。';
}

export function UpdateBadge({ className }: { className?: string }) {
  const info = useUpdate((s) => s.info);
  if (!info?.newer || !info.latest) return null;
  return (
    <Tip content={<span className="block max-w-64">{updateHowTo(info)}（点击查看更新内容）</span>}>
      <a href={info.url} target="_blank" rel="noreferrer"
        className={cn('focus-ring inline-flex items-center gap-1 rounded-md bg-accent-soft px-1.5 py-0.5 text-[11px] font-medium text-accent hover:underline', className)}>
        <ArrowUpCircle className="size-3.5" />有新版本 {info.latest}
      </a>
    </Tip>
  );
}
