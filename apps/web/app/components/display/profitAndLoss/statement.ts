/**
 * Profit & Loss screen — the pure logic (no React): months for the picker,
 * the three Xero documents merged into one statement (this month, the month
 * before, the same month last year), and the plain-words "what moved" lines.
 *
 * The document is what `xero.get_profit_and_loss` returns for ONE month
 * (verified live 23 Oct 2026 against Glass Goose):
 *   { organisation, currency, from, to, window, columns: ['30 Sep 26'],
 *     totals: { income, cost_of_sales, gross_profit, operating_expenses,
 *               net_profit }            // each a list, one value per column
 *     sections: [{ title, rows: [{ name, values }], total }] }
 *
 * Why three separate calls and not Xero's own comparison columns: Xero's
 * MONTH comparison shifts the END date by a month, so September compared
 * with August reads 1–30 August (31 Aug dropped) and February against
 * January reads 1–28 January — $30k of income went missing on the live test.
 * One plain month per call is always a whole month.
 *
 * Sections: Xero's report is flat. The organisation's layout groups
 * ('Cost of Goods', 'Salaries & Wages' under cost of sales; 'Property Costs'
 * under operating expenses) arrive in report order, but the tool drops the
 * empty group headers ('Less Cost of Sales') that said which headline each
 * belongs to. So the screen walks the sections in order and closes each
 * headline (income → cost of sales → operating expenses) when the running
 * sum reaches Xero's own total for it; what follows is "other" (Xero's
 * non-operating expenses). That follows the organisation's layout, which
 * account types would not (this organisation's wages are cost of sales,
 * its income tax sits below operating profit).
 */

export type HeadlineKey = 'income' | 'cost_of_sales' | 'gross_profit' | 'operating_expenses' | 'net_profit';
export type BlockKey = 'income' | 'cost_of_sales' | 'other_income' | 'operating_expenses' | 'other';

export interface PlRow { name: string; values: (number | null)[] }
export interface PlSection {
  title: string | null;
  rows: PlRow[];
  total: (number | null)[] | null;
  /** Not sent today; honoured if the tool ever names a section's headline. */
  group?: string | null;
}
export interface PlDoc {
  organisation?: string | null;
  currency?: string | null;
  titles?: string[] | null;
  from?: string | null;
  to?: string | null;
  window?: string | null;
  columns?: string[];
  totals?: Partial<Record<HeadlineKey, (number | null)[]>>;
  sections?: PlSection[];
  venue_id?: string;
}

/** The tool's document, wherever the caller put it (body.data or body). */
export function extractPlDoc(data: unknown): PlDoc | null {
  if (!data || typeof data !== 'object') return null;
  const d = data as Record<string, unknown>;
  const isDoc = (o: Record<string, unknown>) => (o.totals && typeof o.totals === 'object') || Array.isArray(o.sections);
  if (isDoc(d)) return d as PlDoc;
  const inner = d.data;
  if (inner && typeof inner === 'object' && isDoc(inner as Record<string, unknown>)) return inner as PlDoc;
  return null;
}

// ---------------------------------------------------------------------------
// Months
// ---------------------------------------------------------------------------

export const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

export interface Month { year: number; month: number } // month 1–12

export function shiftMonth({ year, month }: Month, delta: number): Month {
  const i = year * 12 + (month - 1) + delta;
  return { year: Math.floor(i / 12), month: (i % 12) + 1 };
}

/** "September 2026" — also the plain-English period the tool resolves. */
export function monthLabel(m: Month): string {
  return `${MONTH_NAMES[m.month - 1]} ${m.year}`;
}

/** "Sep 2026" — column headings. */
export function monthShort(m: Month): string {
  return `${MONTH_NAMES[m.month - 1].slice(0, 3)} ${m.year}`;
}

export function monthKey(m: Month): string {
  return `${m.year}-${String(m.month).padStart(2, '0')}`;
}

export function parseMonthKey(key: string | null | undefined): Month | null {
  const m = /^(\d{4})-(\d{2})$/.exec(String(key || ''));
  if (!m) return null;
  const month = Number(m[2]);
  return month >= 1 && month <= 12 ? { year: Number(m[1]), month } : null;
}

/** The last full month before `today` (the screen's default). */
export function lastFullMonth(today: Date): Month {
  return shiftMonth({ year: today.getFullYear(), month: today.getMonth() + 1 }, -1);
}

