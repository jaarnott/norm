'use client';

import type { DisplayBlockProps } from './DisplayBlockRenderer';

function formatTime(value: unknown): string {
  if (!value) return '';
  const str = String(value);
  // Handle ISO datetime strings
  try {
    const d = new Date(str);
    if (!isNaN(d.getTime())) {
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
  } catch { /* fall through */ }
  return str;
}

function calcDuration(start: unknown, end: unknown): string {
  if (!start || !end) return '';
  try {
    const s = new Date(String(start)).getTime();
    const e = new Date(String(end)).getTime();
    if (isNaN(s) || isNaN(e)) return '';
    const hours = (e - s) / (1000 * 60 * 60);
    if (hours <= 0 || hours > 24) return '';
    return `${hours.toFixed(1)}h`;
  } catch { return ''; }
}

function summarizeBreaks(breaks: unknown): string {
  if (!Array.isArray(breaks) || breaks.length === 0) return '';
  const totalMin = breaks.reduce((sum: number, b: Record<string, unknown>) => {
    const start = b.startTime || b.start;
    const end = b.endTime || b.end;
    if (start && end) {
      try {
        const diff = (new Date(String(end)).getTime() - new Date(String(start)).getTime()) / 60000;
        return sum + (diff > 0 ? diff : 0);
      } catch { return sum; }
    }
    const dur = Number(b.duration || b.durationMinutes || 0);
    return sum + dur;
  }, 0);
  return totalMin > 0 ? `${Math.round(totalMin)}m break` : '';
}

interface ShiftRow {
  name: string;
  role: string;
  clockIn: string;
  clockOut: string;
  duration: string;
  breaks: string;
}

function extractShifts(data: Record<string, unknown>): ShiftRow[] {
  // Find the array of shifts in the data
  let items: Record<string, unknown>[] = [];
  if (Array.isArray(data)) {
    // Check if this is an array of rosters containing rosteredShifts
    if (data.length > 0 && Array.isArray((data[0] as Record<string, unknown>)?.rosteredShifts)) {
      items = (data as Record<string, unknown>[]).flatMap(
        r => ((r as Record<string, unknown>).rosteredShifts as Record<string, unknown>[]) || []
      );
    } else {
      items = data as Record<string, unknown>[];
    }
  } else if (Array.isArray(data.rosteredShifts)) {
    items = data.rosteredShifts as Record<string, unknown>[];
  } else {
    for (const val of Object.values(data)) {
      if (Array.isArray(val) && val.length > 0 && typeof val[0] === 'object') {
        items = val;
        break;
      }
    }
  }

  return items.map(item => {
    const firstName = String(item.staffMemberFirstName || '');
    const lastName = String(item.staffMemberLastName || '');
    const name = (firstName || lastName) ? `${firstName} ${lastName}`.trim() : String(item.staffMemberName || item.staffName || item.employee || item.staffMemberId || '');
    return {
      name,
      role: String(item.roleName || item.role || item.roleId || ''),
      clockIn: formatTime(item.clockinTime || item.clockIn || item.startTime || item.start),
      clockOut: formatTime(item.clockoutTime || item.clockOut || item.endTime || item.end),
      duration: calcDuration(
        item.clockinTime || item.clockIn || item.startTime || item.start,
        item.clockoutTime || item.clockOut || item.endTime || item.end,
      ),
      breaks: summarizeBreaks(item.breaks),
    };
  });
}

export default function RosterTable({ data, props }: DisplayBlockProps) {
  const shifts = extractShifts(data);
  if (shifts.length === 0) return null;

  const title = (props?.title as string) || 'Roster';
  const rosterDate = String(data.date || data.searchDate || data.rosterDate || '');

  return (
    <div style={{ marginBottom: '0.75rem' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.5rem', marginBottom: 6 }}>
        <span className="n-eyebrow">{title}</span>
        {rosterDate && <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{rosterDate}</span>}
        <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
          {shifts.length} shift{shifts.length !== 1 ? 's' : ''}
        </span>
      </div>
      <div className="n-card" style={{ overflowX: 'auto' }}>
        <table className="n-table">
          <thead>
            <tr>
              {['Staff', 'Role', 'In', 'Out', 'Hrs', 'Breaks'].map(h => <th key={h} scope="col">{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {shifts.map((s, i) => (
              <tr key={i}>
                <td style={{ color: 'var(--text)', fontWeight: 500 }}>{s.name}</td>
                <td style={{ color: 'var(--text-soft)' }}>{s.role}</td>
                <td style={{ color: 'var(--text)' }}>{s.clockIn}</td>
                <td style={{ color: 'var(--text)' }}>{s.clockOut}</td>
                <td style={{ color: 'var(--text)' }}>{s.duration}</td>
                <td style={{ color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>{s.breaks}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
