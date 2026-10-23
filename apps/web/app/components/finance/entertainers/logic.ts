/**
 * Entertainers — pure logic the pages and the public forms share: status
 * chips, the recurrence preview, booked-hours maths, band share maths, and
 * the client-side format checks for a bank account and an IRD number
 * (mirrors of the server's, so a typo is caught before the code is spent).
 *
 * Plans (dates, times) are venue-local strings. Nothing here goes through
 * `new Date()` in the browser's zone: date arithmetic uses Date.UTC on the
 * calendar date alone.
 */
import type { BadgeTone } from '../../ui/Badge';
import type { BandMember, CheckStatus, ClosingManagerSource, EmailStatus, Lane, Performance, PerformanceStatus, Performer, PortalStatus } from './types';

// ── Performance status ───────────────────────────────────────────────────

export const STATUS_LABEL: Record<PerformanceStatus, string> = {
  scheduled: 'Scheduled',
  awaiting_approvals: 'Needs approval',
  awaiting_manager: 'Needs manager approval',
  awaiting_performer: 'Awaiting performer',
  mismatch: 'Times differ',
  ready_for_payment: 'Ready to pay',
  did_not_perform: 'Did not perform',
  paid: 'Paid',
};

/** warn = waiting on someone here · info = waiting on the performer · error =
 *  blocked · ok = done. */
export const STATUS_TONE: Record<PerformanceStatus, BadgeTone> = {
  scheduled: 'neutral',
  awaiting_approvals: 'warn',
  awaiting_manager: 'warn',
  awaiting_performer: 'info',
  mismatch: 'error',
  ready_for_payment: 'ok',
  did_not_perform: 'neutral',
  paid: 'ok',
};

export const STATUS_ORDER: PerformanceStatus[] = [
  'scheduled',
  'awaiting_approvals',
  'awaiting_manager',
  'awaiting_performer',
  'mismatch',
  'ready_for_payment',
  'did_not_perform',
  'paid',
];

export const LANE_LABEL: Record<Lane, string> = {
  payroll_employee: 'Payroll (employee)',
  payroll_contractor: 'Payroll (withholding tax)',
  invoice: 'Invoice',
  exemption: 'Exemption certificate',
};

export const LANE_SHORT: Record<Lane, string> = {
  payroll_employee: 'Payroll',
  payroll_contractor: 'WT',
  invoice: 'Invoice',
  exemption: 'Exempt',
};

export const CHECK_LABEL: Record<CheckStatus, string> = {
  pending: 'Checking',
  matched: 'Matched',
  query: 'Query',
  unreadable: 'Unreadable',
  waived: 'Waived',
};

export const CHECK_TONE: Record<CheckStatus, BadgeTone> = {
  pending: 'info',
  matched: 'ok',
  query: 'error',
  unreadable: 'warn',
  waived: 'neutral',
};

export const PORTAL_STATUS_LABEL: Record<PortalStatus, string> = {
  scheduled: 'Scheduled',
  awaiting_your_confirmation: 'Please confirm',
  confirmed: 'Confirmed',
  did_not_perform: 'Did not perform',
  paid: 'Paid',
  times_differ: 'Times differ',
};

export const PORTAL_STATUS_TONE: Record<PortalStatus, BadgeTone> = {
  scheduled: 'neutral',
  awaiting_your_confirmation: 'accent',
  confirmed: 'ok',
  did_not_perform: 'neutral',
  paid: 'ok',
  times_differ: 'warn',
};

export const EMAIL_STATUS_TONE: Record<EmailStatus, BadgeTone> = {
  sent: 'neutral',
  delivered: 'ok',
  bounced: 'error',
  complained: 'error',
  failed: 'error',
  suppressed: 'warn',
  queued: 'info',
};