/** Whole months for the picker, newest first, starting at the last full month. */
export function monthOptions(today: Date, count = 24): Month[] {
  const first = lastFullMonth(today);
  return Array.from({ length: count }, (_, i) => shiftMonth(first, -i));
}

/** The month a document covers, when it is exactly one calendar month. */
export function monthOfDoc(doc: PlDoc | null): Month | null {
  const from = String(doc?.from || '');
  const to = String(doc?.to || '');
  const f = /^(\d{4})-(\d{2})-01$/.exec(from);
  if (!f) return null;
  const m: Month = { year: Number(f[1]), month: Number(f[2]) };
  const lastDay = new Date(Date.UTC(m.year, m.month, 0)).getUTCDate();
  return to === `${monthKey(m)}-${String(lastDay).padStart(2, '0')}` ? m : null;
}

/** The three periods the screen reads: the month, the month before, a year before. */
export function comparisonMonths(m: Month): [Month, Month, Month] {
  return [m, shiftMonth(m, -1), shiftMonth(m, -12)];
}

/** How the words name a comparison month: "August", or "December 2025" across a year. */
export function monthInWords(m: Month, relativeTo: Month): string {
  return m.year === relativeTo.year ? MONTH_NAMES[m.month - 1] : monthLabel(m);
}

// ---------------------------------------------------------------------------
// Sections → headline blocks
// ---------------------------------------------------------------------------

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const near = (a: number, b: number) => Math.abs(a - b) <= 0.05 + Math.abs(b) * 1e-6;

const OTHER_INCOME = /other income|other revenue|interest income/i;
const INCOME_WORDS = /income|revenue|gain/i;
/** Xero layout headers ("Less Operating Expenses") — their own lines sit at block level. */
export const LAYOUT_HEADER = /^(less|plus)\s/i;

const EXPLICIT: Record<string, BlockKey> = {
  income: 'income',
  cost_of_sales: 'cost_of_sales',
  other_income: 'other_income',
  operating_expenses: 'operating_expenses',
  other: 'other',
  other_expenses: 'other',
};

export function sectionValue(s: PlSection, col = 0): number {
  const t = num(s.total?.[col]);
  if (t !== null) return t;
  return (s.rows || []).reduce((sum, r) => sum + (num(r.values?.[col]) ?? 0), 0);
}

/** Xero's computed lines (Gross Profit, Operating Profit) come in untitled sections. */
function isComputedSection(s: PlSection): boolean {
  if ((s.title || '').trim()) return false;
  return (s.rows || []).every((r) => /profit/i.test(r.name));
}

/**
 * Which headline each section belongs to (null = skip: Xero's computed
 * lines), and whether the result adds up to Xero's own totals.
 */
export function assignBlocks(doc: PlDoc, col = 0): { blocks: (BlockKey | null)[]; sure: boolean } {
  const sections = doc.sections || [];
  const totals = doc.totals || {};
  const target = (k: HeadlineKey) => num(totals[k]?.[col]) ?? 0;
  const order: BlockKey[] = ['income', 'cost_of_sales', 'operating_expenses'];

  let b = 0;
  let acc = 0;
  let count = 0;
  const blocks: (BlockKey | null)[] = [];
  for (const s of sections) {
    const explicit = s.group ? EXPLICIT[String(s.group).toLowerCase()] : undefined;
    if (explicit) { blocks.push(explicit); continue; }
    if (isComputedSection(s)) { blocks.push(null); continue; }
    if (OTHER_INCOME.test(s.title || '')) { blocks.push('other_income'); continue; }
    // Close every headline whose total the sections so far already make up
    // (a headline Xero left out, total 0, closes before it takes anything).
    while (b < order.length) {
      const t = target(order[b] as HeadlineKey);
      if (near(acc, t) && (count > 0 || near(t, 0))) { b += 1; acc = 0; count = 0; } else break;
    }
    if (b >= order.length) {
      blocks.push(INCOME_WORDS.test(s.title || '') ? 'other_income' : 'other');
      continue;
    }
    blocks.push(order[b]);
    acc += sectionValue(s, col);
    count += 1;
  }

  const sums: Record<BlockKey, number> = { income: 0, cost_of_sales: 0, other_income: 0, operating_expenses: 0, other: 0 };
  sections.forEach((s, i) => { const k = blocks[i]; if (k) sums[k] += sectionValue(s, col); });
  const net = num(totals.net_profit?.[col]);
  const gp = num(totals.gross_profit?.[col]) ?? target('income') - target('cost_of_sales');
  const sure = near(sums.income, target('income'))
    && near(sums.cost_of_sales, target('cost_of_sales'))
    && near(sums.operating_expenses, target('operating_expenses'))
    && (net === null || near(gp + sums.other_income - sums.operating_expenses - sums.other, net));
  return { blocks, sure };
}

