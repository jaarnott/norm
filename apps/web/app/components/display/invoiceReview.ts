/**
 * The invoice card's review list: one row per thing to decide, each with the
 * fix its Accept applies.
 *
 * Since 12 Oct 2026 the server sends it ready-made (invoice_review.py,
 * doc_schema replica_v2): every suggestion is a place the Replica — what Norm
 * thinks the invoice says — differs from Loaded, and every blocker (what stops
 * a receive) names the suggestion(s) that clear it in `fix`. So a row is a
 * suggestion, or the suggestions that together are one decision (a line added
 * with its unit; costs moved to balance the totals). A blocker is never a row
 * of its own unless nothing can fix it — then it is the row, and its "Fix…"
 * opens the line's own picker.
 */

export interface ReviewIssue {
  id: string;
  code: string;
  blocking: boolean;
  line_id?: string | null;
  message: string;
  gate?: string | null;
  /** The suggestion ids that clear this blocker, all of them accepted. */
  fix?: string[] | null;
  clears_when?: unknown;
  data?: Record<string, unknown> | null;
}

export interface ReviewSuggestion {
  id: string;
  kind: string;
  field?: string | null;
  line_id?: string | null;
  explanation: string;
  current?: unknown;
  proposed?: unknown;
  apply?: Record<string, unknown> | null;
  payload?: Record<string, unknown> | null;
  /** False: the Replica's guess — Norm never applies it unattended unless
   *  `gate` (a venue switch) is on. */
  sure?: boolean;
  gate?: string | null;
  /** The blocker a rule set this value to clear. */
  fixes?: string | null;
}

export interface ReviewRow {
  key: string;
  /** The blocker this row clears, when it clears one. */
  issue?: ReviewIssue;
  /** What Accept applies — one suggestion, or several that are one decision. */
  fixes: ReviewSuggestion[];
  /** An added line's blockers nothing could answer (said in words). */
  needs?: ReviewIssue[];
  lineId: string | null;
  /** Still waiting on the person. */
  open: boolean;
}

export interface ReviewList {
  rows: ReviewRow[];
  /** Things worth knowing with nothing to decide (a pairing note). */
  notes: ReviewIssue[];
  /** The open "delete this draft" row, when there is one — every other row is
   *  moot until it is decided. */
  deleteRow: ReviewRow | null;
}

export interface ReviewInput {
  issues: ReviewIssue[];
  suggestions: ReviewSuggestion[];
  /** Line ids in the order the table shows them. */
  lineOrder: string[];
  /** The blocker still stands (not cleared, decided, or fixed). */
  issueOpen: (i: ReviewIssue) => boolean;
  suggestionPending: (s: ReviewSuggestion) => boolean;
  /** The line's field still names something Loaded has no record of (New).
   *  Dismissing Norm's answer for it doesn't settle it, so its row stays open
   *  until it is accepted or a person picks or creates a record (owner,
   *  19 Oct 2026). */
  stillNew?: (lineId: string, field: string) => boolean;
  /** Studying the supplier: suggestions are about to be replaced, so hide them. */
  hideSuggestions?: boolean;
}

/** Blockers about a line's unit — one decision, whichever words raised it. */
export const UNIT_ISSUE_CODES = [
  'unit_missing', 'unit_unconfirmed', 'unit_unknown', 'unit_would_be_created', 'unit_answer_missing',
];

/** Norm's answer didn't come through (owner, 19 Oct 2026): the next review asks
 *  again. They stop Autopilot, not a person — someone at the card can pick or
 *  create the item or unit themselves. */
export const ANSWER_MISSING_CODES = ['unit_answer_missing', 'item_answer_missing'];

/** Blockers only asking again stands down (invoice_review.RETRY_NEXT_REVIEW):
 *  never "checked" — Autopilot would receive what Norm couldn't answer — only
 *  a person's own record for the line. */
export const RETRY_CODES = [...ANSWER_MISSING_CODES, 'copy_not_checked'];

/** A person's change to a field, as the server keeps it
 *  (services/invoice_edits). */
export interface HandEdit {
  seq: number;
  at?: string | null;
  line_id?: string | null;
  field?: string;
  before?: unknown;
  after?: unknown;
  /** Which invoice line it was made on, for a line Norm added: its id is a
   *  place in Norm's reading, which a later review can give another line. */
  line_key?: { code?: string | null; description?: string | null } | null;
  /** Cancelled by an Undo of the decision it was made after. */
  undone_at?: string | null;
  /** Replaced by accepting one of Norm's suggestions (an Undo of that
   *  accept brings it back). */
  superseded_by?: string | null;
}

/** What makes a group of fields one value of the person's — the record they
 *  picked, the brand and its record together, the quantity, the price — never
 *  a name or a ratio that happens to match (invoice_edits._OWNED_BY). */
