'use client';

import { useMemo } from 'react';
import type { Shift } from './shared';
import { dateKey, formatTimeShort, calcHours, roleColor, staffName } from './shared';

const SIDEBAR_W = 120;
const HOUR_W = 80;
const ROW_H = 48;
const DAY_START_HOUR = 6;
const DAY_HOURS = 20; // 6am to 2am next day
const TIMELINE_W = DAY_HOURS * HOUR_W;

interface DayTimelineProps {
  shifts: Shift[];
  selectedDate: Date;
  editingShiftId: string | null;
  onSelectShift: (shift: Shift) => void;
  interactive: boolean;
}

interface StaffLane {
  id: string;
  name: string;
  role: string;
  shifts: Shift[];
}

function buildLanes(shifts: Shift[], date: Date): StaffLane[] {
  const dk = dateKey(date);
  const dayShifts = shifts.filter(s => !s.datestampDeleted && s.clockinTime && dateKey(new Date(s.clockinTime)) === dk);
  const laneMap = new Map<string, StaffLane>();

  for (const s of dayShifts) {
    const sid = s.staffMemberId || 'unknown';
    if (!laneMap.has(sid)) {
      laneMap.set(sid, { id: sid, name: staffName(s), role: s.roleName || '', shifts: [] });
    }
    laneMap.get(sid)!.shifts.push(s);
  }

  return Array.from(laneMap.values()).sort((a, b) => a.name.localeCompare(b.name));
}

function timeToOffset(time: string, date: Date): number {
  try {
    const d = new Date(time);
    if (isNaN(d.getTime())) return 0;
    // Hours since day start (6am)
    const hours = d.getHours() + d.getMinutes() / 60 - DAY_START_HOUR;
    // Handle wrapping past midnight
    const adjusted = hours < 0 ? hours + 24 : hours;
    return Math.max(0, Math.min(adjusted * HOUR_W, TIMELINE_W));
  } catch { return 0; }
}

function formatHourLabel(hour: number): string {
  if (hour === 0 || hour === 24) return '12am';
  if (hour === 12) return '12pm';
  if (hour < 12) return `${hour}am`;
  return `${hour - 12}pm`;
}

export default function DayTimeline({ shifts, selectedDate, editingShiftId, onSelectShift, interactive }: DayTimelineProps) {
  const lanes = useMemo(() => buildLanes(shifts, selectedDate), [shifts, selectedDate]);

  const hours: number[] = [];
  for (let h = DAY_START_HOUR; h < DAY_START_HOUR + DAY_HOURS; h++) {
    hours.push(h % 24);
  }

  if (lanes.length === 0) {
    return (
      <div style={{ padding: '2rem', textAlign: 'center', color: '#bbb', fontSize: '0.85rem' }}>
        No shifts scheduled for this day.
      </div>
    );
  }

  return (
    <div style={{ border: '1px solid #e2e8f0', borderRadius: 8, overflow: 'hidden' }}>
      <div style={{ display: 'flex' }}>
        {/* Sidebar */}
        <div style={{ width: SIDEBAR_W, flexShrink: 0, borderRight: '1px solid #e2e8f0', backgroundColor: '#fafafa' }}>
          {/* Header spacer */}
          <div style={{ height: 28, borderBottom: '2px solid #e2e8f0' }} />
          {/* Staff labels */}
          {lanes.map((lane, i) => (
            <div key={lane.id} style={{
              height: ROW_H, padding: '0.3rem 0.5rem',
              borderBottom: '1px solid #eee',
              display: 'flex', flexDirection: 'column', justifyContent: 'center',
              backgroundColor: i % 2 === 1 ? '#f5f5f5' : '#fafafa',
            }}>
              <div style={{ fontWeight: 500, color: '#333', fontSize: '0.78rem', lineHeight: 1.3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{lane.name}</div>
              <div style={{ fontSize: '0.68rem', color: '#999' }}>{lane.role}</div>
            </div>
          ))}
        </div>

        {/* Timeline area */}
        <div style={{ flex: 1, overflowX: 'auto', minWidth: 0 }}>
          {/* Hour headers */}
          <div style={{ width: TIMELINE_W, height: 28, position: 'relative', borderBottom: '2px solid #e2e8f0' }}>
            {hours.map((h, i) => (
              <div key={i} style={{
                position: 'absolute', left: i * HOUR_W, width: HOUR_W,
                height: '100%', display: 'flex', alignItems: 'center',
                borderRight: '1px solid #f0f0f0',
                paddingLeft: 4, fontSize: '0.68rem', color: '#999', fontWeight: 500,
              }}>
                {formatHourLabel(h)}
              </div>
            ))}
          </div>

          {/* Rows */}
          {lanes.map((lane, li) => (
            <div key={lane.id} style={{
              width: TIMELINE_W, height: ROW_H, position: 'relative',
              borderBottom: '1px solid #eee',
              backgroundColor: li % 2 === 1 ? '#fafafa' : '#fff',
            }}>
              {/* Grid lines */}
              {hours.map((_, i) => (
                <div key={i} style={{
                  position: 'absolute', left: i * HOUR_W, top: 0, bottom: 0,
                  borderRight: '1px solid #f5f5f5',
                }} />
              ))}

              {/* Shift bars */}
              {lane.shifts.map(shift => {
                const left = timeToOffset(shift.clockinTime || '', selectedDate);
                const right = timeToOffset(shift.clockoutTime || '', selectedDate);
                const width = Math.max(right - left, 20);
                const hrs = calcHours(shift.clockinTime, shift.clockoutTime);
                const color = roleColor(shift.roleId || '');
                const isSelected = editingShiftId === shift.id;

                return (
                  <div
                    key={shift.id}
                    onClick={() => interactive && onSelectShift(isSelected ? { id: undefined } as Shift : shift)}
                    style={{
                      position: 'absolute',
                      left, width, top: 6, bottom: 6,
                      backgroundColor: color,
                      borderRadius: 4,
                      cursor: interactive ? 'pointer' : 'default',
                      display: 'flex', alignItems: 'center',
                      padding: '0 6px',
                      overflow: 'hidden',
                      boxShadow: isSelected ? '0 0 0 2px #2563eb' : '0 1px 2px rgba(0,0,0,0.1)',
                      transition: 'box-shadow 0.15s',
                    }}
                  >
                    <span style={{
                      fontSize: '0.72rem', fontWeight: 600, color: '#fff',
                      whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                      textShadow: '0 1px 2px rgba(0,0,0,0.2)',
                    }}>
                      {formatTimeShort(shift.clockinTime)}–{formatTimeShort(shift.clockoutTime)}
                      {hrs > 0 && width > 100 && (
                        <span style={{ fontWeight: 400, marginLeft: 4, opacity: 0.85 }}>
                          ({hrs.toFixed(1)}h)
                        </span>
                      )}
                    </span>
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