export const EMAIL_KIND_LABEL: Record<string, string> = {
  ent_invite: 'Onboarding link',
  ent_invite_reminder: 'Onboarding reminder',
  ent_band_invite: 'Band member link',
  ent_band_reminder: 'Band member reminder',
  ent_code: 'One-time code',
  ent_details_changed: 'Details changed',
  ent_declaration_request: 'Declaration link',
  ent_confirm_request: 'Confirmation request',
  ent_confirm_reminder: 'Confirmation reminder',
  ent_mismatch: 'Times differ',
  ent_approved_amount: 'Approved amount',
  ent_invoice_query: 'Invoice query',
  ent_manager_approval: 'Approval request',
  ent_manager_digest: 'Venue digest',
  // Orbit's imported kinds
  invite: 'Onboarding link',
  invite_reminder: 'Onboarding reminder',
  confirm_request: 'Confirmation request',
  confirm_reminder: 'Confirmation reminder',
  manager_approval: 'Approval request',
  mismatch: 'Times differ',
};

export function emailKindLabel(kind: string): string {
  return EMAIL_KIND_LABEL[kind] ?? kind.replace(/^ent_/, '').replace(/_/g, ' ');
}

export const RECIPIENT_LABEL: Record<string, string> = {
  performer: 'Performer',
  band_member: 'Band member',
  closing_manager: 'Closing manager',
  venue_manager: 'Venue manager',
  finance: 'Finance',
};

/** The kinds a person may resend from the Emails page (the API refuses inside 24 h). */
export const RESENDABLE_KINDS = new Set(['ent_invite', 'ent_band_invite', 'ent_confirm_request', 'ent_manager_approval']);

export const CLOSING_SOURCE_LABEL: Record<ClosingManagerSource, string> = {
  clockin: 'clocked in',
  both: 'rostered and clocked in',
  rostered_only: 'rostered only',
  none: 'nobody found',
  orbit: 'from Orbit',
};

// ── Performer chips ──────────────────────────────────────────────────────

export interface Chip {
  key: string;
  label: string;
  tone: BadgeTone;
}

/** The register's status chips for one performer: the onboarding state
 *  first, then anything finance must act on. */
export function performerChips(p: Performer): Chip[] {
  const chips: Chip[] = [];
  if (!p.is_active) chips.push({ key: 'inactive', label: 'Inactive', tone: 'neutral' });
  if (p.onboarding_status === 'invited') chips.push({ key: 'invited', label: 'Invited', tone: 'info' });
  else if (p.onboarding_status === 'submitted') chips.push({ key: 'submitted', label: 'Awaiting acceptance', tone: 'accent' });
  else chips.push({ key: 'accepted', label: 'Accepted', tone: 'ok' });
  if (p.bank_changed_at && p.onboarding_status !== 'accepted') chips.push({ key: 'bank', label: 'Bank account changed', tone: 'warn' });
  if (p.needs_review) chips.push({ key: 'review', label: 'Needs review', tone: 'warn' });
  if (p.lane === 'payroll_contractor' && !p.has_declaration && p.is_active) {
    chips.push({ key: 'declaration', label: 'Missing declaration', tone: 'error' });
  }
  if (p.lane === 'exemption') {
    if (p.exemption_expired) chips.push({ key: 'expired', label: 'Exemption expired', tone: 'error' });
    else if (p.exemption_expiring) chips.push({ key: 'expiring', label: 'Exemption expiring', tone: 'warn' });
  }
  return chips;
}

export type RegisterTab = 'all' | 'awaiting' | 'declarations';

/** The register's tab filter: "awaiting acceptance" is the submitted list;
 *  "missing declarations" is active WT performers with no declaration. */
