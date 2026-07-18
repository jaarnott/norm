// Shared types and helpers for roster components

export interface ShiftBreak {
  id?: string;
  breakStart: string;
  breakEnd: string;
  paid: boolean;
  deletedAt?: string | null;
}

export interface Shift {
  id?: string;
  rosterId?: string;
  staffMemberId?: string;
  staffMemberFirstName?: string;
  staffMemberLastName?: string;
  roleId?: string;
  roleName?: string;
  clockinTime?: string;
  clockoutTime?: string;
  breaks?: ShiftBreak[];
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
}

export interface RosterMeta {
  startDate: Date | null;
  endDate: Date | null;
  totalHours: number;
  rosterId: string;
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

export function extractShifts(data: Record<string, unknown>): Shift[] {
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

export function extractRosterMeta(data: Record<string, unknown>): RosterMeta {
  let roster: Record<string, unknown> | null = null;
  if (Array.isArray(data) && data.length > 0) {
    roster = data[0] as Record<string, unknown>;
  } else if (data.startDateTime) {
    roster = data;
  }
  if (!roster) return { startDate: null, endDate: null, totalHours: 0, rosterId: '' };
  return {
    startDate: roster.startDateTime ? new Date(String(roster.startDateTime)) : null,
    endDate: roster.endDateTime ? new Date(String(roster.endDateTime)) : null,
    totalHours: Number(roster.totalHours || 0),
    rosterId: String(roster.id || ''),
  };
}

export function dateKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
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

export function formatTimeShort(value: unknown): string {
  if (!value) return '';
  try {
    const d = new Date(String(value));
    if (isNaN(d.getTime())) return '';
    return `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`;
  } catch { return ''; }
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

export function buildStaffRows(shifts: Shift[], days: Date[]): StaffRow[] {
  const dayKeys = new Set(days.map(d => dateKey(d)));
  const staffMap = new Map<string, StaffRow>();

  for (const shift of shifts) {
    if (shift.datestampDeleted) continue;
    const sid = shift.staffMemberId || 'unknown';
    if (!staffMap.has(sid)) {
      staffMap.set(sid, { id: sid, name: staffName(shift), firstName: shift.staffMemberFirstName || '', lastName: shift.staffMemberLastName || '', role: shift.roleName || '', shiftsByDay: new Map() });
    }
    const row = staffMap.get(sid)!;
    if (shift.clockinTime) {
      const dk = dateKey(new Date(shift.clockinTime));
      if (dayKeys.has(dk)) {
        if (!row.shiftsByDay.has(dk)) row.shiftsByDay.set(dk, []);
        row.shiftsByDay.get(dk)!.push(shift);
      }
    }
    if (shift.roleName && !row.role) row.role = shift.roleName;
  }

  return Array.from(staffMap.values()).sort((a, b) => a.name.localeCompare(b.name));
}

export function snapToGrid(timeMs: number, intervalMinutes: number): number {
  const ms = intervalMinutes * 60 * 1000;
  return Math.round(timeMs / ms) * ms;
}

import { tzOffsetOf, localTzOffset } from '../../../lib/datetime';

export { tzOffsetOf, localTzOffset, formatWithOffset } from '../../../lib/datetime';

/**
 * The timezone the roster is expressed in.
 *
 * Times we send back must sit in the same zone as the ones the connector gave
 * us, so take the offset from the roster's own shifts. Only if the roster has
 * no timestamped shift yet (a brand new day) do we fall back to the browser's
 * offset for that date. Never hardcode one: the venue may not be in the
 * viewer's zone, and a fixed offset silently breaks across a DST boundary.
 */
export function rosterTzOffset(shifts: Shift[], fallbackDate: Date): string {
  for (const s of shifts) {
    const tz = tzOffsetOf(s.clockinTime) || tzOffsetOf(s.clockoutTime);
    if (tz) return tz;
  }
  return localTzOffset(fallbackDate);
}

export function offsetToTime(
  offset: number,
  selectedDate: Date,
  hourWidth: number,
  dayStartHour: number,
  tzOffset?: string,
): string {
  const hours = offset / hourWidth + dayStartHour;
  const totalMinutes = Math.round(hours * 60);
  const d = new Date(selectedDate);
  d.setHours(0, 0, 0, 0);
  d.setMinutes(totalMinutes);
  const pad = (n: number) => String(n).padStart(2, '0');
  const tz = tzOffset || localTzOffset(d);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:00${tz}`;
}

export const formInputStyle: React.CSSProperties = {
  width: '100%',
  padding: '4px 8px',
  border: '1px solid #ddd',
  borderRadius: 4,
  fontSize: '0.82rem',
  fontFamily: 'inherit',
  boxSizing: 'border-box' as const,
};