const OWNED_BY: [string[], string[]][] = [
  [['unit', 'linked_unit_id', 'unit_ratio'], ['linked_unit_id']],
  [['linked_item_id', 'item_name'], ['linked_item_id']],
  [['brand', 'linked_brand_id', 'brand_set'], ['brand', 'linked_brand_id']],
  [['quantity_received', 'total_cost'], ['quantity_received']],
  [['unit_cost', 'total_cost'], ['unit_cost']],
  [['linked_supplier_id', 'supplier_name'], ['linked_supplier_id']],
  [['linked_purchase_order_id', 'purchase_order_number'], ['linked_purchase_order_id']],
];

/** Blockers a person's own value answers: the field that value is, and what
 *  it must be — a record ('record'), a brand record or no brand at all
 *  ('brand'), or anything (a line kept or struck). The supplier and the order
 *  are the invoice's own (invoice_edits._HAND_SETTLES). */
const HAND_SETTLES: Record<string, [string, 'record' | 'brand' | null]> = {
  ...Object.fromEntries([...UNIT_ISSUE_CODES, 'unit_kind_mismatch'].map(
    (c) => [c, ['linked_unit_id', 'record'] as [string, 'record']],
  )),
  item_unmatched: ['linked_item_id', 'record'],
  item_answer_missing: ['linked_item_id', 'record'],
  brand_unknown: ['linked_brand_id', 'brand'],
  loaded_line_not_on_copy: ['struck', null],
  supplier_unresolved: ['linked_supplier_id', 'record'],
  po_unresolved: ['linked_purchase_order_id', 'record'],
};

const ADDED_PREFIXES = ['rep-', 'totals-'];

/** An edit on a line Norm added counts only on the invoice line it was made
 *  on (invoice_edits._same_line). */
export function sameLine(e: HandEdit, line: Record<string, unknown>): boolean {
  const id = String(e.line_id ?? '');
  if (!ADDED_PREFIXES.some((p) => id.startsWith(p)) || !e.line_key) return true;
  return (e.line_key.code ?? null) === (line.code ?? null)
    && (e.line_key.description ?? null) === (line.description ?? null);
}

/** The person's own value answers this blocker: their last change to what
 *  makes it their value still stands, on the invoice line they made it on,
 *  and the card still shows it (a unit, item, supplier or order they picked,
 *  a brand they chose or cleared, a line they struck or kept). The server's
 *  invoice_edits.hand_settles — the record arrives with each saved change,
 *  so a value just changed on this card counts once its save comes back. */
export function settledByHand(
  issue: Pick<ReviewIssue, 'code' | 'line_id'>,
  doc: { lines?: unknown[] } & Record<string, unknown>,
  edits: HandEdit[] | undefined,
): boolean {
  const rule = HAND_SETTLES[issue.code];
  if (!rule || !edits?.length) return false;
  const [field, needs] = rule;
  const lineId = issue.line_id ? String(issue.line_id) : null;
  const target = lineId === null
    ? doc
    : (doc.lines ?? []).find((l) => String((l as Record<string, unknown>)?.id) === lineId) as
      Record<string, unknown> | undefined;
  if (!target) return false;
  if (needs === 'record' && !target[field]) return false;
  if (needs === 'brand' && !target[field] && (target.brand ?? null) !== null) return false;
  const last = new Map<string, HandEdit>();
  for (const e of [...edits].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0))) {
    if (!e.field || e.undone_at || e.superseded_by) continue;
    if ((e.line_id ? String(e.line_id) : null) !== lineId) continue;
    if (lineId !== null && !sameLine(e, target)) continue;
    last.set(e.field, e);
  }
  const holds = (f: string) => {
    const e = last.get(f);
    if (!e) return false;
    if (f === 'struck') return !!target[f] === !!e.after;
    return JSON.stringify(target[f] ?? null) === JSON.stringify(e.after ?? null);
  };
  const owned = (f: string) => {
    const group = OWNED_BY.find(([fields]) => fields.includes(f));
    return group ? group[1].every(holds) : holds(f);
  };
  // A kind mismatch is between the unit and the item: both must be theirs.
  if (issue.code === 'unit_kind_mismatch' && !owned('linked_item_id')) return false;
  return owned(field);
}

/** An accepted fix still in force: a line's value it set is still on the
 *  line — one carried while its line wasn't on the card set nothing
 *  (invoice_review._fix_holds). */
export function fixHolds(
  s: Pick<ReviewSuggestion, 'kind' | 'line_id' | 'apply'> | undefined,
  doc: { lines?: unknown[] },
): boolean {
  if (!s || !s.line_id || !['line_value', 'strike'].includes(s.kind)) return true;
  const line = (doc.lines ?? []).find((l) => String((l as Record<string, unknown>)?.id) === String(s.line_id)) as
    Record<string, unknown> | undefined;
  if (!line) return false;
  return Object.entries(s.apply ?? {}).every(([k, v]) => (k === 'struck'
    ? !!line[k] === !!v
    : JSON.stringify(line[k] ?? null) === JSON.stringify(v ?? null)));
}

/** Suggestion kinds that write to Loaded, in the order they must run: the
 *  supplier before anything that hangs off it, then the unit and the brand a
 *  new item is created with, then the item (invoice_review.LOADED_WRITE_ORDER). */
export const LOADED_WRITE_ORDER = ['create_supplier', 'create_unit', 'create_brand', 'create_item'];