export function registerRows(rows: Performer[], tab: RegisterTab, q: string, lane: Lane | 'all', needsReview: boolean, includeInactive: boolean): Performer[] {
  const needle = q.trim().toLowerCase();
  return rows.filter((p) => {
    if (!includeInactive && !p.is_active) return false;
    if (tab === 'awaiting' && p.onboarding_status !== 'submitted') return false;
    if (tab === 'declarations' && !(p.lane === 'payroll_contractor' && !p.has_declaration)) return false;
    if (lane !== 'all' && p.lane !== lane) return false;
    if (needsReview && !p.needs_review) return false;
    if (needle) {
      const hay = [p.name, p.legal_name, p.trading_name, p.company_name, p.email, p.phone].filter(Boolean).join(' ').toLowerCase();
      if (!hay.includes(needle)) return false;
    }
    return true;
  });
}

// ── Dates and times ──────────────────────────────────────────────────────

const DAY_MS = 86_400_000;

function utc(iso: string): number {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  return Date.UTC(y, m - 1, d);
}

export function addDays(iso: string, n: number): string {
  return new Date(utc(iso) + n * DAY_MS).toISOString().slice(0, 10);
}

/** ISO weekday, 1 = Monday … 7 = Sunday (the API's `repeat.weekdays`). */
export function isoWeekday(iso: string): number {
  const d = new Date(utc(iso)).getUTCDay();
  return d === 0 ? 7 : d;
}

export const WEEKDAY_SHORT = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

/** "2026-10" → ["2026-10-01", "2026-10-31"]. */
export function monthBounds(ym: string): [string, string] {
  const [y, m] = ym.split('-').map(Number);
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
  return [`${ym}-01`, `${ym}-${String(last).padStart(2, '0')}`];
}

export function addMonths(ym: string, n: number): string {
  const [y, m] = ym.split('-').map(Number);
  const d = new Date(Date.UTC(y, m - 1 + n, 1));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
}

export function monthLabel(ym: string): string {
  const [y, m] = ym.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString('en-NZ', { timeZone: 'UTC', month: 'long', year: 'numeric' });
}

export function monthOf(iso: string): string {
  return iso.slice(0, 7);
}

/** "HH:MM" → minutes since midnight; null when it isn't a time. */
export function timeToMinutes(t: string | null | undefined): number | null {
  if (!t) return null;
  const m = /^(\d{1,2}):(\d{2})/.exec(t);
  if (!m) return null;
  const hh = Number(m[1]);
  const mm = Number(m[2]);
  if (hh > 23 || mm > 59) return null;
  return hh * 60 + mm;
}

/** An end at or before the start means the performance runs past midnight. */
export function endsNextDay(start: string, end: string): boolean {
  const s = timeToMinutes(start);
  const e = timeToMinutes(end);
  if (s === null || e === null) return false;
  return e <= s;
}

/** Booked clock minutes (no DST): what `agreed_cents` is worked out on. */
export function clockMinutes(start: string, end: string, nextDay: boolean): number | null {
  const s = timeToMinutes(start);
  const e = timeToMinutes(end);
  if (s === null || e === null) return null;
  let mins = e - s;
  if (nextDay || mins <= 0) mins += 24 * 60;
  return mins;
}

/** rate × booked clock hours, to the cent (the API's agreed amount). */
export function agreedCents(rateCents: number, start: string, end: string, nextDay: boolean): number | null {
  const mins = clockMinutes(start, end, nextDay);
  if (mins === null || !Number.isFinite(rateCents)) return null;
  return Math.round((rateCents * mins) / 60);
}

export function hoursLabel(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return '—';
  const h = minutes / 60;
  return `${Number.isInteger(h) ? h : h.toFixed(2).replace(/0$/, '')} h`;
}

/** "45.00" / "$45" → cents; null when blank or not a number. */
export function parseMoney(input: string): number | null {
  const cleaned = input.replace(/[^0-9.\-]/g, '');
  if (!cleaned) return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? Math.round(n * 100) : null;
}

export function centsToInput(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return '';
  return (cents / 100).toFixed(2).replace(/\.00$/, '');
}

// ── Recurrence ───────────────────────────────────────────────────────────

