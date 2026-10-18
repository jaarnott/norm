import { describe, expect, it } from 'vitest';
import { fromField, howItPicks, promptId, shown, toField, when } from './format';
import type { SettingRow } from './types';

describe('suggestion settings fields', () => {
  it('shows a fraction as a percent and reads it back', () => {
    expect(toField('percent', 0.25)).toBe('25');
    expect(fromField('percent', '12.5')).toBe(0.125);
    expect(fromField('count', ' 3 ')).toBe(3);
    expect(fromField('count', 'lots')).toBeNull();
    expect(shown('percent', 0.08)).toBe('8%');
    expect(shown('dollars', 1500)).toBe('$1,500');
  });

  it('says when a prompt shows', () => {
    expect(when({ days: [0, 1], part: null, month_start: false })).toBe('Mon, Tue');
    expect(when({ days: null, part: 'morning', month_start: true })).toBe('mornings · first week of the month');
    expect(when({ days: null, part: null, month_start: false })).toBe('any time');
  });

  it('names a new prompt from its words, never reusing an id', () => {
    const taken = new Set(['reports.covers_last_week']);
    expect(promptId('reports', 'Covers last week at {venue}?', taken)).toBe('reports.covers_last_week_at');
    expect(promptId('reports', 'Covers last week', taken)).toBe('reports.covers_last_week_2');
  });

  it('describes the picking from the live settings', () => {
    const s = (name: string, value: unknown) => ({ name, value } as SettingRow);
    const lines = howItPicks([
      s('insights_enabled', true), s('insight_slots_desktop', 2), s('insight_slots_phone', 1), s('area_diversity', true),
      s('pending_slot', false), s('venue_boost', 1.5), s('regular_boost', 1.6), s('discover_boost', 1.4),
      s('timely_bonus', 0.4), s('repeat_days', 7), s('repeat_move', 0.25), s('dismiss_days', 30),
    ]);
    expect(lines[0]).toContain('up to 2 on a desktop and 1 on a phone');
    expect(lines[1]).not.toContain('waiting-on-you');
    expect(lines[3]).toContain('25%');
  });
});