// ---------------------------------------------------------------------------
// The merged statement
// ---------------------------------------------------------------------------

/** One value per period: [this month, the month before, the same month last year]. */
export type Trio = [number | null, number | null, number | null];

export interface StatementLine { key: string; name: string; values: Trio }
export interface StatementGroup {
  key: string;
  title: string;
  /** A layout header or the block's own name: its lines show at block level. */
  plain: boolean;
  values: Trio;
  lines: StatementLine[];
}
export interface StatementBlock { key: BlockKey; label: string; groups: StatementGroup[]; total: Trio }
export interface Statement {
  headline: Record<HeadlineKey, Trio>;
  /** Gross profit after other income, less operating expenses — shown only with "other" lines. */
  operatingProfit: Trio;
  blocks: StatementBlock[];
  /** Which periods have a document. */
  present: [boolean, boolean, boolean];
  /** The groups add up to Xero's totals for every period read. */
  sure: boolean;
}

export const BLOCK_LABELS: Record<BlockKey, string> = {
  income: 'Income',
  cost_of_sales: 'Cost of sales',
  other_income: 'Other income',
  operating_expenses: 'Operating expenses',
  other: 'Other expenses',
};
const BLOCK_ORDER: BlockKey[] = ['income', 'cost_of_sales', 'other_income', 'operating_expenses', 'other'];
/** Income-like blocks raise profit when they rise; the rest lower it. */
export const INCOME_BLOCKS = new Set<BlockKey>(['income', 'other_income']);

/**
 * Merge keyed lists keeping each list's own order: a key first seen in a
 * later list goes straight after the key that precedes it there.
 */
export function mergeOrdered(lists: string[][]): string[] {
  const out: string[] = [];
  for (const list of lists) {
    let prev: string | null = null;
    list.forEach((key, i) => {
      if (!out.includes(key)) {
        if (prev !== null) {
          out.splice(out.indexOf(prev) + 1, 0, key);
        } else {
          // Leads its list: before the first of its followers already placed.
          const next = list.slice(i + 1).find((k) => out.includes(k));
          out.splice(next === undefined ? out.length : out.indexOf(next), 0, key);
        }
      }
      prev = key;
    });
  }
  return out;
}

const blank = (present: boolean[]): Trio => present.map((p) => (p ? 0 : null)) as Trio;

