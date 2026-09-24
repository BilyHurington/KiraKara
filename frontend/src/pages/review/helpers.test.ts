import { describe, expect, it } from 'vitest';
import { flagHelp, flagLabel } from './helpers';

describe('review flag labels', () => {
  it('names the interlude flags and tail flags instead of showing raw codes', () => {
    expect(flagLabel('line_gap')).toBe('行内长停顿');
    expect(flagLabel('in_rest')).toBe('人声无声处');
    expect(flagLabel('tail_adjusted')).toBe('尾音已修正');
    expect(flagHelp('in_rest')).toContain('间奏');
    expect(flagLabel('something_new')).toBe('something_new');
  });
});
