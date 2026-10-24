/**
 * The pay run's pure logic (the Payroll page's Hours, Leave, Gratuities,
 * Entertainers and Ace files tabs): the tab an old Payrun link opens, the
 * "worked at this venue" view (the old Timeclock screen), the people
 * filters, and the totals rows. Nothing here computes what the server computes — a totals
 * row adds the rows it is given, for the foot of the table only.
 */
import type { Fact, StaffRow } from '../types';

/** A Payroll page tab an old Payrun `?view=` lands on. */
export type LegacyPayrunTab = 'hours' | 'checks' | 'leave' | 'gratuities' | 'entertainers' | 'ace-files';

/**
 * An old Payrun link's `?view=` → the Payroll page's tab, plus the `view`
 * the Hours tab keeps for its chip or the shifts view (timeclock, security,
 * salaried). Staff, nothing or anything unknown → Hours; Hygiene → Checks;
 * Exports → Ace files. Used by the old-link table (pages/pageAliases.ts).
 */
export function legacyPayrunView(view: string | null | undefined): { tab: LegacyPayrunTab; view?: string } {
  switch (view) {
    case 'timeclock':
    case 'security':
    case 'salaried':
      return { tab: 'hours', view };
    case 'leave':
    case 'gratuities':
    case 'entertainers':
      return { tab: view };
    case 'hygiene':
      return { tab: 'checks' };
    case 'exports':
      return { tab: 'ace-files' };
    default:
      return { tab: 'hours' };
  }
}

export type PeopleChip = 'staff' | 'security' | 'salaried';

/** The Hours tab's `?view=` → its people chip (Security, Salaried, else Staff). */
export function chipFrom(view: string | null | undefined): PeopleChip {
  if (view === 'security' || view === 'salaried') return view;
  return 'staff';
}

export function toNumber(v: number | string | null | undefined): number {
  if (v === null || v === undefined || v === '') return 0;
  const n = typeof v === 'string' ? Number(v) : v;
  return Number.isFinite(n) ? n : 0;
}

/** Shifts that happened at this venue, not leave (the Timeclock view). */
export function workedHere(facts: Fact[]): Fact[] {
  return facts.filter((f) => !f.is_from_other_venue && !f.leave_request_id && !isLeaveType(f.shift_type));
}

/** Shifts the entity pays for that happened somewhere else. */
export function workedElsewhere(facts: Fact[]): Fact[] {
  return facts.filter((f) => f.is_from_other_venue && !f.leave_request_id);
}

/** Loaded says "Salary" / "HourlyRate"; compare without caring about case. */
export function isSalary(remunerationType: string | null | undefined): boolean {
  return (remunerationType ?? '').toLowerCase() === 'salary';
}

/** Loaded's remuneration type as people say it: "HourlyRate" → "Hourly". */
export function remunerationLabel(remunerationType: string | null | undefined): string {
  if (!remunerationType) return '—';
  if (isSalary(remunerationType)) return 'Salary';
  return remunerationType.replace(/Rate$/i, '').replace(/([a-z])([A-Z])/g, '$1 $2');
}

export function isLeaveType(shiftType: string | null | undefined): boolean {
  return !!shiftType && /leave/i.test(shiftType);
}

/** A person's shifts, oldest first. */
export function shiftsOf(facts: Fact[], staffMemberId: string): Fact[] {
  return facts
    .filter((f) => f.staff_member_id === staffMemberId)
    .sort((a, b) => String(a.clockin_at ?? '').localeCompare(String(b.clockin_at ?? '')));
}

export interface StaffTotals {
  people: number;
  shifts: number;
  hours: number;
  cost_cents: number;
  ph_hours: number;
  ph_cost_cents: number;
  other_venue_hours: number;
}

export function staffTotals(rows: StaffRow[]): StaffTotals {
  return rows.reduce<StaffTotals>((t, r) => ({
    people: t.people + 1,
    shifts: t.shifts + (r.shifts || 0),
    hours: t.hours + toNumber(r.hours),
    cost_cents: t.cost_cents + (r.cost_cents || 0),
    ph_hours: t.ph_hours + toNumber(r.ph_hours),
    ph_cost_cents: t.ph_cost_cents + (r.ph_cost_cents || 0),
    other_venue_hours: t.other_venue_hours + toNumber(r.other_venue_hours),
  }), { people: 0, shifts: 0, hours: 0, cost_cents: 0, ph_hours: 0, ph_cost_cents: 0, other_venue_hours: 0 });
}

export function factTotals(facts: Fact[]): { shifts: number; hours: number; cost_cents: number; ph_cost_cents: number } {
  return facts.reduce((t, f) => ({
    shifts: t.shifts + 1,
    hours: t.hours + toNumber(f.hours),
    cost_cents: t.cost_cents + (f.cost_cents || 0),
    ph_cost_cents: t.ph_cost_cents + (f.ph_cost_cents || 0),
  }), { shifts: 0, hours: 0, cost_cents: 0, ph_cost_cents: 0 });
}

/** Rows filtered by a typed name or pay code. */
export function matchesSearch(text: string, ...fields: (string | null | undefined)[]): boolean {
  const q = text.trim().toLowerCase();
  if (!q) return true;
  return fields.some((f) => (f ?? '').toLowerCase().includes(q));
}

export type ExportKind = 'import' | 'leave' | 'gratuities' | 'entertainers';

export const EXPORT_KINDS: { kind: ExportKind; filename: string; label: string }[] = [
  { kind: 'import', filename: 'IMPORT.TXT', label: 'Wages (IMPORT.TXT)' },
  { kind: 'leave', filename: 'IMPORT_LEAVE.TXT', label: 'Leave (IMPORT_LEAVE.TXT)' },
  { kind: 'gratuities', filename: 'GRATUITIES.TXT', label: 'Gratuities (GRATUITIES.TXT)' },
  { kind: 'entertainers', filename: 'IMPORT_ENTERTAINERS.TXT', label: 'Entertainers (IMPORT_ENTERTAINERS.TXT)' },
];

export const FALLBACK_FILES = ['IMPORT.TXT', 'IMPORT_LEAVE.TXT', 'GRATUITIES.TXT', 'IMPORT_ENTERTAINERS.TXT', 'REKEY.csv'];

/** Plain words for a week's status. */
export function statusLabel(status: string | null | undefined): { label: string; tone: 'neutral' | 'ok' | 'warn' | 'info' | 'accent' } {
  switch (status) {
    case 'snapshotted': return { label: 'Snapshot taken', tone: 'info' };
    case 'close_ready': return { label: 'Ready to close', tone: 'accent' };
    case 'exported': return { label: 'Exported', tone: 'warn' };
    case 'closed': return { label: 'Closed', tone: 'ok' };
    default: return { label: 'Open', tone: 'neutral' };
  }
}

/** "Monday job" / "Close" / "Taken by hand" / "From Orbit". */
export function sourceLabel(source: string | null | undefined): string {
  switch (source) {
    case 'monday': return 'Monday snapshot';
    case 'close': return 'Close snapshot';
    case 'manual': return 'Taken by hand';
    case 'orbit': return 'From Orbit';
    default: return source || '—';
  }
}
