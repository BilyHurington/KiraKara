// Who sings which part of the lyrics (多人演唱): pure helpers for the 演唱者 page.
//
// A line has its own singers (`singers`) and parts sung by others (`singer_spans`, character
// ranges of its text) — the same model and normalisation as kara_align.lyrics.singers.  A
// selection is a set of character ranges per line; assigning replaces what the selected
// characters had (a whole line: the line's own singers, its parts cleared).

import type { KaraokeSinger, KaraokeSingers, Line, SingerCombo, SingerSpan } from './types';

export const MAX_SINGERS = 9;
/** A new singer's colour: the first of these no other singer has (as the server picks them). */
export const SINGER_SWATCHES = ['#ED35B3', '#2F80ED', '#F5C400', '#3CC46A', '#FF8A1E', '#8B5CF6', '#1FB5C9', '#FF4D6D', '#8A8A8A'];

/** The assignment of one line as the server stores it (`text`: what the spans were made for). */
export interface LineSingers { line_id: string; text: string; singers: number[]; spans: SingerSpan[] }

export type Ranges = [number, number][];
/** Selected character ranges per line id. */
export type Selection = Map<string, Ranges>;

export const idsKey = (ids: readonly number[]) => ids.join('+');

export function lineSingers(l: Line): LineSingers {
  return {
    line_id: l.id, text: l.text, singers: [...(l.singers ?? [])],
    spans: (l.singer_spans ?? []).map((s) => ({ start: s.start, end: s.end, singers: [...s.singers] })),
  };
}

export function sameSingers(a: LineSingers, b: LineSingers) {
  return idsKey(a.singers) === idsKey(b.singers) && a.spans.length === b.spans.length
    && a.spans.every((s, i) => s.start === b.spans[i].start && s.end === b.spans[i].end && idsKey(s.singers) === idsKey(b.spans[i].singers));
}

export function cleanIds(ids: readonly number[]): number[] {
  const out: number[] = [];
  for (const n of ids) if (Number.isInteger(n) && n >= 1 && n <= MAX_SINGERS && !out.includes(n)) out.push(n);
  return out;
}

/** The singers of every character. */
export function effective(ls: LineSingers): number[][] {
  const out: number[][] = Array.from({ length: ls.text.length }, () => ls.singers);
  for (const sp of ls.spans) for (let i = Math.max(0, sp.start); i < Math.min(out.length, sp.end); i++) out[i] = sp.singers;
  return fillBlanks(ls.text, out, ls.singers);
}

const isBlank = (ch: string) => /\s/.test(ch);

/** Blanks (spaces, full-width spaces) are nobody's: a run of them takes the singers on both sides when
 * those agree (it never splits a part), else the line's own (as kara_align.lyrics.singers.fill_blanks). */
export function fillBlanks(text: string, chars: number[][], own: number[]): number[][] {
  const out = [...chars];
  let i = 0;
  while (i < out.length) {
    if (!isBlank(text[i] ?? '')) { i++; continue; }
    let j = i;
    while (j < out.length && isBlank(text[j] ?? '')) j++;
    const left = i > 0 ? out[i - 1] : null;
    const right = j < out.length ? out[j] : null;
    const fill = left && right && idsKey(left) === idsKey(right) ? left : own;
    for (let x = i; x < j; x++) out[x] = fill;
    i = j;
  }
  return out;
}

/** Spans from per-character singers: joined, and none where the line's own singers apply. */
function fromChars(ls: LineSingers, chars: number[][]): LineSingers {
  chars = fillBlanks(ls.text, chars, ls.singers);
  const own = idsKey(ls.singers);
  const spans: SingerSpan[] = [];
  chars.forEach((ids, i) => {
    if (idsKey(ids) === own) return;
    const last = spans[spans.length - 1];
    if (last && last.end === i && idsKey(last.singers) === idsKey(ids)) last.end = i + 1;
    else spans.push({ start: i, end: i + 1, singers: [...ids] });
  });
  return { ...ls, singers: cleanIds(ls.singers), spans };
}

/** The singers most characters of [a, b) have (ties: the first character's); with `text`, blanks count
 * only when the whole range is blank. */
export function rangeSingers(chars: number[][], a: number, b: number, text?: string): number[] {
  let part = chars.slice(Math.max(0, a), Math.max(a, b));
  if (text) {
    const inked = part.filter((_, i) => !isBlank(text[Math.max(0, a) + i] ?? ''));
    if (inked.length) part = inked;
  }
  if (!part.length) return [];
  const count = new Map<string, number>();
  for (const ids of part) count.set(idsKey(ids), (count.get(idsKey(ids)) ?? 0) + 1);
  const best = Math.max(...count.values());
  return part.find((ids) => count.get(idsKey(ids)) === best)!;
}