export interface Repeat {
  weekdays: number[];
  /** Ends on a date, or after a number of performances (the UI's choice; the
   *  API only takes `ends_on`, so a count is turned into its last date). */
  until?: string | null;
  count?: number | null;
}

/** The dates a weekly rule produces from the first date, at most `limit`.
 *  The first date always counts (the booking itself) even when its weekday
 *  isn't ticked — the API stores one series per ticked weekday and the
 *  performance as booked. */
export function recurrenceDates(first: string, repeat: Repeat, limit = 60): string[] {
  const days = Array.from(new Set(repeat.weekdays.filter((d) => d >= 1 && d <= 7))).sort();
  if (!days.length) return [first];
  const out: string[] = [first];
  const max = repeat.count && repeat.count > 0 ? Math.min(repeat.count, limit) : limit;
  let cursor = first;
  // Twelve months ahead is how far the API materialises.
  const horizon = addDays(first, 366);
  while (out.length < max) {
    cursor = addDays(cursor, 1);
    if (cursor > horizon) break;
    if (repeat.until && cursor > repeat.until) break;
    if (days.includes(isoWeekday(cursor)) && cursor !== first) out.push(cursor);
  }
  return out;
}

/** The `ends_on` to send for "after N performances". */
export function endsOnForCount(first: string, weekdays: number[], count: number): string | null {
  const dates = recurrenceDates(first, { weekdays, count }, Math.max(count, 1));
  return dates.length ? dates[dates.length - 1] : null;
}

// ── Band shares ──────────────────────────────────────────────────────────

export const SHARE_TOTAL_BP = 10_000;

/** Equal shares in basis points summing to exactly 10,000; the leftover
 *  points go to the first member (the plan's "leftover cent to the first"). */
export function equalShares(n: number): number[] {
  if (n <= 0) return [];
  const base = Math.floor(SHARE_TOTAL_BP / n);
  const out = new Array<number>(n).fill(base);
  out[0] += SHARE_TOTAL_BP - base * n;
  return out;
}

export function sharesTotal(shares: Record<string, number>): number {
  return Object.values(shares).reduce((s, v) => s + (Number.isFinite(v) ? v : 0), 0);
}

export function sharesComplete(shares: Record<string, number>): boolean {
  return sharesTotal(shares) === SHARE_TOTAL_BP;
}

/** "33.33" (percent) → basis points, to the whole point. */
export function percentToBp(input: string): number | null {
  const n = Number(input.replace(/[^0-9.]/g, ''));
  if (!input.trim() || !Number.isFinite(n) || n < 0 || n > 100) return null;
  return Math.round(n * 100);
}

export function bpToPercent(bp: number): string {
  return (bp / 100).toFixed(2).replace(/\.?0+$/, '');
}

/** How an approved amount splits by share: whole cents, the leftover cent(s)
 *  to the first member — the API's rule, shown as a preview. */
export function splitCents(amountCents: number, members: Pick<BandMember, 'id' | 'share_bp'>[]): Record<string, number> {
  const out: Record<string, number> = {};
  if (!members.length) return out;
  let allocated = 0;
  for (const m of members) {
    const part = Math.floor((amountCents * m.share_bp) / SHARE_TOTAL_BP);
    out[m.id] = part;
    allocated += part;
  }
  out[members[0].id] += amountCents - allocated;
  return out;
}

// ── Settings: the closing-manager roles ──────────────────────────────────

/** A role name as Loaded compares it: trimmed, inner spaces collapsed. */
export function cleanRole(name: string): string {
  return name.trim().split(/\s+/).filter(Boolean).join(' ');
}

/** Whether `roles` already holds `name` (Loaded compares role names case-blind). */
export function hasRole(roles: string[], name: string): boolean {
  const key = cleanRole(name).toLowerCase();
  return roles.some((r) => cleanRole(r).toLowerCase() === key);
}

/** Add a role (Loaded's spelling or one typed) or take it away — one of each,
 *  whatever its case. */
