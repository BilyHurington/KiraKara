import { describe, expect, it } from 'vitest';
import type { Line } from './types';
import {
  assign, caretAt, caretTimeline, coversLine, effective, freeKey, keyIds, keyOf, mergeRanges, mixBackground, newSinger, newSingerColor, parseCombo,
  rangeSingers, SINGER_KEYS, SINGER_SWATCHES, union, withCombo, withKey, withNewSinger, wordRange, wordsOf, type LineSingers,
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

  it('keys: each singer and combination has its own, new ones take the first free one, any can be changed', () => {
    const empty = { members: [], mix: 'split' as const, direction: 'vertical' as const };
    const two = withNewSinger(withNewSinger(empty));
    expect(two.members.map((m) => m.key)).toEqual(['1', '2']);
    const { next, key } = withCombo(two, [1, 2]);
    expect(key).toBe('3');
    expect(keyIds(next, '3')).toEqual([1, 2]);
    expect(keyIds(next, '2')).toEqual([2]);
    expect(keyIds(next, '4')).toBeNull();
    expect(withCombo(next, [1, 2]).key).toBe('3');  // saved already
    const three = withNewSinger(next);
    expect(three.members[2].key).toBe('4');  // the combination keeps its key
    // a key given to another: they swap; '' leaves it without one
    const moved = withKey(three, { singer: 1 }, '3');
    expect(moved.swapped).toEqual({ combo: 0 });
    expect(moved.next.members[0].key).toBe('3');
    expect(moved.next.combos![0].key).toBe('1');
    const cleared = withKey(moved.next, { singer: 2 }, '').next;
    expect(cleared.members[1].key).toBe('');
    expect(freeKey(cleared)).toBe('2');  // the smallest free one, in the order 1–9, a–z
    // past 9 come the letters; l and p stay with the page (loop, listen)
    let many = empty as typeof three;
    for (let i = 0; i < 12; i++) many = withNewSinger(many);
    expect(many.members.map((m) => m.key).join('')).toBe('123456789abc');
    expect(SINGER_KEYS).not.toMatch(/[lp0]/);
    expect(keyOf('Q')).toBe('q');
    expect(keyOf('l')).toBe('');
    expect(keyOf('Enter')).toBe('');
  });

  it('any number of singers: colours past the swatches are all different', () => {
    const colors = new Set(Array.from({ length: 20 }, (_, i) => newSingerColor(SINGER_SWATCHES, 9 + i)));
    expect(colors.size).toBe(20);
    expect(newSingerColor(SINGER_SWATCHES, 9)).toMatch(/^#[0-9A-F]{6}$/);
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