const NEED_WORDS: Record<string, string> = {
  item_unmatched: 'a stock item',
  item_answer_missing: 'a stock item',
  unit_missing: 'a unit',
  unit_answer_missing: 'a unit',
  unit_unconfirmed: 'a unit',
  unit_unknown: 'a unit',
  unit_would_be_created: 'a unit',
  brand_unknown: 'its brand in Loaded',
};

/** What a line will still need once it is added, in plain words. */
export function needWords(needs: ReviewIssue[]): string[] {
  return [...new Set(needs.map((i) => NEED_WORDS[i.code]).filter(Boolean))];
}

/** Has every fix this blocker names been accepted? */
export function fixAccepted(i: ReviewIssue, accepted: (id: string) => boolean): boolean {
  return !!i.fix?.length && i.fix.every(accepted);
}

// Unit before item before brand: the order the work has to be done in (an
// item can't be created without its unit).
function topicRank(row: ReviewRow): number {
  const code = row.issue?.code ?? '';
  const field = row.fixes[0]?.field ?? '';
  if (row.fixes[0]?.kind === 'add_line') return 0;
  if (UNIT_ISSUE_CODES.includes(code) || field === 'unit') return 1;
  if (code === 'item_unmatched' || code === 'item_answer_missing' || field === 'linked_item_id') return 2;
  if (code === 'brand_unknown' || field === 'linked_brand_id') return 3;
  return 4;
}

export function buildReviewList(input: ReviewInput): ReviewList {
  const { issues, lineOrder, issueOpen } = input;
  const settlesNew = (s: ReviewSuggestion) => !!s.line_id && !!s.field
    && !!input.stillNew?.(String(s.line_id), String(s.field));
  const suggestionPending = (s: ReviewSuggestion) => input.suggestionPending(s) || settlesNew(s);
  const suggestions = input.hideSuggestions ? [] : input.suggestions;
  const onInvoice = new Set(lineOrder.map(String));
  const rows: ReviewRow[] = [];
  const notes: ReviewIssue[] = [];
  const used = new Set<string>();
  const ownerOf = new Map<string, ReviewIssue>();
  for (const i of issues) for (const f of i.fix ?? []) if (!ownerOf.has(f)) ownerOf.set(f, i);

  // A line the invoice has but the draft doesn't yet: its unit and item ride
  // on the add, so one Accept lands the line complete (Aitkens 173670).
  const notAdded = new Set<string>();
  for (const s of input.suggestions) {
    if (s.kind === 'add_line' && s.line_id && !onInvoice.has(String(s.line_id))) {
      notAdded.add(String(s.line_id));
    }
  }
  for (const s of suggestions) {
    if (s.kind !== 'add_line' || !s.line_id || !notAdded.has(String(s.line_id))) continue;
    const lid = String(s.line_id);
    const companions = suggestions.filter(
      (o) => o.id !== s.id && String(o.line_id ?? '') === lid && o.kind !== 'add_line',
    );
    [s, ...companions].forEach((o) => used.add(o.id));
    rows.push({
      key: s.id,
      fixes: [s, ...companions],
      needs: issues.filter((i) => String(i.line_id ?? '') === lid && i.blocking && !i.fix?.length),
      lineId: lid,
      open: suggestionPending(s),
    });
  }

  for (const s of suggestions) {
    if (used.has(s.id)) continue;
    const owner = ownerOf.get(s.id);
    if (owner && (owner.fix?.length ?? 0) > 1) {
      // Several values that are one decision (a totals fix across lines).
      const fixes = suggestions.filter((o) => owner.fix?.includes(o.id) && !used.has(o.id));
      fixes.forEach((o) => used.add(o.id));
      rows.push({
        key: owner.id,
        issue: owner,
        fixes,
        lineId: owner.line_id ? String(owner.line_id) : null,
        open: fixes.some((o) => suggestionPending(o)),
      });
      continue;
    }
    used.add(s.id);
    rows.push({
      key: s.id,
      issue: owner,
      fixes: [s],
      lineId: s.line_id ? String(s.line_id) : null,
      open: suggestionPending(s),
    });
  }

  for (const i of issues) {
    const lid = i.line_id ? String(i.line_id) : null;
    if (lid && notAdded.has(lid)) continue;
    if (i.fix?.some((f) => suggestions.some((s) => s.id === f))) continue;
    if (!i.blocking) {
      notes.push(i);
      continue;
    }
    // Nothing can fix it — the blocker is the row.
    rows.push({ key: i.id, issue: i, fixes: [], lineId: lid, open: issueOpen(i) });
  }

  const pos = (lid: string | null) => {
    if (lid === null) return -1;
    const k = lineOrder.indexOf(lid);
    return k >= 0 ? k : lineOrder.length;
  };
  rows.sort((a, b) =>
    Number(!a.open) - Number(!b.open)
    || pos(a.lineId) - pos(b.lineId)
    || topicRank(a) - topicRank(b));

  const deleteRow = rows.find((r) => r.open && r.fixes.some((s) => s.kind === 'delete_invoice')) ?? null;
  return { rows, notes, deleteRow };
}
