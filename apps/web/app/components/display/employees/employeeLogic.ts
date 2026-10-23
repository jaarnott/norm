/**
 * Pure logic for HR → Employees (the `employee_records` screen): reading
 * `bamboohr.get_hr`'s two employee documents, filtering the directory, and
 * the plain-words labels the screen shows.
 *
 * PRIVACY. Both readers below copy only the fields named here. get_hr returns
 * nothing sensitive today (its consolidator keeps an allow-list too), but if
 * it ever starts returning a date of birth, pay, tax or bank numbers, a home
 * address or emergency contacts, this screen still never shows them.
 */

// ------------------------------------------------------------ the list ----

/** One person from get_hr's directory (view 'employees', no employee_id). */
export interface DirectoryRow {
  id: string;
  name: string;
  preferred: string | null;
  job_title: string | null;
  /** BambooHR's location — the venue they work at. */
  venue: string | null;
  /** BambooHR's division: Kitchen, Front of House, Management. */
  division: string | null;
  /** BambooHR's department, which this account uses for the employing company. */
  company: string | null;
  work_email: string | null;
  mobile: string | null;
  /** Not in the directory today (it lists current staff only). Read when the
   *  tool starts returning them; `undefined` = the tool doesn't say. */
  status?: string | null;
  hire_date?: string | null;
}

export interface Directory {
  employees: DirectoryRow[];
  current_staff: number;
  by_venue: Record<string, number>;
  note: string | null;
}

const str = (v: unknown): string | null => {
  if (v === null || v === undefined) return null;
  const s = String(v).trim();
  return s && s !== '0000-00-00' ? s : null;
};

function unwrap(data: unknown): Record<string, unknown> | null {
  if (!data || typeof data !== 'object') return null;
  const d = data as Record<string, unknown>;
  // The execute route wraps the tool's document: { success, data, error }.
  const inner = d.data;
  if (inner && typeof inner === 'object' && !Array.isArray(inner)) return inner as Record<string, unknown>;
  return d;
}

/** The tool's own error, if it returned one instead of a document. */
export function toolError(body: unknown): string | null {
  if (!body || typeof body !== 'object') return null;
  const b = body as Record<string, unknown>;
  const inner = b.data && typeof b.data === 'object' ? (b.data as Record<string, unknown>).error : null;
  const e = inner || b.error || b.detail;
  return typeof e === 'string' && e.trim() ? e : null;
}

/** get_hr's directory, wherever the caller put it, or null. */
export function extractDirectory(data: unknown): Directory | null {
  const d = unwrap(data);
  if (!d || !Array.isArray(d.employees)) return null;
  const employees: DirectoryRow[] = [];
  for (const raw of d.employees as unknown[]) {
    if (!raw || typeof raw !== 'object') continue;
    const p = raw as Record<string, unknown>;
    const id = str(p.id);
    if (!id) continue;
    const row: DirectoryRow = {
      id,
      name: str(p.name) || [str(p.first_name), str(p.last_name)].filter(Boolean).join(' ') || 'Unnamed',
      preferred: str(p.preferred),
      job_title: str(p.job_title),
      venue: str(p.venue),
      division: str(p.division),
      company: str(p.company),
      work_email: str(p.work_email),
      mobile: str(p.mobile),
    };
    if ('status' in p) row.status = str(p.status);
    const hire = 'hire_date' in p ? p.hire_date : 'start_date' in p ? p.start_date : undefined;
    if (hire !== undefined) row.hire_date = str(hire);
    employees.push(row);
  }
  const byVenue: Record<string, number> = {};
  if (d.by_venue && typeof d.by_venue === 'object') {
    for (const [k, v] of Object.entries(d.by_venue as Record<string, unknown>)) {
      if (typeof v === 'number') byVenue[k] = v;
    }
  }
  return {
    employees,
    current_staff: typeof d.current_staff === 'number' ? d.current_staff : employees.length,
    by_venue: byVenue,
    note: str(d.note),
  };
}

// ---------------------------------------------------------- one person ----

/** One person's record (get_hr with employee_id) — the allowed fields only. */
export interface PersonDetail {
  id: string;
  name: string;
  preferredName: string | null;
  jobTitle: string | null;
  /** The employing company (BambooHR's department in this account). */
  department: string | null;
  location: string | null;
  division: string | null;
  status: string | null;
  employmentHistoryStatus: string | null;
  hireDate: string | null;
  originalHireDate: string | null;
  terminationDate: string | null;
  workEmail: string | null;
  workPhone: string | null;
  mobilePhone: string | null;
  /** "Arnott, James" as BambooHR gives it. */
  supervisor: string | null;
}

