'use client';

import { useMemo, useState, useRef, useCallback } from 'react';
import { useDraggable, useDroppable } from '@dnd-kit/core';
import type { Shift, ShiftBreak, DragData } from './shared';
import { dateKey, formatTimeShort, calcHours, roleColor, staffName, snapToGrid, offsetToTime } from './shared';

const SIDEBAR_W = 140;
const HOUR_W = 60;
const ROW_H = 56;
const DAY_START_HOUR = 6;
const DAY_HOURS = 20; // 6am to 2am next day
const TIMELINE_W = DAY_HOURS * HOUR_W;
const SNAP_MINUTES = 15;
const MIN_SHIFT_MS = 15 * 60 * 1000;
const DEFAULT_SHIFT_MS = 4 * 60 * 60 * 1000;
const DRAG_THRESHOLD = 5;

interface DayTimelineProps {
  shifts: Shift[];
  selectedDate: Date;
  editingShiftId: string | null;
  onSelectShift: (shift: Shift) => void;
  onResizeShift?: (shiftId: string, clockinTime: string, clockoutTime: string) => void;
  onCreateShift?: (staffId: string, clockinTime: string, clockoutTime: string) => void;
  interactive: boolean;
}

interface StaffLane {
  id: string;
  name: string;
  firstName: string;
  lastName: string;
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
      laneMap.set(sid, {
        id: sid,
        name: staffName(s),
        firstName: s.staffMemberFirstName || '',
        lastName: s.staffMemberLastName || '',
        role: s.roleName || '',
        shifts: [],
      });
    }
    laneMap.get(sid)!.shifts.push(s);
  }

  // Sort shifts within each lane by start time
  for (const lane of laneMap.values()) {
    lane.shifts.sort((a, b) => (a.clockinTime || '').localeCompare(b.clockinTime || ''));
  }

  return Array.from(laneMap.values()).sort((a, b) => a.name.localeCompare(b.name));
}

