import { describe, expect, it } from 'vitest';
import type { Line } from './types';
import {
  assign, caretAt, caretTimeline, coversLine, effective, freeKey, keyIds, mergeRanges, mixBackground, newSinger, parseCombo, rangeSingers, union,
  withCombo, withNewSinger, wordRange, wordsOf, type LineSingers,
} from './singers';

const ls = (text: string, singers: number[] = [], spans: LineSingers['spans'] = []): LineSingers => ({ line_id: 'L', text, singers, spans });
const line = (id: string, surfaces: string[]): Line => ({
  id, text: surfaces.join(''), kind: 'lyric', sing: true, imported_start_ms: null, imported_end_ms: null, anchor: null,
  translation: null, romanization: null, voice: 'main', confirmed: false,
  source: { origin: 'paste', raw_index: null, merged_from: [], split_from: null, tag_index: 0 },
  segments: surfaces.map((s, i) => ({ id: `${id}s${i}`, surface: s, reading: null, lang: 'ja', units: [], reading_source: 'rule', confirmed: false, uncertain: false, candidates: [], note: '' })),
});

describe('assigning singers', () => {
  it('a whole line sets its own singers and clears its parts', () => {
    const out = assign(ls('あいう', [1], [{ start: 0, end: 1, singers: [2] }]), [[0, 3]], [2, 3]);
    expect(out.singers).toEqual([2, 3]);
    expect(out.spans).toEqual([]);
  });

  it('part of a line becomes a span; clearing it goes back to the line', () => {
    let out = assign(ls('あいうえ', [1]), [[1, 3]], [1, 2]);
    expect(out.spans).toEqual([{ start: 1, end: 3, singers: [1, 2] }]);
    expect(effective(out).map((x) => x.join('+'))).toEqual(['1', '1+2', '1+2', '1']);
    out = assign(out, [[2, 3]], []);
    expect(out.spans).toEqual([{ start: 1, end: 2, singers: [1, 2] }]);
    // what the line says anyway needs no span
    expect(assign(ls('あい', [1]), [[0, 1]], [1]).spans).toEqual([]);
  });

  it('majority of a range, ranges and combos', () => {
    expect(rangeSingers([[1], [2], [2]], 0, 3)).toEqual([2]);
    expect(rangeSingers([[1], [2]], 0, 2)).toEqual([1]);
    expect(mergeRanges([[3, 5], [0, 2], [2, 3]])).toEqual([[0, 5]]);
    expect(coversLine([[0, 2], [2, 4]], 'abcd')).toBe(true);
    expect(parseCombo('1+2＋2, 9 x', 3)).toEqual([1, 2]);
    expect(parseCombo('1 3', 2)).toEqual([1]);
  });

  it('word ranges run across lines in either direction', () => {
    const rows = [line('A', ['君', 'と', '歩いた']), line('B', ['夜空', 'に'])].map((l) => ({ line: l, words: wordsOf(l) }));
    const sel = wordRange(rows, [1, 0], [0, 1]);
    expect(sel.get('A')).toEqual([[1, 5]]);
    expect(sel.get('B')).toEqual([[0, 2]]);
    expect(union(sel, new Map([['A', [[0, 1]]]])).get('A')).toEqual([[0, 5]]);
  });

  it('new singers get unused colours; mixes draw like the subtitles', () => {
    const a = newSinger([]);
    expect(newSinger([a]).color).not.toBe(a.color);
    expect(mixBackground(['#f00', '#00f'], 'split', 'vertical')).toBe('linear-gradient(to bottom, #f00 0% 50%, #00f 50% 100%)');
    expect(mixBackground(['#f00', '#00f'], 'gradient', 'horizontal')).toBe('linear-gradient(to right, #f00, #00f)');
  });

  it('number keys: singers first, then saved combinations; a new singer moves a combination off its key', () => {
    const two = { members: [newSinger([]), newSinger([newSinger([])])], mix: 'split' as const, direction: 'vertical' as const };
    const { next, key } = withCombo(two, [1, 2]);
    expect(key).toBe(3);
    expect(keyIds(next, 3)).toEqual([1, 2]);
    expect(keyIds(next, 2)).toEqual([2]);
    expect(keyIds(next, 4)).toBeNull();
    expect(withCombo(next, [1, 2]).key).toBe(3);  // saved already
    const three = withNewSinger(next);
    expect(keyIds(three, 3)).toEqual([3]);
    expect(three.combos).toEqual([{ key: 4, singers: [1, 2] }]);
    expect(freeKey(three)).toBe(5);
  });

  it('blanks are nobody\'s: they join a part around them, else stay with the line', () => {
    expect(assign(ls('あい うえ', [1]), [[0, 5]], [1, 2]).spans).toEqual([]);  // (whole line)
    expect(assign(ls('あい うえお', [1]), [[0, 5]], [1, 2]).spans).toEqual([{ start: 0, end: 5, singers: [1, 2] }]);
    expect(assign(ls('あい うえ', []), [[3, 5]], [2]).spans).toEqual([{ start: 3, end: 5, singers: [2] }]);
    expect(assign(ls('あい うえ', [1]), [[2, 3]], [2]).spans).toEqual([]);  // a space alone is never a part
    expect(rangeSingers([[], [2], [2]], 0, 3, ' bb')).toEqual([2]);
  });

  it('the playhead: unit times to places in the text', () => {
    const a = line('A', ['君', 'と']);
    a.segments[0].units = [{ id: 'u1', reading: 'き', surface: '', flags: [] }, { id: 'u2', reading: 'み', surface: '', flags: [] }] as any;
    a.segments[1].units = [{ id: 'u3', reading: 'と', surface: 'と', flags: [] }] as any;
    const b = line('B', ['夜']);
    b.segments[0].units = [{ id: 'u4', reading: 'よ', surface: '', flags: [] }] as any;
    const steps = caretTimeline([a, b], new Map([['u1', [1000, 1200]], ['u2', [1200, 1400]], ['u3', [1500, 1600]], ['u4', [5000, 5400]]]));
    expect(steps.map((s) => [s.c0, s.c1])).toEqual([[0, 0.5], [0.5, 1], [1, 2], [0, 1]]);
    expect(caretAt(steps, 500)).toEqual({ lineId: 'A', pos: 0, waiting: true });
    expect(caretAt(steps, 1100)).toEqual({ lineId: 'A', pos: 0.25, waiting: false });
    expect(caretAt(steps, 1450)).toEqual({ lineId: 'A', pos: 1, waiting: false });  // between units: stays
    expect(caretAt(steps, 2000)).toEqual({ lineId: 'A', pos: 2, waiting: false });  // just after the line
    expect(caretAt(steps, 4000)).toEqual({ lineId: 'B', pos: 0, waiting: true });  // waits at the next line
  });

  it('a selection never starts or ends on a space', () => {
    const rows = [line('A', ['君', ' ', 'for', ' '])].map((l) => ({ line: l, words: wordsOf(l) }));
    expect(wordRange(rows, [0, 0], [0, 3]).get('A')).toEqual([[0, 5]]);
    expect(wordRange(rows, [0, 1], [0, 1]).get('A')).toBeUndefined();
  });
});

