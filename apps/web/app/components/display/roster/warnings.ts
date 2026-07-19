/**
 * Roster problems, as pure functions.
 *
 * Modelled on the shape Loaded use (`selectors/warnings.ts`): each rule returns
 * `Warning { severity, code, message, scope }`, so the same result can badge a
 * cell in the grid *or* be narrated by the agent ("Sam is double-booked on
 * Friday") without either owning the logic.
 *
 * Only rules computable from the roster itself live here. Loaded's other rules
 * need leave, cost or coverage data — those can be added once the roster loads
 * `get_leave_requests` / `get_unavailability`, which now exist as connector
 * actions.
 */

import type { Shift, StaffRow } from './shared';
import { calcHours, dateKey } from './shared';
import type { VenueTimePrefs } from '../../../lib/rosterTime';
import { companyDayDate, formatClock } from '../../../lib/rosterTime';

export type WarningSeverity = 'warn' | 'error';

export type WarningCode =
  | 'overlappingShifts'
  | 'openShift'
  | 'missingBreak'
  | 'overHours'
  | 'emptyDay'
  | 'leaveConflict'
  | 'unavailableConflict';

export interface Warning {
  id: string;
  severity: WarningSeverity;
  code: WarningCode;
  message: string;
  scope: { shiftId?: string; staffId?: string; dayDate?: string };
}

export interface WarningOptions {
  /** A shift longer than this (hours) is expected to carry a break. */
  breakAfterHours?: number;
  /** More than this many hours for one person in the week is flagged. */
  maxWeeklyHours?: number;
}

const DEFAULTS: Required<WarningOptions> = {
  breakAfterHours: 5,
  maxWeeklyHours: 40,
};

const staffLabel = (s: Shift) =>
  [s.staffMemberFirstName, s.staffMemberLastName].filter(Boolean).join(' ') || 'Someone';

function activeBreakMinutes(shift: Shift): number {
  return (shift.breaks || [])
    .filter(b => !b.deletedAt && b.breakStart && b.breakEnd)
    .reduce((sum, b) => sum + calcHours(b.breakStart, b.breakEnd) * 60, 0);
}

/**
 * Two shifts for the same person that overlap in time.
 *
 * Compared as instants, so a shift running past midnight is handled and the
 * viewer's timezone is irrelevant.
 */
export function overlappingShifts(shifts: Shift[]): Warning[] {
  const byStaff = new Map<string, Shift[]>();
  for (const s of shifts) {
    if (s.datestampDeleted || !s.staffMemberId || !s.clockinTime || !s.clockoutTime) continue;
    const list = byStaff.get(s.staffMemberId) || [];
    list.push(s);
    byStaff.set(s.staffMemberId, list);
  }

  const out: Warning[] = [];
  for (const [staffId, list] of byStaff) {
    const sorted = [...list].sort(
      (a, b) => new Date(a.clockinTime!).getTime() - new Date(b.clockinTime!).getTime(),
    );
    for (let i = 1; i < sorted.length; i++) {
      const prev = sorted[i - 1];
      const cur = sorted[i];
      if (new Date(cur.clockinTime!).getTime() < new Date(prev.clockoutTime!).getTime()) {
        out.push({
          id: `overlap:${cur.id ?? i}`,
          severity: 'error',
          code: 'overlappingShifts',
          message: `${staffLabel(cur)} is double-booked — two shifts overlap`,
          scope: { shiftId: cur.id, staffId },
        });
      }
    }
  }
  return out;
}

/** Shifts nobody is assigned to yet. */
export function openShifts(shifts: Shift[]): Warning[] {
  return shifts
    .filter(s => !s.datestampDeleted && !s.staffMemberId)
    .map((s, i) => ({
      id: `open:${s.id ?? i}`,
      severity: 'warn' as WarningSeverity,
      code: 'openShift' as WarningCode,
      message: 'Shift has nobody assigned',
      scope: { shiftId: s.id },
    }));
}

