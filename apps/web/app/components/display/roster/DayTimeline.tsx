'use client';

import { useMemo, useState, useRef, useCallback } from 'react';
import { useDraggable, useDroppable } from '@dnd-kit/core';
import type { Shift, ShiftBreak, DragData } from './shared';
import { dateKey, calcHours, roleColor, staffName, OPEN_ROW_ID, OPEN_ROW_LABEL } from './shared';
import type { VenueTimePrefs } from '../../../lib/rosterTime';
import { companyDayDate, offsetToISO, formatClock, formatHourLabel } from '../../../lib/rosterTime';
import {
  HOUR_W, DAY_HOURS, TIMELINE_W, SNAP_MINUTES,
  pxToMinutes, snapPx, timeToOffset, shiftsForDay, nowOffset,
} from './grid';
import PageState from '../../ui/PageState';

// Same staff-column width as the week grid, so the two views line up and a
// phone keeps more of the timeline in view.
const SIDEBAR_W = 120;
const HEADER_H = 36;
const ROW_H = 56;
const MIN_SHIFT_MS = 15 * 60 * 1000;
const DEFAULT_SHIFT_MS = 4 * 60 * 60 * 1000;
const DRAG_THRESHOLD = 5;

interface DayTimelineProps {
  shifts: Shift[];
  selectedDate: Date;
  /** Venue timezone + business-day start; drives every position on the grid. */
  prefs: VenueTimePrefs;
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

function buildLanes(shifts: Shift[], dayDate: string, prefs: VenueTimePrefs): StaffLane[] {
  const dayShifts = shiftsForDay(shifts, dayDate, prefs);
  const laneMap = new Map<string, StaffLane>();

  for (const s of dayShifts) {
    const open = !s.staffMemberId;
    const sid = open ? OPEN_ROW_ID : s.staffMemberId!;
    if (!laneMap.has(sid)) {
      laneMap.set(sid, {
        id: sid,
        name: open ? OPEN_ROW_LABEL : staffName(s),
        firstName: open ? '' : (s.staffMemberFirstName || ''),
        lastName: open ? '' : (s.staffMemberLastName || ''),
        role: open ? '' : (s.roleName || ''),
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

// --- Shift bar with resize handles ---

function ShiftBar({ shift, staffId, interactive, isSelected, onSelect, onResize, dayDate, prefs }: {
  shift: Shift;
  staffId: string;
  interactive: boolean;
  isSelected: boolean;
  onSelect: () => void;
  onResize?: (shiftId: string, clockinTime: string, clockoutTime: string) => void;
  dayDate: string;
  prefs: VenueTimePrefs;
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `shift-${shift.id}`,
    data: { shift, sourceStaffId: staffId } satisfies DragData,
    disabled: !interactive,
  });

  const [resizePreview, setResizePreview] = useState<{ left: number; width: number } | null>(null);
  const resizeRef = useRef<{ edge: 'left' | 'right'; startX: number; origLeft: number; origWidth: number } | null>(null);
  const latestPreview = useRef<{ left: number; width: number } | null>(null);

  const origLeft = timeToOffset(shift.clockinTime || '', dayDate, prefs);
  const origRight = timeToOffset(shift.clockoutTime || '', dayDate, prefs);
  const origWidth = Math.max(origRight - origLeft, 20);
  const hrs = calcHours(shift.clockinTime, shift.clockoutTime);
  const color = roleColor(shift.roleId || '');
  // An open (unassigned) shift reads as a slot still to fill: its own tint, a dashed edge.
  const open = !shift.staffMemberId;

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
        // Snap left edge to the grid interval
        newLeft = snapPx(newLeft);
        newWidth = (resizeRef.current.origLeft + resizeRef.current.origWidth) - newLeft;
      } else {
        newWidth = Math.max(HOUR_W / 4, resizeRef.current.origWidth + delta);
        // Snap right edge to the grid interval
        const snappedRight = snapPx(newLeft + newWidth);
        newWidth = snappedRight - newLeft;
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
        const newClockIn = offsetToISO(dayDate, pxToMinutes(preview.left), prefs);
        const newClockOut = offsetToISO(dayDate, pxToMinutes(preview.left + preview.width), prefs);
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
  }, [interactive, onResize, origLeft, origWidth, shift.id, dayDate, prefs]);

  // Preview time labels during resize
  const previewStartTime = resizePreview
    ? formatClock(offsetToISO(dayDate, pxToMinutes(resizePreview.left), prefs), prefs)
    : formatClock(shift.clockinTime as string, prefs);
  const previewEndTime = resizePreview
    ? formatClock(offsetToISO(dayDate, pxToMinutes(resizePreview.left + resizePreview.width), prefs), prefs)
    : formatClock(shift.clockoutTime as string, prefs);

  return (
    <div
      ref={setNodeRef}
      style={{
        position: 'absolute',
        left: displayLeft, width: displayWidth, top: 4, bottom: 4,
        // Light bar, dark text (the role colour is only the stripe): readable at any hue.
        backgroundColor: isSelected ? 'var(--accent-soft)' : open ? 'var(--open-bg)' : 'var(--bg)',
        border: isSelected
          ? '1px solid var(--focus)'
          : open ? '1px dashed var(--line-strong)' : '1px solid var(--line-strong)',
        borderRadius: 'var(--radius-sm)',
        display: 'flex', alignItems: 'center',
        overflow: 'hidden',
        boxShadow: isSelected ? '0 0 0 1px var(--focus)' : resizePreview ? '0 0 0 2px var(--brand-soft)' : undefined,
        opacity: isDragging ? 0.4 : 1,
        transition: resizePreview ? 'none' : 'box-shadow 0.15s, opacity 0.15s',
        zIndex: resizePreview ? 10 : 1,
      }}
    >
      {/* Role stripe — a 3px category mark, as on the week grid. */}
      <div style={{
        position: 'absolute', left: 0, top: 0, bottom: 0, width: 3,
        backgroundColor: color, pointerEvents: 'none', zIndex: 1,
      }} />

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
              backgroundImage: 'repeating-linear-gradient(135deg, var(--line) 0 2px, transparent 2px 6px)',
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
            backgroundColor: 'var(--icon)',
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
              flex: 1, padding: '0 8px 0 10px', cursor: interactive ? 'grab' : 'default',
              display: 'flex', flexDirection: 'column', justifyContent: 'center',
              minWidth: 0, position: 'relative', zIndex: 1,
              fontSize: 'var(--fs-xs)', lineHeight: 1.35,
            }}
          >
            <span style={{
              fontWeight: 600, color: open ? 'var(--open)' : 'var(--text)',
              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            }}>
              {shift.roleName || ''}
            </span>
            <span style={{
              color: 'var(--text-soft)', fontVariantNumeric: 'tabular-nums',
              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            }}>
              {previewStartTime}–{previewEndTime}
              {hrs > 0 && displayWidth > 80 && (
                <span style={{ marginLeft: 4, color: 'var(--muted)' }}>
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
            backgroundColor: 'var(--icon)',
            opacity: 0, transition: 'opacity 0.15s',
          }} />
        </div>
      )}
    </div>
  );
}

