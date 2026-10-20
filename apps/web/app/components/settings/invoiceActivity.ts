/**
 * Words for the invoice activity page (Settings → Invoice activity): how each
 * invoice's mode, outcome and the steps Norm took are said. Pure, so the
 * wording is tested on its own.
 */

export interface Level {
  mode: 'autopilot' | 'approve_fixes' | 'approve_all' | string;
  switches_on?: string[];
}

export interface Activity {
  total?: number | null;
  layout?: string | null;
  reread?: boolean;
  studied?: boolean;
  lines?: number;
  matched?: Record<string, number>;
  new_items?: number;
  units?: Record<string, number>;
  totals?: 'worked out' | "didn't add up" | 'added up';
  created?: Record<string, string[]>;
  /** Rows from before 19 Oct 2026 record only what Norm SUGGESTED creating
   *  (less what was dismissed or left pending) — never counted as created. */
  suggested_creates?: Record<string, number>;
  feed?: boolean;
}

export interface ActivityRow {
  key: string;
  venue_id: string;
  venue_name: string | null;
  invoice_id: string;
  reference_number: string | null;
  supplier_name: string | null;
  total: number | null;
  at: string | null;
  outcome: 'received' | 'deleted' | 'stopped' | 'waiting' | 'failed' | 'reported' | string;
  by: 'norm' | 'person';
  where: 'norm' | 'app' | 'claude' | string;
  run_mode?: string | null;
  level: Level | null;
  person_changes?: number;
  delete_reason?: string | null;
  delete_note?: string | null;
  reasons?: string[];
  gates_needed?: string[];
  activity: Activity;
  unit_check?: { flagged: number; open: number };
  thread_id?: string | null;
  /** What Norm suggested changing, by kind. */
  changes?: Record<string, number>;
}

export const MODE_WORDS: Record<string, string> = {
  autopilot: 'Autopilot',
  approve_fixes: 'Approve fixes',
  approve_all: 'Approve all',
};

const WHERE_WORDS: Record<string, string> = {
  norm: 'by Norm on its own',
  app: "in Norm's app",
  claude: 'on a card in Claude',
};

/** The receiving switches, as the Preferences page names what each allows. */
export const SWITCH_WORDS: Record<string, string> = {
  auto_create_units: 'create units',
  auto_create_items: 'create stock items',
  auto_create_brands: 'create brands',
  auto_create_suppliers: 'create suppliers',
  receive_with_unconfirmed_unit: "receive on Norm's unit",
  receive_without_po: 'receive without an order',
  receive_unit_kind_mismatch: 'receive in a different kind of unit',
  auto_strike_phantom_lines: "strike lines the invoice doesn't bill",
  auto_delete_duplicates: 'delete duplicates',
  auto_delete_non_invoices: "delete what isn't an invoice",
  auto_delete_unreadable: 'delete unreadable drafts',
};

const DELETE_WORDS: Record<string, string> = {
  duplicate: 'a duplicate of one already received',
  statement: 'a statement',
  not_an_invoice: 'not an invoice',
  unreadable: "the copy couldn't be read",
  no_copy: 'no invoice attached',
};

/** The kinds of change Norm suggests on an invoice, in words. */
const CHANGE_WORDS: Record<string, [string, string]> = {
  line_value: ['line figure', 'line figures'],
  header_value: ['invoice detail', 'invoice details'],
  supplier: ['supplier', 'suppliers'],
  add_line: ['added line', 'added lines'],
  strike: ['struck line', 'struck lines'],
  confirm: ['confirmation', 'confirmations'],
  link_po: ['order linked', 'orders linked'],
  split_reference: ['split reference', 'split references'],
};

const CREATED_WORDS: Record<string, [string, string]> = {
  suppliers: ['supplier', 'suppliers'],
  stock_items: ['stock item', 'stock items'],
  units: ['unit', 'units'],
  brands: ['brand', 'brands'],
};

