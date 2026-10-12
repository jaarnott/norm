import { describe, it, expect } from 'vitest';
import {
  buildReviewList, fixAccepted, needWords,
  type ReviewIssue, type ReviewSuggestion,
} from './invoiceReview';

const issue = (over: Partial<ReviewIssue>): ReviewIssue => ({
  id: over.id ?? `${over.code}:${over.line_id ?? ''}`,
  code: 'x', blocking: true, message: 'm', ...over,
});
const sugg = (over: Partial<ReviewSuggestion>): ReviewSuggestion => ({
  id: over.id ?? `${over.kind ?? 'line_value'}:${over.field ?? ''}:${over.line_id ?? ''}`,
  kind: 'line_value', explanation: 'e', ...over,
});

function list(issues: ReviewIssue[], suggestions: ReviewSuggestion[], lineOrder = ['a', 'b'], decided: string[] = []) {
  return buildReviewList({
    issues, suggestions, lineOrder,
    issueOpen: (i) => !decided.includes(i.id),
    suggestionPending: (s) => !decided.includes(s.id),
  });
}

describe('one decision, one row', () => {
  it('a blocker is never a row of its own when a suggestion fixes it', () => {
    const unit = sugg({ field: 'unit', line_id: 'a' });
    const { rows } = list(
      [issue({ code: 'unit_missing', line_id: 'a', fix: [unit.id] })],
      [unit, sugg({ field: 'unit', line_id: 'b' })],
    );
    expect(rows).toHaveLength(2);
    expect(rows[0].fixes.map((s) => s.id)).toEqual([unit.id]);
    expect(rows[0].issue?.code).toBe('unit_missing'); // carried for its gate
    expect(rows[1].issue).toBeUndefined();
  });

  it('puts every fix of a blocker on one row (totals)', () => {
    const a = sugg({ field: 'unit_cost', line_id: 'a', fixes: 'totals_inconsistent' });
    const b = sugg({ field: 'unit_cost', line_id: 'b', fixes: 'totals_inconsistent' });
    const { rows } = list(
      [issue({ id: 'totals_inconsistent', code: 'totals_inconsistent', fix: [a.id, b.id] })],
      [a, b],
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].fixes).toHaveLength(2);
    expect(rows[0].key).toBe('totals_inconsistent');
  });

  it('an added line arrives with its unit and item — one Accept', () => {
    // Aitkens 173670: the freight line arrived unitless and its blocker
    // demanded a decision already made.
    const add = sugg({ kind: 'add_line', line_id: 'rep-3' });
    const unit = sugg({ field: 'unit', line_id: 'rep-3' });
    const item = sugg({ kind: 'create_item', field: 'linked_item_id', line_id: 'rep-3' });
    const { rows } = list(
      [issue({ code: 'unit_missing', line_id: 'rep-3', fix: [unit.id] })],
      [add, unit, item],
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].fixes.map((s) => s.kind)).toEqual(['add_line', 'line_value', 'create_item']);
    expect(rows[0].needs).toEqual([]);
  });

  it("names what an added line will still need that nothing could answer", () => {
    const { rows } = list(
      [issue({ code: 'item_unmatched', line_id: 'rep-3' })],
      [sugg({ kind: 'add_line', line_id: 'rep-3' })],
    );
    expect(needWords(rows[0].needs ?? [])).toEqual(['a stock item']);
  });

  it('once the line is added its decisions are rows of their own', () => {
    const unit = sugg({ field: 'unit', line_id: 'rep-3' });
    const { rows } = list(
      [issue({ code: 'unit_missing', line_id: 'rep-3', fix: [unit.id] })],
      [sugg({ kind: 'add_line', line_id: 'rep-3' }), unit],
      ['a', 'rep-3'],
      ['add_line::rep-3'],
    );
    expect(rows.find((r) => r.fixes[0]?.id === unit.id)?.open).toBe(true);
  });
});

describe('the list', () => {
  it('a blocker nothing can fix is its own row', () => {
    const { rows } = list([issue({ code: 'ambiguous_pairing', line_id: 'a' })], []);
    expect(rows).toHaveLength(1);
    expect(rows[0].fixes).toEqual([]);
  });

  it('a note with nothing to decide is not a row', () => {
    const { rows, notes } = list([issue({ code: 'pairing_arbitrated', blocking: false })], []);
    expect(rows).toEqual([]);
    expect(notes.map((n) => n.code)).toEqual(['pairing_arbitrated']);
  });

  it('follows the invoice: header first, then each line unit → item → brand; decided last', () => {
    const { rows } = list(
      [],
      [
        sugg({ kind: 'create_brand', field: 'linked_brand_id', line_id: 'a' }),
        sugg({ field: 'linked_item_id', line_id: 'a' }),
        sugg({ field: 'unit', line_id: 'a' }),
        sugg({ kind: 'supplier', field: 'linked_supplier_id' }),
        sugg({ field: 'linked_item_id', line_id: 'b' }),
      ],
      ['a', 'b'],
      ['line_value:linked_item_id:b'],
    );
    expect(rows.map((r) => r.key)).toEqual([
      'supplier:linked_supplier_id:',
      'line_value:unit:a',
      'line_value:linked_item_id:a',
      'create_brand:linked_brand_id:a',
      'line_value:linked_item_id:b',
    ]);
    expect(rows.at(-1)?.open).toBe(false);
  });

  it('names the open delete row', () => {
    const del = sugg({ id: 'delete_invoice', kind: 'delete_invoice' });
    const { deleteRow } = list(
      [issue({ id: 'not_an_invoice', code: 'not_an_invoice', fix: [del.id] })],
      [del],
    );
    expect(deleteRow?.fixes[0].kind).toBe('delete_invoice');
  });
});

describe('a blocker stands down when its fix is accepted', () => {
  it('needs every fix, not some', () => {
    const totals = issue({ id: 't', fix: ['a', 'b'] });
    expect(fixAccepted(totals, (id) => id === 'a')).toBe(false);
    expect(fixAccepted(totals, () => true)).toBe(true);
  });
  it('a blocker with no fix never stands down by itself', () => {
    expect(fixAccepted(issue({ fix: [] }), () => true)).toBe(false);
  });
});