/** Merge the three months' documents (any may be null) into one statement. */
export function buildStatement(docs: [PlDoc | null, PlDoc | null, PlDoc | null]): Statement {
  const present = docs.map((d) => !!d) as [boolean, boolean, boolean];
  const headline = {} as Record<HeadlineKey, Trio>;
  const keys: HeadlineKey[] = ['income', 'cost_of_sales', 'gross_profit', 'operating_expenses', 'net_profit'];
  for (const k of keys) {
    headline[k] = docs.map((d) => (d ? num(d.totals?.[k]?.[0]) : null)) as Trio;
  }

  // Per document: section → block, groups keyed by title.
  let sure = true;
  const groupBlock = new Map<string, BlockKey>();
  const groupTitle = new Map<string, string>();
  const groupOrder: string[][] = [];
  const groupValues = new Map<string, Trio>();
  const lineOrder = new Map<string, string[][]>();
  const lineValues = new Map<string, Map<string, Trio>>();
  const lineName = new Map<string, string>();

  docs.forEach((doc, p) => {
    if (!doc) { groupOrder.push([]); return; }
    const { blocks, sure: ok } = assignBlocks(doc, 0);
    if (!ok) sure = false;
    const seen = new Map<string, number>();
    const order: string[] = [];
    (doc.sections || []).forEach((s, i) => {
      const block = blocks[i];
      if (!block) return;
      const title = (s.title || '').trim() || BLOCK_LABELS[block];
      const n = seen.get(title) ?? 0;
      seen.set(title, n + 1);
      const key = n ? `${title}#${n}` : title;
      order.push(key);
      if (!groupBlock.has(key)) { groupBlock.set(key, block); groupTitle.set(key, title); }
      const gv = groupValues.get(key) ?? blank(present);
      gv[p] = (gv[p] ?? 0) + sectionValue(s, 0);
      groupValues.set(key, gv);
      const lo = lineOrder.get(key) ?? [];
      const names: string[] = [];
      const lv = lineValues.get(key) ?? new Map<string, Trio>();
      for (const r of s.rows || []) {
        const lk = r.name;
        names.push(lk);
        lineName.set(`${key}|${lk}`, r.name);
        const v = lv.get(lk) ?? blank(present);
        v[p] = (v[p] ?? 0) + (num(r.values?.[0]) ?? 0);
        lv.set(lk, v);
      }
      lo[p] = names;
      lineOrder.set(key, lo);
      lineValues.set(key, lv);
    });
    groupOrder.push(order);
  });

  const allGroups = mergeOrdered(groupOrder);
  const blocks: StatementBlock[] = [];
  for (const bk of BLOCK_ORDER) {
    const groups: StatementGroup[] = allGroups
      .filter((g) => groupBlock.get(g) === bk)
      .map((g) => {
        const title = groupTitle.get(g) || g;
        const lv = lineValues.get(g) ?? new Map<string, Trio>();
        const lines = mergeOrdered((lineOrder.get(g) ?? []).filter(Boolean)).map((lk) => ({
          key: `${g}|${lk}`,
          name: lineName.get(`${g}|${lk}`) || lk,
          values: lv.get(lk) ?? blank(present),
        }));
        return {
          key: g,
          title,
          plain: LAYOUT_HEADER.test(title) || title.toLowerCase() === BLOCK_LABELS[bk].toLowerCase(),
          values: groupValues.get(g) ?? blank(present),
          lines,
        };
      });
    if (!groups.length) continue;
    const fromHeadline: Partial<Record<BlockKey, HeadlineKey>> = {
      income: 'income', cost_of_sales: 'cost_of_sales', operating_expenses: 'operating_expenses',
    };
    const hk = fromHeadline[bk];
    const summed = [0, 1, 2].map((p) => (present[p] ? groups.reduce((s, g) => s + (g.values[p] ?? 0), 0) : null)) as Trio;
    const total = hk ? (headline[hk].map((v, p) => v ?? summed[p]) as Trio) : summed;
    blocks.push({ key: bk, label: BLOCK_LABELS[bk], groups, total });
  }

  // Fill headline gaps Xero leaves out (no cost of sales → no total line).
  for (let p = 0; p < 3; p += 1) {
    if (!present[p]) continue;
    for (const k of ['income', 'cost_of_sales', 'operating_expenses'] as const) {
      if (headline[k][p] === null) headline[k][p] = blocks.find((b) => b.key === k)?.total[p] ?? 0;
    }
    if (headline.gross_profit[p] === null) {
      headline.gross_profit[p] = (headline.income[p] ?? 0) - (headline.cost_of_sales[p] ?? 0);
    }
  }
  const otherIncome = blocks.find((b) => b.key === 'other_income')?.total;
  const operatingProfit = [0, 1, 2].map((p) => (present[p]
    ? (headline.gross_profit[p] ?? 0) + (otherIncome?.[p] ?? 0) - (headline.operating_expenses[p] ?? 0)
    : null)) as Trio;
  const other = blocks.find((b) => b.key === 'other')?.total;
  for (let p = 0; p < 3; p += 1) {
    if (present[p] && headline.net_profit[p] === null) {
      headline.net_profit[p] = (operatingProfit[p] ?? 0) - (other?.[p] ?? 0);
    }
  }

  return { headline, operatingProfit, blocks, present, sure };
}

/** value / income as a percentage, or null when there is no income. */
export function shareOfIncome(value: number | null, income: number | null): number | null {
  if (value === null || income === null || Math.abs(income) < 0.005) return null;
  return (value / income) * 100;
}

export function change(now: number | null, then: number | null): { amount: number; pct: number | null } | null {
  if (now === null || then === null) return null;
  const amount = now - then;
  const pct = Math.abs(then) < 0.005 ? null : (amount / Math.abs(then)) * 100;
  return { amount, pct };
}