export function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** The level in force, e.g. "Autopilot", with the switches that were on. */
export function levelWords(level: Level | null | undefined): { label: string; switches: string } | null {
  if (!level?.mode) return null;
  const on = (level.switches_on || []).map((s) => SWITCH_WORDS[s] || s.replace(/_/g, ' '));
  return {
    label: MODE_WORDS[level.mode] || level.mode,
    switches: on.length ? `Switches on: ${on.join(', ')}` : 'No switches on',
  };
}

/** A row's level: the one it recorded, else just its mode — a row from
 *  before levels were recorded (or a backfilled stop) never says "no
 *  switches on", which it doesn't know. */
export function rowLevel(row: { level?: Level | null; run_mode?: string | null }): { label: string; switches: string } | null {
  const recorded = levelWords(row.level);
  if (recorded) return recorded;
  if (!row.run_mode) return null;
  return { label: MODE_WORDS[row.run_mode] || row.run_mode, switches: 'Level not recorded' };
}

/** Where it was received (or handled), in words. */
export function whereWords(row: Pick<ActivityRow, 'where'>): string {
  return WHERE_WORDS[row.where] || row.where;
}

/** What happened to the invoice, in one short phrase. */
export function outcomeWords(row: ActivityRow): string {
  switch (row.outcome) {
    case 'received': {
      if (row.by === 'norm') return 'Received by Norm';
      // Where before the changes: "no changes in Norm's app" says something else.
      const who = row.where && row.where !== 'norm' ? `Received by a person ${whereWords(row)}` : 'Received by a person';
      return row.person_changes
        ? `${who}, with ${plural(row.person_changes, 'change', 'changes')}`
        : `${who}, no changes`;
    }
    case 'deleted': {
      // Where a person did it goes before the reason, which can name another
      // receive ("a duplicate of one already received").
      const who = row.by !== 'person' ? 'Deleted'
        : row.where && row.where !== 'norm' ? `Deleted by a person ${whereWords(row)}` : 'Deleted by a person';
      return `${who}: ${DELETE_WORDS[row.delete_reason || ''] || row.delete_note || 'no reason recorded'}`;
    }
    case 'stopped':
      return 'Stopped';
    case 'waiting':
      return 'Ready, waiting for a person to approve';
    case 'failed':
      return 'Failed';
    case 'reported':
      return "Reported as can't receive";
    default:
      return row.outcome;
  }
}

const DELETE_SHORT: Record<string, string> = {
  duplicate: 'duplicate',
  statement: 'statement',
  not_an_invoice: 'not an invoice',
  unreadable: 'unreadable',
  no_copy: 'no copy',
};

/** A delete's own note, when its reason's words didn't already say it. */
export function deleteNote(row: Pick<ActivityRow, 'delete_note' | 'delete_reason'>): string | null {
  if (!row.delete_note) return null;
  return DELETE_WORDS[row.delete_reason || ''] ? row.delete_note : null;
}

/** The outcome in a few words, for a one-line row. */
export function outcomeShort(row: ActivityRow): string {
  if (row.outcome === 'received' && row.by === 'person') {
    return row.person_changes ? `Received by a person, ${plural(row.person_changes, 'change', 'changes')}` : 'Received by a person';
  }
  if (row.outcome === 'deleted') {
    return `${row.by === 'person' ? 'Deleted by a person' : 'Deleted'}: ${DELETE_SHORT[row.delete_reason || ''] || 'other'}`;
  }
  if (row.outcome === 'waiting') return 'Waiting for approval';
  return outcomeWords(row);
}

/** The mode in a few words, for a one-line row. */
export function modeShort(row: ActivityRow): string {
  const level = rowLevel(row);
  const where = row.where === 'app' ? "Norm's app" : row.where === 'claude' ? 'Claude card' : '';
  return [level?.label, where].filter(Boolean).join(' · ');
}

export function createdWords(counts: Record<string, number>): string {
  return Object.entries(counts)
    .filter(([, n]) => n > 0)
    .map(([k, n]) => (CREATED_WORDS[k] ? plural(n, ...CREATED_WORDS[k]) : `${n} ${k}`))
    .join(', ');
}

function sum(rec: Record<string, number> | undefined): number {
  return Object.values(rec || {}).reduce((a, b) => a + b, 0);
}