function timeToOffset(time: string): number {
  try {
    const d = new Date(time);
    if (isNaN(d.getTime())) return 0;
    const hours = d.getHours() + d.getMinutes() / 60 - DAY_START_HOUR;
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

function nowOffset(): number | null {
  const now = new Date();
  const hours = now.getHours() + now.getMinutes() / 60 - DAY_START_HOUR;
  const adjusted = hours < 0 ? hours + 24 : hours;
  if (adjusted < 0 || adjusted > DAY_HOURS) return null;
  return adjusted * HOUR_W;
}

// --- Shift bar with resize handles ---

function ShiftBar({ shift, staffId, interactive, isSelected, onSelect, onResize, selectedDate }: {
  shift: Shift;
  staffId: string;
  interactive: boolean;
  isSelected: boolean;
  onSelect: () => void;
  onResize?: (shiftId: string, clockinTime: string, clockoutTime: string) => void;
  selectedDate: Date;
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `shift-${shift.id}`,
    data: { shift, sourceStaffId: staffId } satisfies DragData,
    disabled: !interactive,
  });

  const [resizePreview, setResizePreview] = useState<{ left: number; width: number } | null>(null);
  const resizeRef = useRef<{ edge: 'left' | 'right'; startX: number; origLeft: number; origWidth: number } | null>(null);
  const latestPreview = useRef<{ left: number; width: number } | null>(null);

  const origLeft = timeToOffset(shift.clockinTime || '');
  const origRight = timeToOffset(shift.clockoutTime || '');
  const origWidth = Math.max(origRight - origLeft, 20);
  const hrs = calcHours(shift.clockinTime, shift.clockoutTime);
  const color = roleColor(shift.roleId || '');

  const displayLeft = resizePreview?.left ?? origLeft;
  const displayWidth = resizePreview?.width ?? origWidth;

  const handleResizeStart = useCallback((e: React.MouseEvent, edge: 'left' | 'right') => {
    if (!interactive || !onResize) return;
    e.stopPropagation();
    e.preventDefault();

    resizeRef.current = { edge, startX: e.clientX, origLeft, origWidth };

    const handleMove = (me: MouseEvent) => {
      if (!resizeRef.current) return;
      const delta = me.clientX - resizeRef.current.startX;
      let newLeft = resizeRef.current.origLeft;
      let newWidth = resizeRef.current.origWidth;

      if (resizeRef.current.edge === 'left') {
        newLeft = Math.max(0, resizeRef.current.origLeft + delta);
        newWidth = resizeRef.current.origWidth - delta;
        // Snap left edge
        const snappedHours = Math.round((newLeft / HOUR_W + DAY_START_HOUR) * (60 / SNAP_MINUTES)) / (60 / SNAP_MINUTES);
        newLeft = (snappedHours - DAY_START_HOUR) * HOUR_W;
        newWidth = (resizeRef.current.origLeft + resizeRef.current.origWidth) - newLeft;
      } else {
        newWidth = Math.max(HOUR_W / 4, resizeRef.current.origWidth + delta);
        // Snap right edge
        const rightHours = Math.round(((newLeft + newWidth) / HOUR_W + DAY_START_HOUR) * (60 / SNAP_MINUTES)) / (60 / SNAP_MINUTES);
        newWidth = (rightHours - DAY_START_HOUR) * HOUR_W - newLeft;
      }

      newWidth = Math.max(HOUR_W / 4, newWidth);
      newLeft = Math.max(0, Math.min(newLeft, TIMELINE_W - HOUR_W / 4));
      const preview = { left: newLeft, width: newWidth };
      latestPreview.current = preview;
      setResizePreview(preview);
    };

    const handleUp = () => {
      const preview = latestPreview.current;
      if (resizeRef.current && preview) {
        const newClockIn = offsetToTime(preview.left, selectedDate, HOUR_W, DAY_START_HOUR);
        const newClockOut = offsetToTime(preview.left + preview.width, selectedDate, HOUR_W, DAY_START_HOUR);
        onResize(shift.id || '', newClockIn, newClockOut);
      }
      resizeRef.current = null;
      latestPreview.current = null;
      setResizePreview(null);
      document.removeEventListener('mousemove', handleMove);
      document.removeEventListener('mouseup', handleUp);
    };

    document.addEventListener('mousemove', handleMove);
    document.addEventListener('mouseup', handleUp);
  }, [interactive, onResize, origLeft, origWidth, shift.id, selectedDate]);

  // Preview time labels during resize
  const previewStartTime = resizePreview
    ? formatTimeShort(offsetToTime(resizePreview.left, selectedDate, HOUR_W, DAY_START_HOUR))
    : formatTimeShort(shift.clockinTime);
  const previewEndTime = resizePreview
    ? formatTimeShort(offsetToTime(resizePreview.left + resizePreview.width, selectedDate, HOUR_W, DAY_START_HOUR))
    : formatTimeShort(shift.clockoutTime);

  return (
    <div
      ref={setNodeRef}
      style={{
        position: 'absolute',
        left: displayLeft, width: displayWidth, top: 4, bottom: 4,
        backgroundColor: color,
        borderRadius: 6,
        display: 'flex', alignItems: 'center',
        overflow: 'hidden',
        boxShadow: isSelected ? '0 0 0 2px #2563eb' : resizePreview ? '0 0 0 2px rgba(255,255,255,0.5)' : '0 1px 3px rgba(0,0,0,0.12)',
        opacity: isDragging ? 0.4 : 1,
        transition: resizePreview ? 'none' : 'box-shadow 0.15s, opacity 0.15s',
        zIndex: resizePreview ? 10 : 1,
      }}
    >
      {/* Break overlays */}
      {(() => {
        const activeBreaks = (shift.breaks || []).filter(
          (b: ShiftBreak) => !b.deletedAt && b.breakStart && b.breakEnd
        );
        if (activeBreaks.length === 0 || !shift.clockinTime || !shift.clockoutTime) return null;
        const shiftStartMs = new Date(shift.clockinTime).getTime();
        const shiftEndMs = new Date(shift.clockoutTime).getTime();
        const shiftDurationMs = shiftEndMs - shiftStartMs;
        if (shiftDurationMs <= 0) return null;
        return activeBreaks.map((b: ShiftBreak, idx: number) => {
          const bStartMs = new Date(b.breakStart).getTime();
          const bEndMs = new Date(b.breakEnd).getTime();
          const leftPct = Math.max(0, ((bStartMs - shiftStartMs) / shiftDurationMs) * 100);
          const widthPct = Math.min(100 - leftPct, ((bEndMs - bStartMs) / shiftDurationMs) * 100);
          if (widthPct <= 0) return null;
          return (
            <div key={b.id || idx} style={{
              position: 'absolute', top: 0, bottom: 0,
              left: `${leftPct}%`, width: `${widthPct}%`,
              backgroundColor: 'rgba(255,255,255,0.3)',
              backgroundImage: 'repeating-linear-gradient(135deg, transparent, transparent 2px, rgba(255,255,255,0.15) 2px, rgba(255,255,255,0.15) 4px)',
              pointerEvents: 'none', zIndex: 0,
            }} />
          );
        });
      })()}

      {/* Left resize handle */}
      {interactive && onResize && (
        <div
          onMouseDown={e => handleResizeStart(e, 'left')}
          style={{
            position: 'absolute', left: 0, top: 0, bottom: 0, width: 6,
            cursor: 'ew-resize', zIndex: 2,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
        >
          <div className="resize-grip" style={{
            width: 2, height: 16, borderRadius: 1,
            backgroundColor: 'rgba(255,255,255,0.4)',
            opacity: 0, transition: 'opacity 0.15s',
          }} />
        </div>
      )}

      {/* Main draggable content */}
      {(() => {
        const activeBreaks = (shift.breaks || []).filter(
          (b: ShiftBreak) => !b.deletedAt && b.breakStart && b.breakEnd
        );
        const totalBreakMins = activeBreaks.reduce((sum: number, b: ShiftBreak) => {
          return sum + Math.max(0, (new Date(b.breakEnd).getTime() - new Date(b.breakStart).getTime())) / 60000;
        }, 0);
        return (
          <div
            {...listeners}
            {...attributes}
            onClick={onSelect}
            style={{
              flex: 1, padding: '0 8px', cursor: interactive ? 'grab' : 'default',
              display: 'flex', flexDirection: 'column', justifyContent: 'center',
              minWidth: 0, position: 'relative', zIndex: 1,
            }}
          >
            <span style={{
              fontSize: '0.7rem', fontWeight: 600, color: '#fff',
              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
              textShadow: '0 1px 2px rgba(0,0,0,0.25)',
            }}>
              {shift.roleName || ''}
            </span>
            <span style={{
              fontSize: '0.65rem', color: 'rgba(255,255,255,0.85)',
              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            }}>
              {previewStartTime}–{previewEndTime}
              {hrs > 0 && displayWidth > 80 && (
                <span style={{ marginLeft: 4, opacity: 0.7 }}>
                  ({hrs.toFixed(1)}h{totalBreakMins > 0 ? ` \u00b7 ${Math.round(totalBreakMins)}m break` : ''})
                </span>
              )}
            </span>
          </div>
        );
      })()}

      {/* Right resize handle */}
      {interactive && onResize && (
        <div
          onMouseDown={e => handleResizeStart(e, 'right')}
          style={{
            position: 'absolute', right: 0, top: 0, bottom: 0, width: 6,
            cursor: 'ew-resize', zIndex: 2,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
        >
          <div className="resize-grip" style={{
            width: 2, height: 16, borderRadius: 1,
            backgroundColor: 'rgba(255,255,255,0.4)',
            opacity: 0, transition: 'opacity 0.15s',
          }} />
        </div>
      )}
    </div>
  );
}

// --- Droppable lane with click-to-create ---

function Lane({ laneId, index, interactive, selectedDate, onCreateShift, children }: {
  laneId: string;
  index: number;
  interactive: boolean;
  selectedDate: Date;
  onCreateShift?: (staffId: string, clockinTime: string, clockoutTime: string) => void;
  children: React.ReactNode;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: `${laneId}_lane` });
  const rowRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ startX: number; startClientX: number; isDragging: boolean } | null>(null);
  const [dragPreview, setDragPreview] = useState<{ left: number; width: number } | null>(null);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (!interactive || !onCreateShift || e.button !== 0) return;
    if ((e.target as HTMLElement).closest('[data-shift]')) return;
    const rect = rowRef.current?.getBoundingClientRect();
    if (!rect) return;
    const x = e.clientX - rect.left;
    dragRef.current = { startX: x, startClientX: e.clientX, isDragging: false };
  }, [interactive, onCreateShift]);

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (!dragRef.current || !rowRef.current) return;
    if (Math.abs(e.clientX - dragRef.current.startClientX) > DRAG_THRESHOLD) {
      dragRef.current.isDragging = true;
      const rect = rowRef.current.getBoundingClientRect();
      const currentX = Math.max(0, Math.min(TIMELINE_W, e.clientX - rect.left));
      const left = Math.min(dragRef.current.startX, currentX);
      const width = Math.abs(currentX - dragRef.current.startX);
      setDragPreview({ left, width });
    }
  }, []);

  const handleMouseUp = useCallback((e: React.MouseEvent) => {
    if (!dragRef.current || !rowRef.current || !onCreateShift) {
      dragRef.current = null;
      setDragPreview(null);
      return;
    }

    const rect = rowRef.current.getBoundingClientRect();
    const currentX = Math.max(0, Math.min(TIMELINE_W, e.clientX - rect.left));

    if (dragRef.current.isDragging) {
      const startX = Math.min(dragRef.current.startX, currentX);
      const endX = Math.max(dragRef.current.startX, currentX);
      const clockIn = offsetToTime(startX, selectedDate, HOUR_W, DAY_START_HOUR);
      const clockOut = offsetToTime(endX, selectedDate, HOUR_W, DAY_START_HOUR);
      const inMs = new Date(clockIn).getTime();
      const outMs = new Date(clockOut).getTime();
      if (outMs - inMs >= MIN_SHIFT_MS) {
        onCreateShift(laneId, clockIn, clockOut);
      }
    } else {
      // Click — create default-duration shift
      const clickTime = offsetToTime(dragRef.current.startX, selectedDate, HOUR_W, DAY_START_HOUR);
      const startMs = snapToGrid(new Date(clickTime).getTime(), SNAP_MINUTES);
      const endMs = startMs + DEFAULT_SHIFT_MS;
      const pad = (n: number) => String(n).padStart(2, '0');
      const fmt = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:00+13:00`;
      onCreateShift(laneId, fmt(new Date(startMs)), fmt(new Date(endMs)));
    }

    dragRef.current = null;
    setDragPreview(null);
  }, [laneId, selectedDate, onCreateShift]);

  const handleMouseLeave = useCallback(() => {
    if (dragRef.current) {
      dragRef.current = null;
      setDragPreview(null);
    }
  }, []);

  return (
    <div
      ref={(node) => { setNodeRef(node); (rowRef as React.MutableRefObject<HTMLDivElement | null>).current = node; }}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseLeave}
      style={{
        width: TIMELINE_W, height: ROW_H, position: 'relative',
        borderBottom: '1px solid #eee',
        backgroundColor: isOver ? '#dbeafe' : index % 2 === 1 ? '#fafafa' : '#fff',
        transition: 'background-color 0.15s',
        cursor: interactive && onCreateShift ? 'crosshair' : 'default',
      }}
    >
      {children}
      {dragPreview && (
        <div style={{
          position: 'absolute',
          left: dragPreview.left, width: dragPreview.width,
          top: 4, bottom: 4,
          backgroundColor: 'rgba(37, 99, 235, 0.15)',
          border: '1px dashed rgba(37, 99, 235, 0.5)',
          borderRadius: 6,
          pointerEvents: 'none',
        }} />
      )}
    </div>
  );
}

// --- Main component ---

export default function DayTimeline({ shifts, selectedDate, editingShiftId, onSelectShift, onResizeShift, onCreateShift, interactive }: DayTimelineProps) {
  const lanes = useMemo(() => buildLanes(shifts, selectedDate), [shifts, selectedDate]);

  const hours: number[] = [];
  for (let h = DAY_START_HOUR; h < DAY_START_HOUR + DAY_HOURS; h++) {
    hours.push(h % 24);
  }

  const nowLine = useMemo(() => {
    const today = dateKey(new Date());
    const selected = dateKey(selectedDate);
    return today === selected ? nowOffset() : null;
  }, [selectedDate]);

  if (lanes.length === 0) {
    return (
      <div style={{ padding: '2rem', textAlign: 'center', color: '#bbb', fontSize: '0.85rem' }}>
        No shifts scheduled for this day.
      </div>
    );
  }

  return (
    <div style={{ border: '1px solid #e2e8f0', borderRadius: 8, overflow: 'hidden' }}>
      <style>{`
        [data-shift]:hover .resize-grip { opacity: 1 !important; }
      `}</style>
      <div style={{ display: 'flex' }}>
        {/* Sidebar */}
        <div style={{ width: SIDEBAR_W, flexShrink: 0, borderRight: '1px solid #e2e8f0', backgroundColor: '#fafafa' }}>
          <div style={{ height: 32, borderBottom: '2px solid #e2e8f0', display: 'flex', alignItems: 'center', padding: '0 0.5rem' }}>
            <span style={{ fontSize: '0.72rem', fontWeight: 600, color: '#999', textTransform: 'uppercase', letterSpacing: '0.03em' }}>Staff</span>
          </div>
          {lanes.map((lane, i) => (
            <div key={lane.id} style={{
              height: ROW_H, padding: '0.3rem 0.5rem',
              borderBottom: '1px solid #eee',
              display: 'flex', flexDirection: 'column', justifyContent: 'center',
              backgroundColor: i % 2 === 1 ? '#f5f5f5' : '#fafafa',
            }}>
              <div style={{ fontWeight: 600, color: '#333', fontSize: '0.78rem', lineHeight: 1.3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{lane.name}</div>
              <div style={{ fontSize: '0.65rem', color: '#999' }}>{lane.role}</div>
            </div>
          ))}
        </div>

        {/* Timeline area */}
        <div style={{ flex: 1, overflowX: 'auto', minWidth: 0 }}>
          {/* Hour header */}
          <div style={{ width: TIMELINE_W, height: 32, position: 'relative', borderBottom: '2px solid #e2e8f0' }}>
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

          {/* Lanes */}
          <div style={{ position: 'relative' }}>
            {lanes.map((lane, li) => (
              <Lane key={lane.id} laneId={lane.id} index={li} interactive={interactive} selectedDate={selectedDate} onCreateShift={onCreateShift}>
                {/* Hour gridlines */}
                {hours.map((_, i) => (
                  <div key={i} style={{
                    position: 'absolute', left: i * HOUR_W, top: 0, bottom: 0,
                    borderRight: '1px solid #f5f5f5', pointerEvents: 'none',
                  }} />
                ))}
                {/* Shift bars */}
                {lane.shifts.map(shift => (
                  <div key={shift.id} data-shift="true">
                    <ShiftBar
                      shift={shift}
                      staffId={lane.id}
                      interactive={interactive}
                      isSelected={editingShiftId === shift.id}
                      onSelect={() => onSelectShift(editingShiftId === shift.id ? { id: undefined } as Shift : shift)}
                      onResize={onResizeShift}
                      selectedDate={selectedDate}
                    />
                  </div>
                ))}
              </Lane>
            ))}

            {/* Now indicator */}
            {nowLine != null && (
              <div style={{
                position: 'absolute', left: nowLine, top: 0, bottom: 0,
                width: 2, backgroundColor: '#ef4444',
                zIndex: 5, pointerEvents: 'none',
              }}>
                <div style={{
                  position: 'absolute', top: -4, left: -3,
                  width: 8, height: 8, borderRadius: '50%',
                  backgroundColor: '#ef4444',
                }} />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