/** Merge overlapping / touching ranges. */
export function mergeRanges(r: Ranges): Ranges {
  const s = [...r].filter(([a, b]) => b > a).sort((x, y) => x[0] - y[0]);
  const out: Ranges = [];
  for (const [a, b] of s) {
    const last = out[out.length - 1];
    if (last && a <= last[1]) last[1] = Math.max(last[1], b);
    else out.push([a, b]);
  }
  return out;
}

export function coversLine(ranges: Ranges, text: string) {
  const m = mergeRanges(ranges);
  return m.length === 1 && m[0][0] <= 0 && m[0][1] >= text.length;
}

/** `ids` sing the selected characters of a line ([] = back to the line's own singers; a whole line
 * selected: its own singers, every part cleared). */
export function assign(ls: LineSingers, ranges: Ranges, ids: number[]): LineSingers {
  ids = cleanIds(ids);
  if (coversLine(ranges, ls.text)) return { ...ls, singers: ids, spans: [] };
  const chars = effective(ls);
  for (const [a, b] of mergeRanges(ranges)) {
    for (let i = Math.max(0, a); i < Math.min(chars.length, b); i++) chars[i] = ids.length ? ids : ls.singers;
  }
  return fromChars(ls, chars);
}

/** Singers `1+2`, `1 2`, `1，2` … → [1, 2] (only numbers of existing singers; empty when none). */
export function parseCombo(text: string, count: number): number[] {
  const ids = text.split(/[\s+＋,，、/／]+/).map((x) => Number(x.trim())).filter((n) => Number.isInteger(n) && n >= 1 && n <= count);
  return cleanIds(ids);
}

/** Words of a line (its segments, which spell out its text), with their character ranges. */
export interface Word { start: number; end: number; text: string }
export function wordsOf(l: Line): Word[] {
  const out: Word[] = [];
  let pos = 0;
  for (const s of l.segments) {
    out.push({ start: pos, end: pos + s.surface.length, text: s.surface });
    pos += s.surface.length;
  }
  if (pos !== l.text.length || !out.length) return l.text ? [{ start: 0, end: l.text.length, text: l.text }] : [];
  return out;
}

/** Every word from one (line, word) position to another, in lyric order (either way round). */
export function wordRange(lines: { line: Line; words: Word[] }[], a: [number, number], b: [number, number]): Selection {
  const [from, to] = a[0] < b[0] || (a[0] === b[0] && a[1] <= b[1]) ? [a, b] : [b, a];
  const sel: Selection = new Map();
  for (let li = from[0]; li <= to[0]; li++) {
    const { line, words } = lines[li];
    if (!words.length) continue;
    let w0 = li === from[0] ? from[1] : 0;
    let w1 = li === to[0] ? to[1] : words.length - 1;
    // spaces at either end are not part of a selection
    while (w0 <= w1 && !words[w0].text.trim()) w0++;
    while (w1 >= w0 && !words[w1].text.trim()) w1--;
    if (w1 < w0) continue;
    sel.set(line.id, [[words[w0].start, words[w1].end]]);
  }
  return sel;
}

export function union(a: Selection, b: Selection): Selection {
  const out: Selection = new Map(a);
  for (const [id, r] of b) out.set(id, mergeRanges([...(out.get(id) ?? []), ...r]));
  return out;
}

export function isSelected(sel: Selection, lineId: string, a: number, b: number) {
  return (sel.get(lineId) ?? []).some(([x, y]) => x < b && a < y);
}

/** Lines (and parts) each singer sings: how many lines mention it. */
export function usage(lines: Line[], n: number) {
  return lines.filter((l) => (l.singers ?? []).includes(n) || (l.singer_spans ?? []).some((s) => s.singers.includes(n))).length;
}

export function newSinger(members: KaraokeSinger[]): KaraokeSinger {
  const used = new Set(members.map((m) => m.color.toUpperCase()));
  const color = SINGER_SWATCHES.find((c) => !used.has(c)) ?? SINGER_SWATCHES[members.length % SINGER_SWATCHES.length];
  return { name: '', color, color_unsung: '', color_sung: '', outline_color: '', glow_unsung: '', glow_sung: '' };
}

export const singerLabel = (members: KaraokeSinger[], n: number) => members[n - 1]?.name?.trim() || `演唱者 ${n}`;

/** CSS background drawing parts sung together the way the subtitles do (for text with background-clip: text). */
export function mixBackground(colors: string[], mix: 'split' | 'gradient', direction: 'vertical' | 'horizontal') {
  const dir = direction === 'vertical' ? 'to bottom' : 'to right';
  if (colors.length === 1) return colors[0];
  if (mix === 'gradient') return `linear-gradient(${dir}, ${colors.join(', ')})`;
  const stops = colors.map((c, i) => `${c} ${(i / colors.length) * 100}% ${((i + 1) / colors.length) * 100}%`);
  return `linear-gradient(${dir}, ${stops.join(', ')})`;
}

