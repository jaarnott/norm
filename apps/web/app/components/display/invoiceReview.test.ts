import { describe, it, expect } from 'vitest';
import {
  buildReviewList, fixAccepted, fixHolds, needWords, settledByHand,
  type HandEdit, type ReviewIssue, type ReviewSuggestion,
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
    // Norm's answer not coming through is still "needs a stock item / a unit".
    expect(needWords([
      issue({ code: 'item_answer_missing' }), issue({ code: 'unit_answer_missing' }),
    ])).toEqual(['a stock item', 'a unit']);
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

describe('a field Loaded marks New', () => {
  // IN11692458: the fries line read "CTN — new". Dismissing Norm's "3x5kg
  // (new)" left it New, so its row stays open until the unit is accepted,
  // picked or created (owner, 19 Oct 2026).
  it('stays open when Norm\'s answer is dismissed, until the field is settled', () => {
    const create = sugg({ kind: 'create_unit', field: 'unit', line_id: 'a' });
    const rows = (unitNew: boolean) => buildReviewList({
      issues: [], suggestions: [create], lineOrder: ['a'],
      issueOpen: () => true,
      suggestionPending: () => false, // dismissed
      stillNew: (lid, field) => unitNew && lid === 'a' && field === 'unit',
    }).rows;
    expect(rows(true)[0].open).toBe(true);
    expect(rows(false)[0].open).toBe(false);
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

describe('a blocker answered by the person\'s own value', () => {
  const unitMissing = issue({ code: 'unit_missing', line_id: 'a' });
  const picked: HandEdit[] = [
    { seq: 1, line_id: 'a', field: 'linked_unit_id', after: 'u-kilo' },
    { seq: 2, line_id: 'a', field: 'unit', after: 'Kilo' },
    { seq: 3, line_id: 'a', field: 'unit_ratio', after: 1 },
  ];
  const doc = (line: Record<string, unknown>, header: Record<string, unknown> = {}) => (
    { ...header, lines: [{ id: 'a', ...line }] });
  it('stands down while the line shows the unit they picked', () => {
    expect(settledByHand(unitMissing, doc({ linked_unit_id: 'u-kilo', unit: 'Kilo', unit_ratio: 1 }), picked)).toBe(true);
    // Changed since (an Undo, another pick not yet saved): not this edit's answer.
    expect(settledByHand(unitMissing, doc({ linked_unit_id: 'u-5', unit: '5kg', unit_ratio: 1 }), picked)).toBe(false);
    // A ratio that happens to match is not their unit, and no unit is no answer.
    expect(settledByHand(unitMissing, doc({ linked_unit_id: null }), picked)).toBe(false);
  });
  it('not for an edit undone, replaced by an accepted suggestion, or made on another invoice line', () => {
    const line = { linked_unit_id: 'u-kilo', unit: 'Kilo', unit_ratio: 1 };
    expect(settledByHand(unitMissing, doc(line), picked.map((e) => ({ ...e, undone_at: 'x' })))).toBe(false);
    expect(settledByHand(unitMissing, doc(line), picked.map((e) => ({ ...e, superseded_by: 's' })))).toBe(false);
    const added = issue({ code: 'unit_unconfirmed', line_id: 'rep-2' });
    const onLemons = picked.map((e) => ({ ...e, line_id: 'rep-2', line_key: { code: null, description: 'LEMONS' } }));
    const limes = { lines: [{ id: 'rep-2', description: 'LIMES', ...line }] };
    expect(settledByHand(added, limes, onLemons)).toBe(false);
    expect(settledByHand(added, { lines: [{ ...limes.lines[0], description: 'LEMONS' }] }, onLemons)).toBe(true);
  });
  it('"no brand", a kept line and a supplier picked are answers; a brand name alone is not', () => {
    const brand = issue({ code: 'brand_unknown', line_id: 'a' });
    const noBrand: HandEdit[] = [
      { seq: 1, line_id: 'a', field: 'brand', after: null },
      { seq: 2, line_id: 'a', field: 'linked_brand_id', after: null },
    ];
    expect(settledByHand(brand, doc({ brand: null, linked_brand_id: null, brand_set: true }), noBrand)).toBe(true);
    const nameOnly: HandEdit[] = [{ seq: 1, line_id: 'a', field: 'brand', after: 'MCCAIN' }];
    expect(settledByHand(brand, doc({ brand: 'MCCAIN', linked_brand_id: null }), nameOnly)).toBe(false);
    const phantom = issue({ code: 'loaded_line_not_on_copy', line_id: 'a' });
    expect(settledByHand(phantom, doc({}), [{ seq: 1, line_id: 'a', field: 'struck', after: false }])).toBe(true);
    const supplier = issue({ id: 'supplier_unresolved', code: 'supplier_unresolved', line_id: null });
    const chose: HandEdit[] = [{ seq: 1, field: 'linked_supplier_id', after: 's-1' }];
    expect(settledByHand(supplier, doc({}, { linked_supplier_id: 's-1' }), chose)).toBe(true);
    expect(settledByHand(issue({ code: 'po_split_order', line_id: null }), doc({}, { linked_purchase_order_id: 'p' }),
      [{ seq: 1, field: 'linked_purchase_order_id', after: 'p' }])).toBe(false);
  });
});

describe('the fifth review', () => {
  it('a kind mismatch needs the person\'s item as well as their unit', () => {
    const kind = issue({ code: 'unit_kind_mismatch', line_id: 'a' });
    const unit: HandEdit[] = [{ seq: 1, line_id: 'a', field: 'linked_unit_id', after: 'u-kg' }];
    const line = { id: 'a', linked_unit_id: 'u-kg', linked_item_id: 'i-each' };
    expect(settledByHand(kind, { lines: [line] }, unit)).toBe(false);
    const both = [...unit, { seq: 2, line_id: 'a', field: 'linked_item_id', after: 'i-each' }];
    expect(settledByHand(kind, { lines: [line] }, both)).toBe(true);
  });
  it('an accepted fix counts only while its value is on the line', () => {
    const fix = { kind: 'line_value', line_id: 'rep-1', apply: { linked_unit_id: 'u-20l' } };
    expect(fixHolds(fix, { lines: [] })).toBe(false); // Norm's line not added yet
    expect(fixHolds(fix, { lines: [{ id: 'rep-1', linked_unit_id: 'u-20l' }] })).toBe(true);
    expect(fixHolds({ kind: 'confirm', line_id: 'rep-1', apply: {} }, { lines: [] })).toBe(true);
  });
});