export function toggleRole(roles: string[], name: string, on: boolean): string[] {
  const clean = cleanRole(name);
  if (!clean) return roles;
  const key = clean.toLowerCase();
  const rest = roles.filter((r) => cleanRole(r).toLowerCase() !== key);
  return on ? [...rest, clean] : rest;
}

// ── Format checks (mirrors of the server's) ──────────────────────────────

// NZ bank account numbers — Inland Revenue's validation (Payroll Calculations
// and Business Rules Specification 2026–27, §7), the mirror of
// `nz_bank_account_valid` in app/services/entertainers/performers.py: change both.

/** Step 1: banks with their own algorithm; every other listed bank uses A,
 *  or B when the account base is 00990000 or more. */
const BANK_ALGORITHM: Record<string, string> = { '08': 'D', '25': 'F', '31': 'X' };
/** The branch numbers each bank IR lists uses (Payments NZ's register,
 *  widened to each bank's blocks — every branch in the 23 Oct 2026 register). */
const BANK_BRANCHES: Record<string, [number, number][]> = {
  '01': [[1, 999], [1100, 1199], [1800, 1899], [6150, 6150]],
  '02': [[1, 999], [1200, 1299], [2025, 2055]],
  '03': [[1, 999], [1300, 1399], [1500, 1599], [1700, 1799], [1900, 1999], [5000, 5099], [7350, 7399]],
  '04': [[2014, 2024]],
  '05': [[8884, 8889]],
  '06': [[1, 999], [1400, 1499]],
  '08': [[6500, 6599]],
  '10': [[5165, 5169]],
  '11': [[5000, 6499], [6600, 8999]],
  '12': [[3000, 3299], [3400, 3499], [3600, 3699]],
  '13': [[4900, 4999]],
  '14': [[4700, 4799]],
  '15': [[3900, 3999]],
  '16': [[4400, 4499]],
  '17': [[3300, 3399]],
  '18': [[3500, 3599]],
  '19': [[4600, 4649]],
  '20': [[4100, 4199]],
  '21': [[4800, 4899]],
  '22': [[4000, 4049]],
  '23': [[3700, 3799]],
  '24': [[4300, 4349]],
  '25': [[2500, 2599]],
  '27': [[3800, 3849]],
  '30': [[2900, 2949]],
  '31': [[2800, 2849]],
  '38': [[9000, 9499]],
  '88': [[8800, 8805]],
};
/** Step 2: weights over bank(2) branch(4) base(8) suffix(4), and the modulus. */
const BANK_WEIGHTS: Record<string, [number[], number]> = {
  A: [[0, 0, 6, 3, 7, 9, 0, 0, 10, 5, 8, 4, 2, 1, 0, 0, 0, 0], 11],
  B: [[0, 0, 0, 0, 0, 0, 0, 0, 10, 5, 8, 4, 2, 1, 0, 0, 0, 0], 11],
  C: [[3, 7, 0, 0, 0, 0, 9, 1, 10, 5, 3, 4, 2, 1, 0, 0, 0, 0], 11],
  D: [[0, 0, 0, 0, 0, 0, 0, 7, 6, 5, 4, 3, 2, 1, 0, 0, 0, 0], 11],
  E: [[0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 5, 4, 3, 2, 0, 0, 0, 1], 11],
  F: [[0, 0, 0, 0, 0, 0, 0, 1, 7, 3, 1, 7, 3, 1, 0, 0, 0, 0], 10],
  G: [[0, 0, 0, 0, 0, 0, 0, 1, 3, 7, 1, 3, 7, 1, 0, 3, 7, 1], 10],
  X: [Array(18).fill(0), 1],
};
/** How an unseparated run splits — bank, branch, base, suffix — in the order tried. */
const BANK_LAYOUTS: Record<number, number[][]> = {
  15: [[2, 4, 7, 2]],
  16: [[2, 4, 7, 3], [2, 4, 8, 2]],
  17: [[2, 4, 7, 4], [2, 4, 8, 3]],
  18: [[2, 4, 8, 4]],
};
export const BANK_SHAPE_MESSAGE = 'Enter the full account number: bank, branch, account and suffix.';
export const BANK_CHECK_MESSAGE = 'That bank account number doesn’t check out — please re-check it.';

