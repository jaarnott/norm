import { describe, expect, it } from 'vitest';
import { bpLabel, clockLabel, num, pct, personName, plural } from './format';

describe('finance format', () => {
  it('reads percentages, basis points and counts', () => {
    expect(pct(0.289)).toBe('28.9%');
    expect(pct(null)).toBe('—');
    expect(bpLabel(1200)).toBe('12%');
    expect(bpLabel(1250)).toBe('12.5%');
    expect(num(1234)).toBe('1,234');
    expect(num('812.5', 1)).toBe('812.5');
  });
  it('shows a clock-in in the venue zone, and says when it is unfinished', () => {
    expect(clockLabel('2026-10-17T05:30:00Z', '2026-10-17T10:05:00Z', 'Pacific/Auckland')).toBe('Sat 17 Oct, 6:30 pm – 11:05 pm');
    expect(clockLabel('2026-10-17T05:30:00Z', null, 'Pacific/Auckland')).toBe('Sat 17 Oct, 6:30 pm – (still clocked in)');
    expect(clockLabel(null, null)).toBe('—');
  });
  it('turns "Last, First" around and counts nouns', () => {
    expect(personName('Smith, Jane')).toBe('Jane Smith');
    expect(personName('Madonna')).toBe('Madonna');
    expect(plural(1, 'shift')).toBe('1 shift');
    expect(plural(3, 'person', 'people')).toBe('3 people');
  });
});
