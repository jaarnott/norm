// The side panel's search box: a pure function over the fields the thread
// list already has loaded — search never reaches into a conversation's text.
// (The status filter chips — All / Awaiting approval / Needs input /
// Completed — were removed in Oct 2026.)

interface Searchable {
  title?: string | null;
  message?: string | null;
  apps?: { name: string }[];
  automated_task?: { title?: string | null } | null;
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
