// Which threads the side panel lists: the status filter chips and the search
// box. Pure functions over the fields the thread list already has loaded —
// search never reaches into a conversation's text.

export type FilterKey = 'all' | 'awaiting_approval' | 'awaiting_user_input' | 'completed';

/** Finished threads. 'completed' is an ordinary chat the tool loop wrapped up;
 *  'submitted'/'rejected' are decided approvals. Not 'approved': for an order
 *  that can mean approved but the send failed, which is still retryable. */
export const COMPLETED_STATUSES = new Set(['completed', 'submitted', 'rejected']);

/** Waiting on the user to answer a question. */
const NEEDS_INPUT_STATUSES = new Set(['awaiting_user_input', 'needs_clarification']);

interface Filterable {
  status: string;
  automated_task?: { waiting_for_approval?: number } | null;
}

interface Searchable {
  title?: string | null;
  message?: string | null;
  apps?: { name: string }[];
  automated_task?: { title?: string | null } | null;
}

/** Waiting on a person's approval: a change Norm proposed in the thread, or a
 *  scheduled task's run that stopped at one with nobody there. */
export function awaitsApproval(t: Filterable): boolean {
  return t.status === 'awaiting_approval'
    || t.status === 'awaiting_tool_approval'
    || (t.automated_task?.waiting_for_approval ?? 0) > 0;
}

export function applyStatusFilter<T extends Filterable>(threads: T[], filter: FilterKey): T[] {
  if (filter === 'all') return threads;
  // A finished chat whose scheduled task has a run waiting is not done yet.
  if (filter === 'completed') return threads.filter(t => COMPLETED_STATUSES.has(t.status) && !awaitsApproval(t));
  if (filter === 'awaiting_approval') return threads.filter(awaitsApproval);
  return threads.filter(t => NEEDS_INPUT_STATUSES.has(t.status));
}

/** Search: the query (trimmed, any case) appears in the title, the first
 *  prompt, an App's name or the scheduled task's title. An empty query
 *  matches everything. */
export function matchesQuery(t: Searchable, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const fields = [t.title, t.message, t.automated_task?.title, ...(t.apps ?? []).map(a => a.name)];
  return fields.some(f => !!f && f.toLowerCase().includes(q));
}