export function extractPerson(data: unknown): PersonDetail | null {
  const d = unwrap(data);
  if (!d) return null;
  const id = str(d.employee_id) || str(d.id);
  if (!id) return null;
  const first = str(d.firstName);
  const last = str(d.lastName);
  return {
    id,
    name: str(d.displayName) || [first, last].filter(Boolean).join(' ') || 'Unnamed',
    preferredName: str(d.preferredName),
    jobTitle: str(d.jobTitle),
    department: str(d.department),
    location: str(d.location),
    division: str(d.division),
    status: str(d.status),
    employmentHistoryStatus: str(d.employmentHistoryStatus),
    hireDate: str(d.hireDate),
    originalHireDate: str(d.originalHireDate),
    terminationDate: str(d.terminationDate),
    workEmail: str(d.workEmail),
    workPhone: str(d.workPhone),
    mobilePhone: str(d.mobilePhone),
    supervisor: str(d.supervisor),
  };
}

// --------------------------------------------------------------- venues ----

/**
 * The name key get_hr itself compares venues with: case, punctuation and a
 * leading "The" dropped, "&" read as "and". "The Glass Goose" and "Glass
 * Goose" match, "Mr Murdoch's" and "Mr Murdochs" match; a whole name still
 * has to match.
 */
export function venueKey(name: string | null | undefined): string {
  let kept = '';
  for (const ch of String(name ?? '').toLowerCase()) {
    if (/[\p{L}\p{N}]/u.test(ch)) kept += ch;
    else if (ch === '&') kept += ' and ';
    else if (' -/,.'.includes(ch)) kept += ' ';
  }
  const words = kept.split(/\s+/).filter(Boolean);
  if (words[0] === 'the') words.shift();
  return words.join(' ');
}

/** BambooHR's location for a Norm venue, or null when none matches. */
export function matchLocation(venueName: string | null | undefined, locations: string[]): string | null {
  const key = venueKey(venueName);
  if (!key) return null;
  return locations.find((l) => venueKey(l) === key) ?? null;
}

// -------------------------------------------------------------- filters ----

export type StatusFilter = 'active' | 'inactive' | 'all';
/** Team filter value for people with no division set. */
export const NO_TEAM = '__none__';

export interface Filters {
  query: string;
  /** BambooHR location; '' = every venue. */
  location: string;
  /** Division; '' = every team, NO_TEAM = none set. */
  team: string;
  /** Job title; '' = every role. */
  role: string;
  status: StatusFilter;
}

export const EMPTY_FILTERS: Filters = { query: '', location: '', team: '', role: '', status: 'active' };

/** Active unless the record says otherwise: the directory lists current staff. */
export function isActive(status: string | null | undefined): boolean {
  if (!status) return true;
  return !/inactive|terminated|former|left/i.test(status);
}

function haystack(r: DirectoryRow): string {
  return [r.name, r.preferred, r.job_title, r.division, r.venue, r.work_email].filter(Boolean).join(' ').toLowerCase();
}

/** Every word of the search must appear somewhere in the person's row. */
export function matchesQuery(r: DirectoryRow, query: string): boolean {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = haystack(r);
  return words.every((w) => hay.includes(w));
}

export function applyFilters(
  rows: DirectoryRow[],
  f: Filters,
  statusOf: (r: DirectoryRow) => string | null | undefined = (r) => r.status,
  skip: Partial<Record<keyof Filters, boolean>> = {},
): DirectoryRow[] {
  return rows.filter((r) => {
    if (!skip.query && !matchesQuery(r, f.query)) return false;
    if (!skip.location && f.location && r.venue !== f.location) return false;
    if (!skip.team && f.team) {
      if (f.team === NO_TEAM ? !!r.division : r.division !== f.team) return false;
    }
    if (!skip.role && f.role && r.job_title !== f.role) return false;
    if (!skip.status && f.status !== 'all') {
      const active = isActive(statusOf(r));
      if (f.status === 'active' ? !active : active) return false;
    }
    return true;
  });
}

const byText = (a: string, b: string) => a.localeCompare(b, 'en', { sensitivity: 'base' });

