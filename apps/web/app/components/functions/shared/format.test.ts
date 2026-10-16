import { describe, expect, it } from 'vitest';
import { dateLabel, money, parseMoney, timeLabel, timeRangeLabel } from './format';
import { qs } from './api';

describe('functions format', () => {
  it('formats cents in the company currency', () => {
    expect(money(123456, 'NZD')).toBe('$1,234.56');
    expect(money(123456, 'NZD', 0)).toBe('$1,235');
    expect(money(null)).toBe('—');
  });

  it('parses typed money into cents', () => {
    expect(parseMoney('$1,250.50')).toBe(125050);
    expect(parseMoney('')).toBeNull();
  });

  it('never shifts plain dates through the browser time zone', () => {
    expect(dateLabel('2026-10-18')).toBe('Sun 18 Oct 2026');
    expect(dateLabel('2026-04-05', { weekday: false, year: false })).toBe('5 Apr');
  });

  it('formats wall-clock times', () => {
    expect(timeLabel('18:30:00')).toBe('6:30 pm');
    expect(timeLabel('00:15')).toBe('12:15 am');
    expect(timeRangeLabel('20:00', '01:00', true)).toBe('8:00 pm – 1:00 am (next day)');
  });

  it('builds query strings, repeating array keys and dropping blanks', () => {
    expect(qs({ stage: ['new', 'replied'], q: '', venue: 'v1' })).toBe('?stage=new&stage=replied&venue=v1');
    expect(qs({})).toBe('');
  });
});
