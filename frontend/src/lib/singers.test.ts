import { describe, expect, it } from 'vitest';
import type { Line } from './types';
import {
  assign, coversLine, effective, freeKey, keyIds, mergeRanges, mixBackground, newSinger, parseCombo, rangeSingers, union,
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
});

