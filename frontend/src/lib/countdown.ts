// Countdown dots before a line (karaoke subtitles): which lines get them, the same rules as
// kara_align.karaoke.ass.plan_countdowns (the first line, and a line after a long pause).

import type { KaraokeCountdown } from './types';

export const COUNTDOWN_DEFAULTS: KaraokeCountdown = { intro: true, interlude: true, min_gap_ms: 6000, dots: 3 };

export interface TimedLine { id: string; start: number; end: number; countdown?: boolean | null }

/** For each line (by id): whether the style's rules give it a countdown (`auto`) and whether it has one
 * in the end (`on`: its own setting, else `auto`). */
export function countdownPlan(lines: TimedLine[], cd: KaraokeCountdown = COUNTDOWN_DEFAULTS) {
  const out = new Map<string, { auto: boolean; on: boolean; gap: number | null }>();
  let sungTo: number | null = null;
  for (const l of [...lines].sort((a, b) => a.start - b.start)) {
    const gap = sungTo === null ? null : l.start - sungTo;
    const auto = gap === null ? cd.intro : cd.interlude && gap >= cd.min_gap_ms;
    out.set(l.id, { auto, on: l.countdown ?? auto, gap });
    sungTo = sungTo === null ? l.end : Math.max(sungTo, l.end);
  }
  return out;
}