/** Long shifts with no break recorded. */
export function missingBreaks(shifts: Shift[], opts: WarningOptions = {}): Warning[] {
  const limit = opts.breakAfterHours ?? DEFAULTS.breakAfterHours;
  return shifts
    .filter(s => !s.datestampDeleted && s.clockinTime && s.clockoutTime)
    .filter(s => calcHours(s.clockinTime, s.clockoutTime) > limit && activeBreakMinutes(s) === 0)
    .map((s, i) => ({
      id: `break:${s.id ?? i}`,
      severity: 'warn' as WarningSeverity,
      code: 'missingBreak' as WarningCode,
      message: `${calcHours(s.clockinTime, s.clockoutTime).toFixed(1)}h shift with no break`,
      scope: { shiftId: s.id, staffId: s.staffMemberId },
    }));
}

/** Anyone rostered beyond the weekly limit. */
export function overHours(shifts: Shift[], opts: WarningOptions = {}): Warning[] {
  const limit = opts.maxWeeklyHours ?? DEFAULTS.maxWeeklyHours;
  const totals = new Map<string, { hours: number; label: string }>();
  for (const s of shifts) {
    if (s.datestampDeleted || !s.staffMemberId) continue;
    const cur = totals.get(s.staffMemberId) || { hours: 0, label: staffLabel(s) };
    cur.hours += calcHours(s.clockinTime, s.clockoutTime);
    totals.set(s.staffMemberId, cur);
  }
  return [...totals.entries()]
    .filter(([, v]) => v.hours > limit)
    .map(([staffId, v]) => ({
      id: `hours:${staffId}`,
      severity: 'warn' as WarningSeverity,
      code: 'overHours' as WarningCode,
      message: `${v.label} is rostered ${v.hours.toFixed(1)}h this week (over ${limit}h)`,
      scope: { staffId },
    }));
}

/** Days in the week with nobody rostered at all. */
export function emptyDays(shifts: Shift[], days: Date[], prefs: VenueTimePrefs): Warning[] {
  const covered = new Set(
    shifts
      .filter(s => !s.datestampDeleted && s.clockinTime)
      .map(s => companyDayDate(s.clockinTime as string, prefs)),
  );
  // The week columns are already business days (venueWeekDays built them from
  // one), so read their calendar date. Passing them back through companyDayDate
  // would re-interpret each as an instant and slide the whole week for venues
  // whose offset differs from the viewer's.
  return days
    .map(d => ({ d, key: dateKey(d) }))
    .filter(({ key }) => !covered.has(key))
    .map(({ d, key }) => ({
      id: `empty:${key}`,
      severity: 'error' as WarningSeverity,
      code: 'emptyDay' as WarningCode,
      message: `Nobody rostered on ${d.toLocaleDateString('en-NZ', { weekday: 'long' })}`,
      scope: { dayDate: key },
    }));
}

/** A leave request, as `leave_list` returns it. */
export interface LeaveRecord {
  id?: string;
  staffMemberId?: string;
  startDateTime?: string;
  endDateTime?: string;
  status?: string;
  reason?: string | null;
}

/** An unavailability record, as `unavailability_list` returns it. */
export interface UnavailabilityRecord {
  id?: string;
  staffMemberId?: string;
  /** "OneOff" or "Weekly". */
  type?: string;
  from?: string | null;
  to?: string | null;
  note?: string | null;
  status?: string;
  times?: { day?: string | null; start?: string; end?: string; from?: string; to?: string }[];
}

/** Cancelled/declined records shouldn't block anything. */
const isLive = (status?: string) => {
  const s = (status || '').toLowerCase();
  return s !== 'cancelled' && s !== 'declined' && s !== 'rejected';
};

/**
 * Shifts rostered over approved leave.
 *
 * Leave datetimes arrive without an offset (`2026-06-27T00:00:00`) and are
 * venue-local, so they're compared on the business-day the shift belongs to
 * rather than as instants — comparing a zone-less string to an offset-carrying
 * one would be wrong by up to a day.
 */
