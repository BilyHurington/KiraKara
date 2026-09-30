// Per-step status shown in the sidebar navigator.

import type { ProjectView } from '@/lib/types';
import type { Step } from './app';

export type StepState = 'done' | 'todo' | 'optional' | 'skipped' | 'attention' | 'running';

export interface StepStatus { state: StepState; note?: string }

export function stepStatus(step: Step, pv: ProjectView | null, running: Set<string>): StepStatus {
  if (!pv) return { state: step === 'mode' ? 'todo' : 'todo' };
  const p = pv.project;
  const hasLyrics = p.lyrics.lines.some((l) => l.kind === 'lyric');
  const hasAudio = p.audio.some((a) => a.role === 'original');
  const active = p.results.find((r) => r.id === p.active_result_id);
  const activeSum = pv.view.results.find((r) => r.id === p.active_result_id);
  switch (step) {
    case 'mode':
      return { state: 'done', note: p.mode === 'lrc' ? 'LRC 增强' : '普通模式' };
    case 'input':
      if (hasLyrics && hasAudio) return { state: 'done', note: `${p.lyrics.lines.filter((l) => l.sing && l.kind === 'lyric').length} 行` };
      return { state: 'todo', note: !hasLyrics && !hasAudio ? '需要歌词和音频' : !hasLyrics ? '需要歌词' : '需要音频' };
    case 'enhance': {
      if (running.has('separate')) return { state: 'running', note: '分离中' };
      const uncertain = p.lyrics.lines.reduce((n, l) => n + l.segments.filter((s) => s.uncertain && !s.confirmed).length, 0);
      if (uncertain > 0) return { state: 'optional', note: `${uncertain} 处读音待确认` };
      if (pv.view.audio.vocals?.outdated || pv.view.audio.instrumental?.outdated) {
        return { state: 'attention', note: '分轨来自更换前的原曲，需重新分离' };
      }
      const vocals = !!pv.view.audio.vocals?.available;
      return { state: vocals ? 'done' : 'optional', note: vocals ? '已有人声分轨' : '可选' };
    }
    case 'calibrate':
      if (p.mode !== 'lrc') return { state: 'skipped', note: '普通模式不需要' };
      if (pv.view.calibration_issues.some((i) => i.severity === 'error')) return { state: 'attention', note: '锚点需修正' };
      return p.calibration.confirmed
        ? { state: 'done', note: `平移 ${p.calibration.user_shift_ms > 0 ? '+' : ''}${p.calibration.user_shift_ms} ms` }
        : { state: 'todo', note: '未确认' };
    case 'align':
      if (running.has('align')) return { state: 'running', note: '对齐中' };
      if (!active) return { state: 'todo' };
      return activeSum?.stale ? { state: 'attention', note: '结果已过期' } : { state: 'done', note: `${p.results.length} 个结果` };
    case 'review': {
      if (!active) return { state: 'todo' };
      const n = active.issues.filter((i) => i.severity !== 'info').length;
      const failed = active.units.filter((u) => u.status !== 'ok' && !u.manual).length;
      if (failed) return { state: 'attention', note: `${failed} 个单元无时间` };
      if (n) return { state: 'attention', note: `${n} 条提示` };
      return { state: 'done' };
    }
    case 'singers': {
      const n = p.karaoke?.singers?.members.length ?? 0;
      const lines = p.lyrics.lines.filter((l) => l.kind === 'lyric' && l.sing && ((l.singers?.length ?? 0) || (l.singer_spans?.length ?? 0))).length;
      if (!n && pv.view.singer_markers) return { state: 'optional', note: `${pv.view.singer_markers} 行写着演唱者，可自动识别` };
      if (!n) return { state: 'optional', note: '可选 · 多人演唱时分色' };
      return { state: lines ? 'done' : 'optional', note: `${n} 位演唱者 · ${lines} 行` };
    }
    case 'karaoke':
      if (running.has('burn')) return { state: 'running', note: '生成视频中' };
      return active ? { state: 'optional', note: p.video ? '字幕 · 生成带字幕的视频' : '字幕 · 纯黑背景视频' } : { state: 'todo', note: '需要对齐结果' };
    case 'export':
      return { state: active ? 'todo' : 'todo', note: active ? '可导出' : undefined };
  }
}
