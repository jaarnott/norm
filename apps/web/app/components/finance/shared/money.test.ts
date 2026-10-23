import { describe, expect, it } from 'vitest';
import { centsToInput, formatCents, formatHours, formatSignedCents, parseCents } from './money';

describe('formatCents', () => {
  it('formats integer cents in the organisation currency', () => {
    expect(formatCents(123456)).toBe('$1,234.56');
    expect(formatCents(123456, 'NZD', 0)).toBe('$1,235');
    expect(formatCents(-1200)).toBe('-$12.00');
  });
  it('shows an em dash for nothing', () => {
    expect(formatCents(null)).toBe('—');
    expect(formatCents(undefined)).toBe('—');
    expect(formatCents(Number.NaN)).toBe('—');
  });
  it('falls back to the code for a currency the browser lacks', () => {
    expect(formatCents(100, 'XXX')).toContain('1.00');
  });
});

describe('formatSignedCents', () => {
  it('puts the sign in front', () => {
    expect(formatSignedCents(1500)).toBe('+$15.00');
    expect(formatSignedCents(-1500)).toBe('-$15.00');
    expect(formatSignedCents(0)).toBe('$0.00');
  });
});

describe('parseCents / centsToInput', () => {
  it('round-trips a typed amount', () => {
    expect(parseCents('12.50')).toBe(1250);
    expect(parseCents('$1,234')).toBe(123400);
    expect(parseCents('-3')).toBe(-300);
    expect(parseCents('')).toBeNull();
    expect(parseCents('abc')).toBeNull();
    expect(centsToInput(1250)).toBe('12.50');
    expect(centsToInput(null)).toBe('');
  });
});

describe('formatHours', () => {
  it('reads decimals and strings', () => {
    expect(formatHours(6.48)).toBe('6.48 h');
    expect(formatHours('812.5')).toBe('812.50 h');
    expect(formatHours(null)).toBe('—');
  });
});