// --- Droppable lane with click-to-create ---

function Lane({ laneId, isLast, interactive, dayDate, prefs, onCreateShift, children }: {
  laneId: string;
  /** The container's own edge closes the last lane. */
  isLast: boolean;
  interactive: boolean;
  /** Business day this lane renders, and the venue clock it is measured in. */
  dayDate: string;
  prefs: VenueTimePrefs;
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
      const clockIn = offsetToISO(dayDate, pxToMinutes(startX), prefs);
      const clockOut = offsetToISO(dayDate, pxToMinutes(endX), prefs);
      const inMs = new Date(clockIn).getTime();
      const outMs = new Date(clockOut).getTime();
      if (outMs - inMs >= MIN_SHIFT_MS) {
        onCreateShift(laneId, clockIn, clockOut);
      }
    } else {
      // Click — create a default-duration shift, snapped to the grid interval
      const startMin = Math.round(pxToMinutes(dragRef.current.startX) / SNAP_MINUTES) * SNAP_MINUTES;
      const endMin = startMin + DEFAULT_SHIFT_MS / 60000;
      onCreateShift(laneId, offsetToISO(dayDate, startMin, prefs), offsetToISO(dayDate, endMin, prefs));
    }

    dragRef.current = null;
    setDragPreview(null);
  }, [laneId, dayDate, prefs, onCreateShift]);

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
        borderBottom: isLast ? undefined : '1px solid var(--line)',
        backgroundColor: isOver ? 'var(--accent-soft)' : 'var(--bg)',
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
          backgroundColor: 'var(--accent-soft)',
          border: '1px dashed var(--accent)',
          borderRadius: 'var(--radius-sm)',
          pointerEvents: 'none',
        }} />
      )}
    </div>
  );
}

// --- Main component ---