export function leaveConflicts(
  shifts: Shift[],
  leave: LeaveRecord[],
  prefs: VenueTimePrefs,
): Warning[] {
  const live = leave.filter(l => l.staffMemberId && l.startDateTime && isLive(l.status));
  if (!live.length) return [];

  const out: Warning[] = [];
  for (const s of shifts) {
    if (s.datestampDeleted || !s.staffMemberId || !s.clockinTime) continue;
    const day = companyDayDate(s.clockinTime as string, prefs);
    for (const l of live) {
      if (l.staffMemberId !== s.staffMemberId) continue;
      const from = (l.startDateTime || '').slice(0, 10);
      const to = (l.endDateTime || l.startDateTime || '').slice(0, 10);
      if (day >= from && day <= to) {
        out.push({
          id: `leave:${s.id ?? day}:${l.id ?? from}`,
          severity: 'error',
          code: 'leaveConflict',
          message: `${staffLabel(s)} is on leave${l.reason ? ` (${l.reason})` : ''}`,
          scope: { shiftId: s.id, staffId: s.staffMemberId, dayDate: day },
        });
        break;
      }
    }
  }
  return out;
}

const WEEKDAYS = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];

/**
 * Shifts rostered when someone said they're unavailable.
 *
 * Two shapes: a OneOff record covers a date range outright, while a Weekly one
 * recurs on its named weekday for as long as the record is valid (`to` may be
 * null, meaning open-ended).
 */
export function unavailabilityConflicts(
  shifts: Shift[],
  records: UnavailabilityRecord[],
  prefs: VenueTimePrefs,
): Warning[] {
  const live = records.filter(u => u.staffMemberId && isLive(u.status));
  if (!live.length) return [];

  const out: Warning[] = [];
  for (const s of shifts) {
    if (s.datestampDeleted || !s.staffMemberId || !s.clockinTime) continue;
    const day = companyDayDate(s.clockinTime as string, prefs);
    const weekday = WEEKDAYS[new Date(`${day}T12:00:00`).getDay()];

    for (const u of live) {
      if (u.staffMemberId !== s.staffMemberId) continue;
      const from = (u.from || '').slice(0, 10);
      const to = (u.to || '').slice(0, 10);
      if (from && day < from) continue;
      if (to && day > to) continue;

      const weekly = (u.type || '').toLowerCase() === 'weekly';
      if (weekly) {
        const named = (u.times || [])
          .map(t => (t.day || '').toLowerCase())
          .filter(Boolean);
        // A Weekly record with no named days applies to the whole window.
        if (named.length && !named.includes(weekday)) continue;
      }

      out.push({
        id: `unavail:${s.id ?? day}:${u.id ?? from}`,
        severity: 'warn',
        code: 'unavailableConflict',
        message: `${staffLabel(s)} is unavailable${u.note ? ` (${u.note})` : ''}`,
        scope: { shiftId: s.id, staffId: s.staffMemberId, dayDate: day },
      });
      break;
    }
  }
  return out;
}

/** Optional context; each rule is skipped when its data isn't loaded. */
export interface WarningContext {
  leave?: LeaveRecord[];
  unavailability?: UnavailabilityRecord[];
}

/** Every rule, in the order a human would want to read them. */
export function computeWarnings(
  shifts: Shift[],
  days: Date[],
  prefs: VenueTimePrefs,
  opts: WarningOptions = {},
  ctx: WarningContext = {},
): Warning[] {
  return [
    ...overlappingShifts(shifts),
    ...leaveConflicts(shifts, ctx.leave ?? [], prefs),
    ...emptyDays(shifts, days, prefs),
    ...unavailabilityConflicts(shifts, ctx.unavailability ?? [], prefs),
    ...openShifts(shifts),
    ...missingBreaks(shifts, opts),
    ...overHours(shifts, opts),
  ];
}

/** Group warnings by the shift they point at, for badging the grid. */
export function warningsByShift(warnings: Warning[]): Map<string, Warning[]> {
  const out = new Map<string, Warning[]>();
  for (const w of warnings) {
    if (!w.scope.shiftId) continue;
    const list = out.get(w.scope.shiftId) || [];
    list.push(w);
    out.set(w.scope.shiftId, list);
  }
  return out;
}

/** One-line summary an agent can say out loud. */
export function summarise(warnings: Warning[]): string {
  if (!warnings.length) return 'No problems found';
  const errors = warnings.filter(w => w.severity === 'error').length;
  const warns = warnings.length - errors;
  const bits = [];
  if (errors) bits.push(`${errors} to fix`);
  if (warns) bits.push(`${warns} to review`);
  return bits.join(', ');
}

// Re-exported for callers that badge a cell and want the venue's clock.
export { formatClock };
export type { StaffRow };