/** True when a period's document carries no figures at all (nothing posted). */
export function periodEmpty(st: Statement, p: 0 | 1 | 2): boolean {
  if (!st.present[p]) return true;
  const h = st.headline;
  const headlines = [h.income[p], h.cost_of_sales[p], h.operating_expenses[p], h.net_profit[p]];
  const lines = st.blocks.some((b) => b.groups.some((g) => g.lines.some((l) => Math.abs(l.values[p] ?? 0) >= 0.005)));
  return !lines && headlines.every((v) => Math.abs(v ?? 0) < 0.005);
}

// ---------------------------------------------------------------------------
// Words
// ---------------------------------------------------------------------------

/** "$1,234" / "-$1,234" — whole dollars. */
export function money0(n: number | null | undefined): string {
  if (typeof n !== 'number' || !Number.isFinite(n)) return '—';
  const r = Math.round(n);
  const s = `$${Math.abs(r).toLocaleString('en-NZ')}`;
  return r < 0 ? `-${s}` : s;
}

/** "+$1,234" / "-$1,234" / "$0". */
export function signedMoney0(n: number | null | undefined): string {
  if (typeof n !== 'number' || !Number.isFinite(n)) return '—';
  const r = Math.round(n);
  if (r === 0) return '$0';
  return `${r > 0 ? '+' : '-'}$${Math.abs(r).toLocaleString('en-NZ')}`;
}

export function pct1(n: number | null | undefined): string {
  if (typeof n !== 'number' || !Number.isFinite(n)) return '—';
  return `${n.toFixed(1)}%`;
}

export interface Mover { name: string; block: BlockKey; amount: number }

/** The lines that moved most between this month and period `p`, biggest first. */
export function biggestMoves(st: Statement, p: 1 | 2, limit = 3): Mover[] {
  const income = Math.max(Math.abs(st.headline.income[0] ?? 0), Math.abs(st.headline.income[p] ?? 0));
  const floor = Math.max(100, income * 0.005);
  const moves: Mover[] = [];
  for (const b of st.blocks) {
    for (const g of b.groups) {
      for (const l of g.lines) {
        const c = change(l.values[0], l.values[p]);
        if (c && Math.abs(c.amount) >= floor) moves.push({ name: l.name, block: b.key, amount: c.amount });
      }
    }
  }
  return moves.sort((a, b) => Math.abs(b.amount) - Math.abs(a.amount)).slice(0, limit);
}

function listInWords(items: string[]): string {
  if (items.length <= 1) return items.join('');
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
}

/**
 * One short plain-words line comparing this month with period `p`
 * ("August", "September 2025"). Null when that period wasn't read.
 */
export function movementLine(st: Statement, p: 1 | 2, label: string): string | null {
  if (!st.present[p]) return null;
  if (periodEmpty(st, p)) return `Xero has nothing posted for ${label}, so there is nothing to compare.`;
  if (periodEmpty(st, 0)) return null;

  const parts: string[] = [];
  const net = st.headline.net_profit;
  const c = change(net[0], net[p]);
  if (c && net[0] !== null) {
    const what = net[0] < 0 ? `Net loss was ${money0(Math.abs(net[0]))}` : `Net profit was ${money0(net[0])}`;
    const r = Math.round(c.amount);
    const how = r === 0 ? `the same as ${label}` : `${money0(Math.abs(r))} ${r > 0 ? 'better' : 'worse'} than ${label}`;
    parts.push(`${what}, ${how}.`);
  }
  const gm0 = shareOfIncome(st.headline.gross_profit[0], st.headline.income[0]);
  const gmp = shareOfIncome(st.headline.gross_profit[p], st.headline.income[p]);
  if (gm0 !== null && gmp !== null) {
    const diff = gm0 - gmp;
    const how = pct1(gm0) === pct1(gmp)
      ? `the same as in ${label}`
      : Math.abs(diff) < 0.5
        ? `about the same as in ${label} (${pct1(gmp)})`
        : `${diff > 0 ? 'up' : 'down'} from ${pct1(gmp)}`;
    parts.push(`Gross profit was ${pct1(gm0)} of income, ${how}.`);
  }
  const moves = biggestMoves(st, p);
  if (moves.length) {
    const words = moves.map((m) => `${m.name} ${m.amount > 0 ? 'up' : 'down'} ${money0(Math.abs(m.amount))}`);
    parts.push(`Biggest changes: ${listInWords(words)}.`);
  }
  return parts.join(' ');
}
