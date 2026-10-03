import { describe, it, expect } from 'vitest';
import { approveParams, cardLabel, declineParams, groupRows, rowState, type ApprovalRow } from './approvalCard';

function row(id: string, title?: string, extra: Partial<ApprovalRow> = {}): ApprovalRow {
  return {
    id,
    action: 'manage_stock_item',
    connector_name: 'loadedhub',
    summary: `Update ${id}`,
    preview: title ? { title, changes: [] } : null,
    ...extra,
  };
}

describe('grouping', () => {
  it('groups rows by kind of change, in the order proposed', () => {
    const groups = groupRows([
      row('a', 'Update a stock item'),
      row('b', 'Email a report'),
      row('c', 'Update a stock item'),
    ]);
    expect(groups.map(g => [g.title, g.rows.map(r => r.id)])).toEqual([
      ['Update a stock item', ['a', 'c']],
      ['Email a report', ['b']],
    ]);
  });

  it('falls back to the summary for a row without a preview', () => {
    expect(groupRows([row('a')])[0].title).toBe('Update a');
  });
});

describe('what a decision sends', () => {
  const rows = [row('a'), row('b'), row('c')];

  it('approves everything without a list when every row is ticked', () => {
    expect(approveParams('t', rows, new Set(['a', 'b', 'c']))).toEqual({ thread_id: 't' });
  });

  it('names the ticked rows when some are left out', () => {
    expect(approveParams('t', rows, new Set(['c', 'a']))).toEqual({
      thread_id: 't',
      tool_call_ids: ['a', 'c'],
    });
  });

  it('sends a decline reason only when one was given', () => {
    expect(declineParams('t', '  ')).toEqual({ thread_id: 't' });
    expect(declineParams('t', ' wrong venue ')).toEqual({ thread_id: 't', notes: 'wrong venue' });
  });
});

describe('row and card state', () => {
  it('shows nothing on a row that is still waiting', () => {
    expect(rowState(row('a'), 'pending')).toBeNull();
  });

  it('shows the outcome once an approved row has run', () => {
    expect(rowState(row('a', undefined, { status: 'approved', outcome: 'done' }), 'approved')?.label).toBe('Done');
    expect(rowState(row('a', undefined, { status: 'approved', outcome: 'changed' }), 'approved')?.tone).toBe('warn');
    expect(rowState(row('a', undefined, { status: 'rejected' }), 'approved')?.label).toBe('Declined');
  });

  it('reads cards decided before rows had their own status', () => {
    expect(rowState(row('a'), 'approved')?.label).toBe('Approved');
    expect(rowState(row('a'), 'rejected')?.label).toBe('Declined');
  });

  it('says a card was partly approved', () => {
    const rows = [row('a', undefined, { status: 'approved' }), row('b', undefined, { status: 'rejected' })];
    expect(cardLabel('approved', rows)).toBe('Partly approved');
    expect(cardLabel('approved', [rows[0]])).toBe('Approved');
    expect(cardLabel('pending', rows)).toBe('Approval needed');
  });
});