/** Venue, then name — the order a manager scans a staff list in. */
export function sortRows(rows: DirectoryRow[]): DirectoryRow[] {
  return [...rows].sort((a, b) => byText(a.venue ?? '￿', b.venue ?? '￿') || byText(a.name, b.name));
}

/** Distinct values of one field, sorted, for a filter's options. */
export function distinct(rows: DirectoryRow[], pick: (r: DirectoryRow) => string | null): string[] {
  return [...new Set(rows.map(pick).filter((v): v is string => !!v))].sort(byText);
}

/** People per team, biggest first. */
export function teamCounts(rows: DirectoryRow[]): { team: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const r of rows) if (r.division) counts.set(r.division, (counts.get(r.division) ?? 0) + 1);
  return [...counts.entries()].map(([team, count]) => ({ team, count })).sort((a, b) => b.count - a.count || byText(a.team, b.team));
}

// --------------------------------------------------------------- labels ----

/** "Arnott, James" → "James Arnott". */
export function managerName(supervisor: string | null | undefined): string | null {
  if (!supervisor) return null;
  const m = /^\s*([^,]+),\s*(.+?)\s*$/.exec(supervisor);
  return m ? `${m[2]} ${m[1]}` : supervisor.trim();
}

/** "Full-Time" → "Full-time". */
export function sentenceCase(s: string | null | undefined): string | null {
  if (!s) return null;
  const t = s.trim();
  return t ? t.charAt(0).toUpperCase() + t.slice(1).toLowerCase() : null;
}

/** A first name worth showing beside the full one ("Ash" for Ashley, not "Felipe" for Felipe). */
export function goesBy(name: string, preferred: string | null | undefined): string | null {
  if (!preferred) return null;
  const first = name.trim().split(/\s+/)[0] ?? '';
  return preferred.trim().toLowerCase() === first.toLowerCase() ? null : preferred.trim();
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function parts(iso: string | null | undefined): [number, number, number] | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? '');
  if (!m) return null;
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = Number(m[3]);
  if (mo < 1 || mo > 12 || d < 1 || d > 31) return null;
  return [y, mo, d];
}

/** "2025-06-04" → "4 Jun 2025". */
export function formatDay(iso: string | null | undefined): string {
  const p = parts(iso);
  if (!p) return iso || '—';
  return `${p[2]} ${MONTHS[p[1] - 1]} ${p[0]}`;
}

/** How long since a start date, in plain words: "1 year 4 months". */
export function tenure(startIso: string | null | undefined, today: Date): string | null {
  const p = parts(startIso);
  if (!p) return null;
  let months = (today.getFullYear() - p[0]) * 12 + (today.getMonth() + 1 - p[1]);
  if (today.getDate() < p[2]) months -= 1;
  if (months < 0) return 'starts soon';
  if (months === 0) return 'under a month';
  const y = Math.floor(months / 12);
  const m = months % 12;
  const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`;
  return [y ? plural(y, 'year') : null, m ? plural(m, 'month') : null].filter(Boolean).join(' ');
}

// --------------------------------------------------------------- errors ----

export type ErrorKind = 'not_connected' | 'refused' | 'failed';

/**
 * A get_hr failure in words for the person reading the page. The tool's
 * messages are written for Norm ("Tell the user that…"); the screen keeps
 * the part that says what happened.
 */
export function readError(message: string): { kind: ErrorKind; text: string } {
  if (/No credentials|not connected|not configured|connector not configured/i.test(message)) {
    return { kind: 'not_connected', text: 'BambooHR is not connected.' };
  }
  if (/refused Norm's API key|\b401\b|\b403\b/i.test(message)) {
    return { kind: 'refused', text: 'BambooHR refused Norm’s API key. An admin needs to re-enter it.' };
  }
  if (/network error|timed out|timeout|ssl|connection (?:refused|reset)|temporarily unavailable|\b50[234]\b/i.test(message)) {
    return { kind: 'failed', text: 'BambooHR couldn’t be reached just now. Try again in a minute.' };
  }
  const cut = message.split(/ — this is a BambooHR failure| Tell the user/)[0].trim();
  // The technical detail BambooHR's failures end with, "(…)", isn't for people.
  const plain = cut.replace(/\s*\((?:[^()]|\([^()]*\))*\)\s*$/, '').trim();
  return { kind: 'failed', text: plain ? `${plain.replace(/[.\s]+$/, '')}.` : 'BambooHR didn’t answer.' };
}
