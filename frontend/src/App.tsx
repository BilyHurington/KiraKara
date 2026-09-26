import { useEffect } from 'react';
import { player } from '@/audio/player';
import { loadInfo, loadProjects, openProject, run, useApp } from '@/store/app';
import { redo, undo } from '@/store/edits';
import { Sidebar } from '@/components/shell/Sidebar';
import { StudioDock } from '@/components/shell/StudioDock';
import { ErrorBoundary } from '@/components/shell/ErrorBoundary';
import { Toaster } from '@/components/shell/Toaster';
import { Topbar } from '@/components/shell/Topbar';
import { TooltipProvider } from '@/components/ui';
import { HomePage } from '@/pages/Home';
import { ModePage } from '@/pages/Mode';
import { InputPage } from '@/pages/Input';
import { EnhancePage } from '@/pages/Enhance';
import { CalibratePage } from '@/pages/Calibrate';
import { AlignPage } from '@/pages/Align';
import { ReviewPage } from '@/pages/Review';
import { KaraokePage } from '@/pages/Karaoke';
import { ExportPage } from '@/pages/Export';
import { SimpleApp } from '@/pages/simple/SimpleApp';
import { setUi, startTaskPolling, taskOnProject, useSimple } from '@/store/simple';
import { Button, Callout } from '@/components/ui';

const PAGES = {
  mode: ModePage,
  input: InputPage,
  enhance: EnhancePage,
  calibrate: CalibratePage,
  align: AlignPage,
  review: ReviewPage,
  karaoke: KaraokePage,
  export: ExportPage,
};

export default function App() {
  const pid = useApp((s) => s.pid);
  const step = useApp((s) => s.step);
  const ui = useSimple((s) => s.ui);

  // the simple mode's queue is watched in both modes (notifications, open project refresh)
  useEffect(() => { startTaskPolling(); }, []);

  useEffect(() => {
    void run(async () => {
      await Promise.all([loadInfo(), loadProjects()]);
      let last: string | null = null;
      try { last = localStorage.getItem('kara.pid'); } catch { /* ignore */ }
      if (last && useApp.getState().projects.some((p) => p.id === last)) await openProject(last);
    }, '无法连接本地服务');
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (useSimple.getState().ui === 'simple') return;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        void (e.shiftKey ? redo() : undo());
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.code === 'Space') {
        e.preventDefault();
        player.toggle();
      } else if (e.key.toLowerCase() === 'l') {
        player.toggleLoop();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const Page = pid ? PAGES[step] : HomePage;

  if (ui === 'simple') {
    return (
      <TooltipProvider>
        <SimpleApp />
        <Toaster />
      </TooltipProvider>
    );
  }

  return (
    <TooltipProvider>
      <div className="flex h-full overflow-hidden">
        <Sidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <Topbar />
          <main className="min-h-0 flex-1 overflow-y-auto">
            <div key={`${pid}-${step}`} className="mx-auto max-w-6xl animate-slide-up px-6 py-8 xl:px-10">
              <ErrorBoundary resetKey={`${pid}-${step}`}>
                <TaskBusyBanner />
                <Page />
              </ErrorBoundary>
            </div>
          </main>
          <ErrorBoundary resetKey={pid ?? ''}>
            <StudioDock />
          </ErrorBoundary>
        </div>
      </div>
      <Toaster />
    </TooltipProvider>
  );
}

/** A simple-mode task is still working on the open project: say so, and what waits for it. */
export function TaskBusyBanner() {
  const pid = useApp((s) => s.pid);
  const task = taskOnProject(useSimple((s) => s.tasks), pid);
  if (!task) return null;
  const doing = { waiting: '等待确认开头位置', running: task.message || '处理中', preparing: '读取视频和歌词', queued: '排队中' }[
    task.status as 'waiting' | 'running' | 'preparing' | 'queued'] ?? '处理中';
  return (
    <Callout tone="warn" className="mb-6" title={`极简模式任务「${task.name || task.media_filename}」正在处理这个项目（${doing}）`}
      actions={<Button size="sm" variant="secondary" onClick={() => setUi('simple')}>查看任务队列</Button>}>
      完成前这里不能对齐、人声分离、AI 注音或烧录视频；任务每完成一步，这里会自动更新。
    </Callout>
  );
}
