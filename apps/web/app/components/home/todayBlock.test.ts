import { describe, expect, it } from 'vitest';
import { dueLabel, groupByVenue, isOverdue } from './TodayBlock';

const now = new Date(2026, 9, 24, 9); // Sat 24 Oct 2026, 09:00 local

describe('isOverdue', () => {
  it('reads Training\'s overdue tone', () => {
    expect(isOverdue({ due_at: '2026-10-20', tone: 'overdue' })).toBe(true);
    expect(isOverdue({ due_at: '2026-10-20', tone: 'due' })).toBe(false);
  });
  it('reads a provider\'s error tone the same way, dated or not', () => {
    expect(isOverdue({ due_at: '2026-10-24T08:00:00', tone: 'error' })).toBe(true);
    expect(isOverdue({ due_at: null, tone: 'error' })).toBe(true);
    expect(isOverdue({ due_at: '2026-10-24T08:00:00', tone: 'warn' })).toBe(false);
  });
});

describe('dueLabel', () => {
  it('says Overdue, Due today, or the day', () => {
    expect(dueLabel({ due_at: '2026-10-20', tone: 'overdue' }, now)).toBe('Overdue');
    expect(dueLabel({ due_at: '2026-10-24T08:00:00', tone: 'error' }, now)).toBe('Overdue');
    expect(dueLabel({ due_at: null, tone: 'error' }, now)).toBeNull();
    expect(dueLabel({ due_at: '2026-10-24', tone: 'due' }, now)).toBe('Due today');
    expect(dueLabel({ due_at: '2026-10-24T15:00:00', tone: 'warn' }, now)).toBe('Due today');
    expect(dueLabel({ due_at: '2026-10-26', tone: 'due' }, now)).toMatch(/^Due Mon/);
    expect(dueLabel({ due_at: null, tone: 'due' }, now)).toBeNull();
    expect(dueLabel({ due_at: 'not a date', tone: 'due' }, now)).toBeNull();
  });
});

describe('groupByVenue', () => {
  const item = (title: string, venue_id: string | null = null) => ({ kind: 'fcp', title, venue_id });
  it('keeps one venue\'s items in one unnamed group', () => {
    const items = [item('Fridge 1', 'v1'), item('Fridge 2', 'v1'), item('Training')];
    expect(groupByVenue(items)).toEqual([{ venueId: null, items }]);
  });
  it('groups a manager\'s items by venue, in first-seen order, unplaced items in their own group', () => {
    const items = [item('A at Lott', 'lott'), item('Training'), item('B at Bowl', 'bowl'), item('C at Lott', 'lott')];
    expect(groupByVenue(items)).toEqual([
      { venueId: 'lott', items: [items[0], items[3]] },
      { venueId: null, items: [items[1]] },
      { venueId: 'bowl', items: [items[2]] },
    ]);
  });
  it('is empty for no items', () => {
    expect(groupByVenue([])).toEqual([{ venueId: null, items: [] }]);
  });
});
