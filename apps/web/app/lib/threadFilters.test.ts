import { describe, it, expect } from 'vitest';
import { matchesQuery } from './threadFilters';

const thread = {
  title: 'Weekly sales report',
  message: 'How did Bessie & Royals trade last week?',
  apps: [{ name: 'Loaded Reports' }],
  automated_task: { title: 'Monday trading summary' },
};

describe('matchesQuery', () => {
  it('matches the title, first prompt, App names and task title, in any case', () => {
    expect(matchesQuery(thread, 'sales REPORT')).toBe(true);
    expect(matchesQuery(thread, 'bessie')).toBe(true);
    expect(matchesQuery(thread, 'loaded reports')).toBe(true);
    expect(matchesQuery(thread, 'monday')).toBe(true);
  });

  it('trims the query, and an empty one matches everything', () => {
    expect(matchesQuery(thread, '  royals  ')).toBe(true);
    expect(matchesQuery(thread, '')).toBe(true);
    expect(matchesQuery(thread, '   ')).toBe(true);
  });

  it('rejects a query in none of the fields', () => {
    expect(matchesQuery(thread, 'roster')).toBe(false);
  });

  it('copes with a thread that has no title, Apps or task', () => {
    const bare = { title: null, message: 'Order more limes', automated_task: null };
    expect(matchesQuery(bare, 'limes')).toBe(true);
    expect(matchesQuery(bare, 'report')).toBe(false);
  });
});