/** Short tags for what Norm did along the way. */
export function activityTags(row: ActivityRow): { text: string; title?: string; tone?: 'warn' | 'ok' }[] {
  const a = row.activity || {};
  const tags: { text: string; title?: string; tone?: 'warn' | 'ok' }[] = [];
  if (a.layout) {
    tags.push({
      text: a.reread ? `Re-read with “${a.layout}”` : `Read with “${a.layout}”`,
      title: a.reread
        ? "The name printed on the invoice pointed to a different supplier's layout, so Norm read it again with that one"
        : 'The supplier layout Norm used to read the invoice',
    });
  } else if (a.feed) {
    tags.push({ text: 'From the supplier feed', title: 'No copy to read: the lines came from the supplier feed' });
  }
  if (a.studied) tags.push({ text: 'Studied the supplier', title: 'Norm had no layout for this supplier and wrote one first' });
  if (a.lines) {
    const matched = sum(a.matched);
    const how = Object.entries(a.matched || {}).map(([k, n]) => `${n} by ${k}`).join(', ');
    tags.push({
      text: `${matched} of ${plural(a.lines, 'line', 'lines')} matched`,
      title: how ? `Matched to stock items ${how}` : undefined,
      tone: matched < a.lines ? 'warn' : undefined,
    });
  }
  const units = Object.entries(a.units || {});
  if (units.length) {
    tags.push({ text: `Units from ${units.map(([k, n]) => `${k} (${n})`).join(', ')}` });
  }
  if (a.totals === 'worked out') tags.push({ text: 'Norm worked out the totals', title: "The figures didn't add up at first; Norm's model found the lines that were wrong" });
  if (a.totals === "didn't add up") tags.push({ text: "Totals didn't add up", tone: 'warn' });
  const created: Record<string, number> = {};
  for (const [k, names] of Object.entries(a.created || {})) created[k] = names.length;
  const made = createdWords(created);
  if (made) {
    const names = Object.values(a.created || {}).flat();
    tags.push({ text: `Created ${made}`, title: names.length ? names.join(', ') : undefined });
  }
  const suggested = createdWords(a.suggested_creates || {});
  if (suggested) {
    tags.push({
      text: `Norm suggested creating ${suggested}`,
      title: "This row is from before 19 Oct 2026, which didn't record whether each was new in Loaded",
    });
  }
  // Only on a receive: a deleted draft's suggestions were never applied.
  const changes = Object.entries(row.changes || {}).filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]);
  if (changes.length && row.outcome === 'received') {
    const said = changes.map(([k, n]) => (CHANGE_WORDS[k] ? plural(n, ...CHANGE_WORDS[k]) : `${n} ${k.replace(/_/g, ' ')}`));
    tags.push({
      text: `Norm suggested: ${said.slice(0, 3).join(', ')}${said.length > 3 ? ', …' : ''}`,
      title: `${said.join(', ')}${row.by === 'norm' ? '' : ' (the person accepted or dismissed each)'}`,
    });
  }
  if (row.unit_check?.flagged) {
    const { flagged, open } = row.unit_check;
    tags.push({
      text: `Unit check: ${plural(flagged, 'line', 'lines')} flagged${open ? `, ${open} to look at` : ', all decided'}`,
      tone: open ? 'warn' : 'ok',
    });
  }
  return tags;
}

/** YYYY-MM-DD for a local date. */
export function isoDay(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** The last `days` days, ending today. */
export function lastDays(days: number, today: Date = new Date()): { start: string; end: string } {
  const start = new Date(today);
  start.setDate(start.getDate() - (days - 1));
  return { start: isoDay(start), end: isoDay(today) };
}

/** The filters a link carries (the unit check's email): from, to, venues. */
export function filtersFromLink(view: string | undefined): { start?: string; end?: string; venues: string[] } {
  const p = new URLSearchParams(view || '');
  const day = (v: string | null) => (v && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : undefined);
  const start = day(p.get('from'));
  const end = day(p.get('to'));
  return {
    ...(start && end && start <= end ? { start, end } : {}),
    venues: (p.get('venues') || '').split(',').filter(Boolean),
  };
}
