// Shared types and helpers for roster components

import type { VenueTimePrefs } from '../../../lib/rosterTime';
import { companyDayDate } from '../../../lib/rosterTime';

export { tzOffsetOf, localTzOffset, formatWithOffset } from '../../../lib/datetime';

export interface ShiftBreak {
  id?: string;
  breakStart: string;
  breakEnd: string;
  paid: boolean;
  deletedAt?: string | null;
}

/**
 * A rostered shift.
 *
 * The field names are LoadedHub's, because LoadedHub is currently the only
 * connector that serves rosters. If a second one arrives, normalise it into
 * this shape at the connector seam (the `get_roster` tool's `response_transform`
 * in the config DB) rather than teaching this component a second vocabulary —
 * see the roster plan. The index signature is what makes that possible without
 * a migration: extra connector fields ride along untouched.
 *
 * Semantics worth knowing (they match how Loaded's own rostering models them):
 *  - no `staffMemberId` means an **open / unassigned** shift, not bad data;
 *  - `datestampDeleted` is a **soft delete** — filter on it, don't assume the
 *    row is gone (`shiftsForDay` in ./grid does this);
 *  - times are ISO strings carrying the **venue's** offset. Never parse them
 *    with the browser's clock; go through `app/lib/rosterTime.ts`;
 *  - a shift may finish after midnight, so the day it *belongs* to is its
 *    business day, not the calendar date of `clockoutTime`.
 */
export interface Shift {
  id?: string;
  rosterId?: string;
  /** Absent for an open/unassigned shift. */
  staffMemberId?: string;
  staffMemberFirstName?: string;
  staffMemberLastName?: string;
  roleId?: string;
  roleName?: string;
  /** ISO 8601 with the venue's UTC offset. */
  clockinTime?: string;
  /** ISO 8601 with the venue's UTC offset; may fall on the next calendar day. */
  clockoutTime?: string;
  breaks?: ShiftBreak[];
  /** Soft delete — set means removed. */
  datestampDeleted?: string | null;
  [key: string]: unknown;
}

export interface StaffRow {
  id: string;
  name: string;
  firstName: string;
  lastName: string;
  role: string;
  shiftsByDay: Map<string, Shift[]>;
}

export interface ShiftFormData {
  staff_member_id: string;
  role_id: string;
  clockin_time: string;
  clockout_time: string;
  /**
   * The shift's breaks. Edited here and sent back on every write — core-api
   * replaces the whole shift, so anything omitted is deleted.
   */
  breaks?: ShiftBreak[];
}

export interface RosterMeta {
  startDate: Date | null;
  endDate: Date | null;
  totalHours: number;
  rosterId: string;
  /**
   * Publish/lock state as nullable timestamps — how core-api models it. No
   * datestampPublished means the roster is still a draft; a locked one should
   * not be edited. Both already arrive on get_roster; we just never read them.
   */
  publishedAt: string | null;
  lockedAt: string | null;
}

export interface DragData {
  shift: Shift;
  sourceStaffId: string;
}

export interface DropData {
  staffId: string;
  staffFirstName: string;
  staffLastName: string;
}

export const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

/** Lane id + label for shifts nobody is assigned to yet. */
export const OPEN_ROW_ID = '__open__';
export const OPEN_ROW_LABEL = 'Open shifts';

/**
 * Unwrap the `{window, data, venue_id}` envelope the *_for_period consolidators
 * return, so callers see the connector payload itself.
 *
 * Without this, `extractShifts`'s "first array of objects" fallback matched the
 * envelope's own `data` — a one-element list of *rosters* — and the grid showed
 * a single unassigned shift for a week that actually had 115. Unwrap by
 * structure (an envelope has a `window` alongside `data`), not by key name
 * alone, since a connector payload may legitimately have a `data` key.
 */
function unwrapEnvelope(data: Record<string, unknown>): Record<string, unknown> {
  let cur = data;
  for (let i = 0; i < 4; i++) {
    if (!cur || Array.isArray(cur) || typeof cur !== 'object') return cur;
    const inner = cur.data;
    if (!('window' in cur) || inner == null) return cur;
    cur = inner as Record<string, unknown>;
  }
  return cur;
}

export function extractShifts(raw: Record<string, unknown>): Shift[] {
  const data = unwrapEnvelope(raw);
  if (Array.isArray(data)) {
    if (data.length > 0 && Array.isArray((data[0] as Record<string, unknown>)?.rosteredShifts)) {
      return (data as Record<string, unknown>[]).flatMap(
        r => ((r.rosteredShifts as Shift[]) || [])
      );
    }
    return data as Shift[];
  }
  if (Array.isArray(data.rosteredShifts)) return data.rosteredShifts as Shift[];
  for (const val of Object.values(data)) {
    if (Array.isArray(val) && val.length > 0 && typeof val[0] === 'object') {
      return val as Shift[];
    }
  }
  return [];
}