export default function DayTimeline({ shifts, selectedDate, prefs, editingShiftId, onSelectShift, onResizeShift, onCreateShift, interactive }: DayTimelineProps) {
  // The business day being shown. Every position on the grid is measured from
  // this day's start in the venue's zone.
  const dayDate = useMemo(() => dateKey(selectedDate), [selectedDate]);
  const lanes = useMemo(() => buildLanes(shifts, dayDate, prefs), [shifts, dayDate, prefs]);

  // Hour ticks start at the venue's day start, not a hardcoded 6am.
  const hourTicks = useMemo(
    () => Array.from({ length: DAY_HOURS }, (_, i) => prefs.dayStartMinutes + i * 60),
    [prefs.dayStartMinutes],
  );

  const nowLine = useMemo(
    () => (companyDayDate(new Date(), prefs) === dayDate ? nowOffset(dayDate, prefs) : null),
    [dayDate, prefs],
  );

  if (lanes.length === 0) {
    return <PageState kind="empty" title="No shifts scheduled for this day." />;
  }

  return (
    <div style={{ border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', overflow: 'hidden', backgroundColor: 'var(--bg)' }}>
      <style>{`
        [data-shift]:hover .resize-grip { opacity: 1 !important; }
      `}</style>
      <div style={{ display: 'flex' }}>
        {/* Sidebar */}
        <div style={{ width: SIDEBAR_W, flexShrink: 0, borderRight: '1px solid var(--line)', backgroundColor: 'var(--bg)' }}>
          {/* Header cells: the table-header look — 12px/600, sentence case, on the header fill */}
          <div style={{
            height: HEADER_H, padding: '0 12px',
            display: 'flex', alignItems: 'center',
            borderBottom: '1px solid var(--line-strong)', backgroundColor: 'var(--surface-alt)',
            fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--text-soft)',
          }}>Staff</div>
          {lanes.map((lane, i) => (
            <div key={lane.id} style={{
              height: ROW_H, padding: '0 12px',
              borderBottom: i < lanes.length - 1 ? '1px solid var(--line)' : undefined,
              display: 'flex', flexDirection: 'column', justifyContent: 'center',
              lineHeight: 1.3,
            }}>
              <div style={{
                fontSize: 'var(--fs-sm)', fontWeight: 500,
                color: lane.id === OPEN_ROW_ID ? 'var(--open)' : 'var(--text)',
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>{lane.name}</div>
              {lane.role && (
                <div style={{
                  fontSize: 'var(--fs-xs)', color: 'var(--muted)',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>{lane.role}</div>
              )}
            </div>
          ))}
        </div>

        {/* Timeline area */}
        <div style={{ flex: 1, overflowX: 'auto', minWidth: 0 }}>
          {/* Hour header */}
          <div style={{
            width: TIMELINE_W, height: HEADER_H, position: 'relative',
            borderBottom: '1px solid var(--line-strong)', backgroundColor: 'var(--surface-alt)',
          }}>
            {hourTicks.map((mins, i) => (
              <div key={i} style={{
                position: 'absolute', left: i * HOUR_W, width: HOUR_W,
                height: '100%', display: 'flex', alignItems: 'center',
                borderRight: '1px solid var(--line)',
                paddingLeft: 6, fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--text-soft)',
                whiteSpace: 'nowrap',
              }}>
                {formatHourLabel(mins)}
              </div>
            ))}
          </div>

          {/* Lanes */}
          <div style={{ position: 'relative' }}>
            {lanes.map((lane, li) => (
              <Lane key={lane.id} laneId={lane.id} isLast={li === lanes.length - 1} interactive={interactive} dayDate={dayDate} prefs={prefs} onCreateShift={onCreateShift}>
                {/* Hour gridlines */}
                {hourTicks.map((_, i) => (
                  <div key={i} style={{
                    position: 'absolute', left: i * HOUR_W, top: 0, bottom: 0,
                    borderRight: '1px solid var(--line-soft)', pointerEvents: 'none',
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
                      dayDate={dayDate}
                      prefs={prefs}
                    />
                  </div>
                ))}
              </Lane>
            ))}

            {/* Now indicator — the accent, like today's column on the week grid */}
            {nowLine != null && (
              <div style={{
                position: 'absolute', left: nowLine, top: 0, bottom: 0,
                width: 2, backgroundColor: 'var(--accent)',
                zIndex: 5, pointerEvents: 'none',
              }}>
                <div style={{
                  position: 'absolute', top: -4, left: -3,
                  width: 8, height: 8, borderRadius: '50%',
                  backgroundColor: 'var(--accent)',
                }} />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