// ------------------------------------------------------------------ number keys

/** What number key `n` assigns: singer n, or a saved combination on that key (null: nothing). */
export function keyIds(sg: KaraokeSingers, n: number): number[] | null {
  if (n >= 1 && n <= sg.members.length) return [n];
  return sg.combos?.find((c) => c.key === n)?.singers ?? null;
}

/** The first number key neither a singer nor a combination has (null: all nine are taken). */
export function freeKey(sg: KaraokeSingers, skip: number[] = []): number | null {
  const used = new Set([...(sg.combos ?? []).map((c) => c.key), ...skip]);
  for (let k = sg.members.length + 1; k <= MAX_SINGERS; k++) if (!used.has(k)) return k;
  return null;
}

/** Keys left for new singers or combinations. */
export const keysLeft = (sg: KaraokeSingers) => MAX_SINGERS - sg.members.length - (sg.combos?.length ?? 0);

/** A new singer takes the next number, and so the next key: a combination on that key moves to a free one. */
export function withNewSinger(sg: KaraokeSingers): KaraokeSingers {
  const n = sg.members.length + 1;
  const next: KaraokeSingers = { ...sg, members: [...sg.members, newSinger(sg.members)] };
  const combos = [...(sg.combos ?? [])];
  const i = combos.findIndex((c) => c.key === n);
  if (i >= 0) {
    const k = freeKey(next, [n]);
    if (k === null) combos.splice(i, 1);
    else combos[i] = { ...combos[i], key: k };
  }
  return { ...next, combos };
}

/** Save singers sung together on the first free key (the same combination again: its key). */
export function withCombo(sg: KaraokeSingers, ids: number[]): { next: KaraokeSingers; key: number | null } {
  const same = sg.combos?.find((c) => idsKey(c.singers) === idsKey(ids));
  if (same) return { next: sg, key: same.key };
  const key = freeKey(sg);
  if (key === null || ids.length < 2) return { next: sg, key: null };
  const combos: SingerCombo[] = [...(sg.combos ?? []), { key, singers: ids }].sort((a, b) => a.key - b.key);
  return { next: { ...sg, combos }, key };
}

// ------------------------------------------------------------------ the playhead on the lyrics

/** When each sung unit is heard and where it is in its line's text (character positions, fractional
 * where several units share characters, e.g. 桜 = さ く ら). Sorted by time. */
export interface CaretStep { lineId: string; start: number; end: number; c0: number; c1: number }

export function caretTimeline(lines: Line[], times: Map<string, [number, number]>): CaretStep[] {
  const out: CaretStep[] = [];
  for (const l of lines) {
    let pos = 0;
    for (const seg of l.segments) {
      const a = pos;
      const b = pos + seg.surface.length;
      pos = b;
      const units = seg.units;
      if (!units.length) continue;
      const own = units.every((u) => u.surface) && units.map((u) => u.surface).join('') === seg.surface;
      let at = a;
      units.forEach((u, k) => {
        const c0 = own ? at : a + ((b - a) * k) / units.length;
        const c1 = own ? at + (u.surface?.length ?? 0) : a + ((b - a) * (k + 1)) / units.length;
        at = c1;
        const t = times.get(u.id);
        if (t) out.push({ lineId: l.id, start: t[0], end: t[1], c0, c1 });
      });
    }
  }
  return out.sort((x, y) => x.start - y.start);
}

/** Where the caret is at `t`: inside the unit being sung (moving with it), after the last one of a line
 * while that line pauses, or waiting before the next line (`waiting`). null: before the first line. */
export function caretAt(steps: CaretStep[], t: number): { lineId: string; pos: number; waiting: boolean } | null {
  if (!steps.length) return null;
  let lo = 0;
  let hi = steps.length - 1;
  if (t < steps[0].start) return { lineId: steps[0].lineId, pos: steps[0].c0, waiting: true };
  while (lo < hi) {  // the last step starting at or before t
    const mid = (lo + hi + 1) >> 1;
    if (steps[mid].start <= t) lo = mid; else hi = mid - 1;
  }
  const s = steps[lo];
  if (t < s.end) return { lineId: s.lineId, pos: s.c0 + (s.c1 - s.c0) * ((t - s.start) / Math.max(1, s.end - s.start)), waiting: false };
  const next = steps[lo + 1];
  if (next && next.lineId === s.lineId) return { lineId: s.lineId, pos: s.c1, waiting: false };
  // after a line: stays at its end a moment, then waits at the start of the next line
  if (!next || t - s.end < 1200) return { lineId: s.lineId, pos: s.c1, waiting: !next };
  return { lineId: next.lineId, pos: next.c0, waiting: true };
}