export function extractRosterMeta(raw: Record<string, unknown>): RosterMeta {
  const data = unwrapEnvelope(raw);
  let roster: Record<string, unknown> | null = null;
  if (Array.isArray(data) && data.length > 0) {
    roster = data[0] as Record<string, unknown>;
  } else if (data.startDateTime) {
    roster = data;
  }
  if (!roster) {
    return { startDate: null, endDate: null, totalHours: 0, rosterId: '', publishedAt: null, lockedAt: null };
  }
  return {
    startDate: roster.startDateTime ? new Date(String(roster.startDateTime)) : null,
    endDate: roster.endDateTime ? new Date(String(roster.endDateTime)) : null,
    totalHours: Number(roster.totalHours || 0),
    rosterId: String(roster.id || ''),
    publishedAt: roster.datestampPublished ? String(roster.datestampPublished) : null,
    lockedAt: roster.datestampLocked ? String(roster.datestampLocked) : null,
  };
}

/**
 * YYYY-MM-DD for a Date's *calendar* fields.
 *
 * Only safe for dates that already represent a business day (the week columns).
 * To ask which business day an *instant* belongs to, use companyDayDate — a
 * shift finishing at 2am belongs to the day before, and reading it with the
 * browser's clock puts it in the wrong column (or drops it from the grid).
 */
export function dateKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/**
 * The seven business days a roster covers, in the *venue's* clock.
 *
 * A roster's `startDateTime` is an instant: LoadedHub returns Mon 20 Jul 07:00
 * NZ as `2026-07-19T19:00:00+00:00`. Reading its calendar fields with the
 * browser's clock (what getWeekDays does) gives Sunday the 19th for any viewer
 * west of the venue — so the grid rendered Sun 19 – Sat 25 for a Mon 20 – Sun 26
 * roster, showing a phantom empty Sunday and **silently dropping every shift on
 * the last day**. Ask which business day the instant belongs to instead.
 *
 * The returned Dates are anchored at local noon, so `dateKey` reads back exactly
 * the intended calendar date in any timezone and far from any DST boundary.
 */
export function venueWeekDays(start: Date | null, prefs: VenueTimePrefs): Date[] {
  if (!start) return [];
  const [y, m, d] = companyDayDate(start, prefs).split('-').map(Number);
  return Array.from({ length: 7 }, (_, i) => new Date(y, m - 1, d + i, 12));
}

export function getWeekDays(start: Date | null, end: Date | null): Date[] {
  if (!start) return [];
  const days: Date[] = [];
  const d = new Date(start);
  const limit = end ? new Date(end) : new Date(start.getTime() + 7 * 86400000);
  while (d < limit && days.length < 7) {
    days.push(new Date(d));
    d.setDate(d.getDate() + 1);
  }
  while (days.length < 7) {
    const last = days[days.length - 1];
    const next = new Date(last);
    next.setDate(next.getDate() + 1);
    days.push(next);
  }
  return days;
}

export function calcHours(start: unknown, end: unknown): number {
  if (!start || !end) return 0;
  try {
    const s = new Date(String(start)).getTime();
    const e = new Date(String(end)).getTime();
    if (isNaN(s) || isNaN(e)) return 0;
    return Math.max(0, (e - s) / (1000 * 60 * 60));
  } catch { return 0; }
}

export function roleColor(roleId: string): string {
  let hash = 0;
  for (let i = 0; i < roleId.length; i++) {
    hash = roleId.charCodeAt(i) + ((hash << 5) - hash);
  }
  return `hsl(${Math.abs(hash) % 360}, 55%, 65%)`;
}

export function staffName(shift: Shift): string {
  const first = shift.staffMemberFirstName || '';
  const last = shift.staffMemberLastName || '';
  if (first && last) return `${first} ${last.charAt(0)}.`;
  return first || last || String(shift.staffMemberId || '').slice(0, 8);
}

/**
 * Group shifts into per-staff rows, keyed by *business* day — so a shift that
 * runs past midnight stays on the day it started rather than jumping a column.
 */
export function buildStaffRows(shifts: Shift[], days: Date[], prefs: VenueTimePrefs): StaffRow[] {
  const dayKeys = new Set(days.map(d => dateKey(d)));
  const staffMap = new Map<string, StaffRow>();

  for (const shift of shifts) {
    if (shift.datestampDeleted) continue;
    const open = !shift.staffMemberId;
    const sid = open ? OPEN_ROW_ID : shift.staffMemberId!;
    if (!staffMap.has(sid)) {
      staffMap.set(sid, {
        id: sid,
        name: open ? OPEN_ROW_LABEL : staffName(shift),
        firstName: open ? '' : (shift.staffMemberFirstName || ''),
        lastName: open ? '' : (shift.staffMemberLastName || ''),
        role: open ? '' : (shift.roleName || ''),
        shiftsByDay: new Map(),
      });
    }
    const row = staffMap.get(sid)!;
    if (shift.clockinTime) {
      const dk = companyDayDate(shift.clockinTime, prefs);
      if (dayKeys.has(dk)) {
        if (!row.shiftsByDay.has(dk)) row.shiftsByDay.set(dk, []);
        row.shiftsByDay.get(dk)!.push(shift);
      }
    }
    if (shift.roleName && !row.role) row.role = shift.roleName;
  }

  return Array.from(staffMap.values()).sort((a, b) => a.name.localeCompare(b.name));
}

export const formInputStyle: React.CSSProperties = {
  width: '100%',
  padding: '4px 8px',
  border: '1px solid var(--line)',
  borderRadius: 4,
  fontSize: '0.82rem',
  fontFamily: 'inherit',
  boxSizing: 'border-box' as const,
};
