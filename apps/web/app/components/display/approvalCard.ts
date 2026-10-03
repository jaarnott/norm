/**
 * The approval card's logic — pure, so it is tested without rendering.
 *
 * Every write Norm proposes in a turn arrives as one row with its own preview
 * (apps/api/app/services/previews.py): what changes, before → after, read from
 * live data. The card groups rows by kind of change, lets the person tick the
 * ones to approve, and after the decision shows what happened to each row.
 */

export interface PreviewChange {
  field: string;
  before?: string;
  after: string;
}

export interface Preview {
  title: string;
  target?: string | null;
  venue?: string | null;
  changes: PreviewChange[];
  warnings?: string[];
  note?: string | null;
  source?: 'tool' | 'writes' | 'params';
  more_changes?: number;
}

export interface ApprovalRow {
  id: string;
  action: string;
  connector_name: string;
  method?: string;
  summary: string;
  preview?: Preview | null;
  input_params?: Record<string, unknown> | null;
  /** The tool, for "Always allow Norm to {label} without asking". */
  approval?: { key: string; label?: string | null; allow_auto?: boolean } | null;
  /** The decision on this row: approved | rejected | superseded. */
  status?: string;
  /** What happened once approved: done | failed | changed. */
  outcome?: string;
  outcome_note?: string | null;
}

export interface RowGroup {
  title: string;
  rows: ApprovalRow[];
}

/** Rows grouped by the kind of change, in the order Norm proposed them. */
export function groupRows(rows: ApprovalRow[]): RowGroup[] {
  const groups: RowGroup[] = [];
  const byTitle = new Map<string, RowGroup>();
  for (const row of rows) {
    const title = row.preview?.title || row.summary || row.action.replace(/_/g, ' ');
    let group = byTitle.get(title);
    if (!group) {
      group = { title, rows: [] };
      byTitle.set(title, group);
      groups.push(group);
    }
    group.rows.push(row);
  }
  return groups;
}

/** The tools on this card the person may tell Norm to stop asking about. */
export function allowable(rows: ApprovalRow[]): { key: string; label: string }[] {
  const seen = new Map<string, string>();
  for (const r of rows) {
    const a = r.approval;
    if (a?.allow_auto && a.key && !seen.has(a.key)) seen.set(a.key, a.label || r.action.replace(/_/g, ' '));
  }
  return [...seen].map(([key, label]) => ({ key, label }));
}

/**
 * What an Approve click sends. Every row ticked: no list (approve them all).
 * Some ticked: their ids, and the server declines the rest by name. Any
 * "always allow" ticked: those tools, saved as the approver's own preference.
 */
export function approveParams(
  threadId: string,
  rows: ApprovalRow[],
  ticked: Set<string>,
  always: Set<string> = new Set(),
) {
  const ids = rows.map(r => r.id).filter(id => ticked.has(id));
  const keys = allowable(rows).map(t => t.key).filter(k => always.has(k));
  return {
    thread_id: threadId,
    ...(ids.length === rows.length ? {} : { tool_call_ids: ids }),
    ...(keys.length ? { always_allow: keys } : {}),
  };
}

export function declineParams(threadId: string, reason: string) {
  const notes = reason.trim();
  return notes ? { thread_id: threadId, notes } : { thread_id: threadId };
}

export type Tone = 'pending' | 'good' | 'bad' | 'warn' | 'muted';

/** One row's state, as its badge reads — null while it waits for a decision. */
export function rowState(row: ApprovalRow, cardStatus: string): { label: string; tone: Tone } | null {
  if (row.outcome === 'done') return { label: 'Done', tone: 'good' };
  if (row.outcome === 'failed') return { label: 'Failed', tone: 'bad' };
  if (row.outcome === 'changed') return { label: 'Changed since — not done', tone: 'warn' };
  const status = row.status || (cardStatus === 'pending' ? undefined : cardStatus);
  if (!status || status === 'pending') return null;
  if (status === 'approved') return { label: 'Approved', tone: 'good' };
  if (status === 'superseded') return { label: 'Not done', tone: 'muted' };
  return { label: 'Declined', tone: 'muted' };
}

/** The card's header badge. */
export function cardLabel(cardStatus: string, rows: ApprovalRow[]): string {
  if (cardStatus === 'pending') return 'Approval needed';
  if (cardStatus === 'superseded') return 'Not done — you moved on';
  if (cardStatus === 'approved') {
    return rows.some(r => r.status === 'rejected') ? 'Partly approved' : 'Approved';
  }
  return 'Declined';
}
