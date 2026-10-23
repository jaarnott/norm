import { describe, expect, it } from 'vitest';
import { bandLabel, bandOf, bandTone } from './bands';

describe('reconciliation bands', () => {
  it('splits at $1 and $50 either side of zero', () => {
    expect(bandOf(0)).toBe('ok');
    expect(bandOf(99)).toBe('ok');
    expect(bandOf(-99)).toBe('ok');
    expect(bandOf(100)).toBe('near');
    expect(bandOf(4999)).toBe('near');
    expect(bandOf(-4999)).toBe('near');
    expect(bandOf(5000)).toBe('off');
    expect(bandOf(-123456)).toBe('off');
  });
  it('has no band without a difference', () => {
    expect(bandOf(null)).toBeNull();
    expect(bandOf(undefined)).toBeNull();
  });
  it('maps to a tone and plain words', () => {
    expect(bandTone('ok')).toBe('ok');
    expect(bandTone('near')).toBe('warn');
    expect(bandTone('off')).toBe('error');
    expect(bandTone(null)).toBe('neutral');
    expect(bandLabel('near')).toBe('Within $50');
    expect(bandLabel(null)).toBe('Not entered');
  });
});
