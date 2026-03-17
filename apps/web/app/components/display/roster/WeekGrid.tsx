'use client';

import type { Shift, StaffRow } from './shared';
import { dateKey, formatTimeShort, calcHours, roleColor, DAY_NAMES } from './shared';

interface WeekGridProps {
  staffRows: StaffRow[];
  days: Date[];
  editingShiftId: string | null;
  onSelectShift: (shift: Shift) => void;
  onSelectDay: (date: Date) => void;
  interactive: boolean;
}

export default function WeekGrid({ staffRows, days, editingShiftId, onSelectShift, onSelectDay, interactive }: WeekGridProps) {
  return (
    <div style={{ overflowX: 'auto', border: '1px solid #e2e8f0', borderRadius: 8 }}>
      <table style={{
        width: '100%', borderCollapse: 'collapse', fontSize: '0.78rem',
        tableLayout: 'fixed', minWidth: 700,
      }}>
        <colgroup>
          <col style={{ width: 120 }} />
          {days.map((_, i) => <col key={i} />)}
        </colgroup>
        <thead>
          <tr>
            <th style={{
              padding: '0.5rem 0.6rem', textAlign: 'left',
              borderBottom: '2px solid #e2e8f0', borderRight: '1px solid #e2e8f0',
              fontWeight: 600, color: '#555', backgroundColor: '#fafafa',
              position: 'sticky', left: 0, zIndex: 1,
            }}>Staff</th>
            {days.map((d, i) => {
              const isToday = dateKey(d) === dateKey(new Date());
              return (
                <th
                  key={i}
                  onClick={() => onSelectDay(d)}
                  style={{
                    padding: '0.5rem 0.4rem', textAlign: 'center',
                    borderBottom: '2px solid #e2e8f0',
                    borderRight: i < days.length - 1 ? '1px solid #f0f0f0' : 'none',
                    fontWeight: 600, color: isToday ? '#2563eb' : '#555',
                    backgroundColor: isToday ? '#eff6ff' : '#fafafa',
                    whiteSpace: 'nowrap',
                    cursor: 'pointer',
                  }}
                >
                  <div>{DAY_NAMES[d.getDay()]}</div>
                  <div style={{ fontSize: '0.85em', fontWeight: 500, color: isToday ? '#2563eb' : '#999' }}>{d.getDate()}</div>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {staffRows.map((row, ri) => (
            <tr key={row.id} style={{ backgroundColor: ri % 2 === 1 ? '#fafafa' : '#fff' }}>
              <td style={{
                padding: '0.4rem 0.6rem',
                borderBottom: '1px solid #eee', borderRight: '1px solid #e2e8f0',
                position: 'sticky', left: 0, zIndex: 1,
                backgroundColor: ri % 2 === 1 ? '#fafafa' : '#fff',
              }}>
                <div style={{ fontWeight: 500, color: '#333', lineHeight: 1.3 }}>{row.name}</div>
                <div style={{ fontSize: '0.7rem', color: '#999' }}>{row.role}</div>
              </td>
              {days.map((d, di) => {
                const dk = dateKey(d);
                const dayShifts = row.shiftsByDay.get(dk) || [];
                const isToday = dk === dateKey(new Date());
                return (
                  <td key={di} style={{
                    padding: '0.25rem 0.2rem',
                    borderBottom: '1px solid #eee',
                    borderRight: di < days.length - 1 ? '1px solid #f0f0f0' : 'none',
                    verticalAlign: 'top',
                    backgroundColor: isToday ? '#f8fbff' : undefined,
                  }}>
                    {dayShifts.map(shift => {
                      const hrs = calcHours(shift.clockinTime, shift.clockoutTime);
                      const color = roleColor(shift.roleId || '');
                      const isSelected = editingShiftId === shift.id;
                      return (
                        <div
                          key={shift.id}
                          onClick={() => onSelectShift(isSelected ? { id: undefined } as Shift : shift)}
                          style={{
                            display: 'flex', alignItems: 'stretch', gap: 0,
                            marginBottom: 2, borderRadius: 4, overflow: 'hidden',
                            cursor: interactive ? 'pointer' : 'default',
                            border: isSelected ? '1px solid #2563eb' : '1px solid #e2e8f0',
                            backgroundColor: isSelected ? '#eff6ff' : '#fff',
                            transition: 'border-color 0.15s',
                          }}
                        >
                          <div style={{ width: 3, backgroundColor: color, flexShrink: 0 }} />
                          <div style={{ padding: '3px 5px', flex: 1, minWidth: 0 }}>
                            <div style={{ fontWeight: 500, color: '#333', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                              {formatTimeShort(shift.clockinTime)}–{formatTimeShort(shift.clockoutTime)}
                            </div>
                            {hrs > 0 && <div style={{ fontSize: '0.68rem', color: '#888' }}>{hrs.toFixed(1)}h</div>}
                          </div>
                        </div>
                      );
                    })}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