/** IR's step 2 for one algorithm: the weighted sum of the padded digits
 *  divides by the modulus (E and G add a product's digits, twice). */
export function bankChecksumOk(algorithm: string, bank: string, branch: string, base: string, suffix: string): boolean {
  const [weights, modulus] = BANK_WEIGHTS[algorithm];
  const digits = bank.padStart(2, '0') + branch.padStart(4, '0') + base.padStart(8, '0') + suffix.padStart(4, '0');
  let total = 0;
  for (let i = 0; i < 18; i += 1) {
    let product = Number(digits[i]) * weights[i];
    if (algorithm === 'E' || algorithm === 'G') {
      for (let n = 0; n < 2; n += 1) product = String(product).split('').reduce((s, d) => s + Number(d), 0);
    }
    total += product;
  }
  return total % modulus === 0;
}

/** A New Zealand bank account passes Inland Revenue's validation: a bank IR
 *  lists, a branch that bank uses, and the bank's check digits. */
export function nzBankAccountValid(bank: string, branch: string, base: string, suffix: string): boolean {
  if (![bank, branch, base, suffix].every((p) => /^\d+$/.test(p))) return false;
  if (bank.length > 2 || branch.length > 4 || base.length > 8 || suffix.length > 4) return false;
  const id = bank.padStart(2, '0');
  const ranges = BANK_BRANCHES[id];
  if (!ranges) return false;
  const b = Number(branch);
  if (!ranges.some(([lo, hi]) => b >= lo && b <= hi)) return false;
  const algorithm = BANK_ALGORITHM[id] ?? (Number(base) < 990000 ? 'A' : 'B');
  return bankChecksumOk(algorithm, bank, branch, base, suffix);
}

function bankCandidates(input: string): string[][] {
  const groups = input.match(/\d+/g) ?? [];
  if (groups.length === 4) {
    const [bank, branch, base, suffix] = groups;
    const fits = bank.length === 2 && branch.length >= 3 && branch.length <= 4 && base.length >= 6 && base.length <= 8 && suffix.length >= 2 && suffix.length <= 4;
    return fits ? [groups] : [];
  }
  const digits = groups.join('');
  return (BANK_LAYOUTS[digits.length] ?? []).map((layout) => {
    const parts: string[] = [];
    let at = 0;
    for (const size of layout) {
      parts.push(digits.slice(at, at + size));
      at += size;
    }
    return parts;
  });
}

/** What is wrong with a typed bank account: `shape` (not bank, branch,
 *  account and suffix), `check` (the right shape, but it fails IR's
 *  validation), or null when it is fine. */
export function bankAccountProblem(input: string): 'shape' | 'check' | null {
  const candidates = bankCandidates(input);
  if (candidates.length === 0) return 'shape';
  return normaliseBankAccount(input) ? null : 'check';
}

/** A NZ bank account as "12-3456-7890123-00" (the suffix as typed, 2–3
 *  digits), or null unless it passes IR's validation. Spaces and dashes are
 *  optional; a run that could split two ways takes the split that checks out. */
export function normaliseBankAccount(input: string): string | null {
  for (const [bank, branch, rawBase, rawSuffix] of bankCandidates(input)) {
    if (Number(rawBase) === 0 || !nzBankAccountValid(bank, branch, rawBase, rawSuffix)) continue;
    const base = rawBase.length === 8 && rawBase.startsWith('0') ? rawBase.slice(1) : rawBase;
    const suffix = rawSuffix.length === 4 && rawSuffix.startsWith('0') ? rawSuffix.slice(1) : rawSuffix;
    return `${bank}-${branch.padStart(4, '0')}-${base.padStart(7, '0')}-${suffix.padStart(2, '0')}`;
  }
  return null;
}

