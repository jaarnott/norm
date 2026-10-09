import { describe, it, expect } from 'vitest';
import { COMPLETED_STATUSES, applyStatusFilter, awaitsApproval, matchesQuery } from './threadFilters';

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

describe('COMPLETED_STATUSES', () => {
  it('counts a chat the tool loop finished as completed', () => {
    expect(COMPLETED_STATUSES.has('completed')).toBe(true);
    expect(COMPLETED_STATUSES.has('submitted')).toBe(true);
    expect(COMPLETED_STATUSES.has('rejected')).toBe(true);
  });

  it('leaves out approved — an order whose send failed is still retryable', () => {
    expect(COMPLETED_STATUSES.has('approved')).toBe(false);
  });
});

describe('applyStatusFilter', () => {
  const threads = [
    { id: 'done', status: 'completed' },
    { id: 'sent', status: 'submitted' },
    { id: 'approved', status: 'approved' },
    { id: 'tool', status: 'awaiting_tool_approval' },
    { id: 'run', status: 'completed', automated_task: { waiting_for_approval: 2 } },
    { id: 'ask', status: 'needs_clarification' },
    { id: 'input', status: 'awaiting_user_input' },
  ];
  const ids = (f: Parameters<typeof applyStatusFilter>[1]) => applyStatusFilter(threads, f).map(t => t.id);

  it('keeps every thread under All', () => {
    expect(ids('all')).toHaveLength(threads.length);
  });

  it('lists finished chats under Completed, unless a scheduled run is waiting', () => {
    expect(ids('completed')).toEqual(['done', 'sent']);
  });

  it('lists proposed changes and waiting scheduled runs under Awaiting approval', () => {
    expect(ids('awaiting_approval')).toEqual(['tool', 'run']);
    expect(awaitsApproval({ status: 'completed', automated_task: null })).toBe(false);
  });

  it('lists questions for the user under Needs input', () => {
    expect(ids('awaiting_user_input')).toEqual(['ask', 'input']);
  });
});
