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
  /** The blocker still stands — the server's answer (invoice_decisions). */
  issueOpen: (i: ReviewIssue) => boolean;
  /** Still to decide — the server's answer: pending, or declined while its
   *  field still names something Loaded has no record of. */
  suggestionPending: (s: ReviewSuggestion) => boolean;
  /** Studying the supplier: suggestions are about to be replaced, so hide them. */
  hideSuggestions?: boolean;
}

/** Blockers about a line's unit — one decision, whichever words raised it. */
export const UNIT_ISSUE_CODES = [
  'unit_missing', 'unit_unconfirmed', 'unit_unknown', 'unit_would_be_created', 'unit_answer_missing',
];

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

const ADDED_PREFIXES = ['rep-', 'totals-'];

/** An edit on a line Norm added counts only on the invoice line it was made
 *  on (invoice_edits._same_line). */
export function sameLine(e: HandEdit, line: Record<string, unknown>): boolean {
  const id = String(e.line_id ?? '');
  if (!ADDED_PREFIXES.some((p) => id.startsWith(p)) || !e.line_key) return true;
  return (e.line_key.code ?? null) === (line.code ?? null)
    && (e.line_key.description ?? null) === (line.description ?? null);
}

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
  const { issues, lineOrder, issueOpen, suggestionPending } = input;
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