/** Inland Revenue's check-digit rule for an IRD number (8 or 9 digits):
 *  weights 3 2 7 6 5 4 3 2 over the leading digits padded to eight, sum mod
 *  11; a remainder of 0 gives check digit 0, else 11 − remainder; a result
 *  of 10 is retried with weights 7 4 3 2 5 2 7 6, and 10 again is invalid.
 *  The number must sit between 10,000,000 and 150,000,000. */
export function validIrdNumber(input: string): boolean {
  const digits = input.replace(/\D/g, '');
  if (digits.length !== 8 && digits.length !== 9) return false;
  const value = Number(digits);
  if (value < 10_000_000 || value > 150_000_000) return false;
  const check = Number(digits[digits.length - 1]);
  const body = digits.slice(0, -1).padStart(8, '0').split('').map(Number);
  const weighted = (weights: number[]) => {
    const sum = body.reduce((s, d, i) => s + d * weights[i], 0);
    const rem = sum % 11;
    return rem === 0 ? 0 : 11 - rem;
  };
  let expected = weighted([3, 2, 7, 6, 5, 4, 3, 2]);
  if (expected === 10) expected = weighted([7, 4, 3, 2, 5, 2, 7, 6]);
  if (expected === 10) return false;
  return expected === check;
}

export function normaliseIrd(input: string): string {
  return input.replace(/\D/g, '');
}

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
export function validEmail(input: string): boolean {
  return EMAIL.test(input.trim());
}

/** A six-digit code typed with or without a space in the middle. */
export function normaliseCode(input: string): string {
  return input.replace(/\D/g, '').slice(0, 6);
}

// ── Payments ─────────────────────────────────────────────────────────────

/** The Monday on or before a date (pay weeks start Monday). */
export function weekStart(iso: string): string {
  return addDays(iso, 1 - isoWeekday(iso));
}

export function sumCents<T>(rows: T[], pick: (row: T) => number | null | undefined): number {
  return rows.reduce((s, r) => s + (pick(r) ?? 0), 0);
}

/** The performance's time range as a label: "8:00 pm – 1:00 am (next day)". */
export function performanceTimes(p: Pick<Performance, 'start_time' | 'end_time' | 'ends_next_day'>): { start: string; end: string; nextDay: boolean } {
  return { start: p.start_time, end: p.end_time, nextDay: p.ends_next_day };
}

/** The manager's times for the approval form: the actual ones once approved,
 *  else the booking as rostered. */
export function approvalDefaults(p: Performance): { start: string; end: string; nextDay: boolean } {
  return {
    start: p.actual_start_time ?? p.start_time,
    end: p.actual_end_time ?? p.end_time,
    nextDay: p.actual_ends_next_day ?? p.ends_next_day,
  };
}

/** Group a month's performances by date, in date then start-time order. */
export function groupByDate(rows: Performance[]): { date: string; rows: Performance[] }[] {
  const by = new Map<string, Performance[]>();
  for (const r of rows) {
    const list = by.get(r.performance_date) ?? [];
    list.push(r);
    by.set(r.performance_date, list);
  }
  return Array.from(by.entries())
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([date, list]) => ({ date, rows: list.slice().sort((a, b) => (a.start_time < b.start_time ? -1 : a.start_time > b.start_time ? 1 : 0)) }));
}

export function statusCounts(rows: Performance[]): Record<PerformanceStatus, number> {
  const out = Object.fromEntries(STATUS_ORDER.map((s) => [s, 0])) as Record<PerformanceStatus, number>;
  for (const r of rows) out[r.status] = (out[r.status] ?? 0) + 1;
  return out;
}
