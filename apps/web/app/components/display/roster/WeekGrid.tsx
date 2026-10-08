'use client';

import { useDraggable, useDroppable } from '@dnd-kit/core';
import type { Shift, ShiftBreak, StaffRow, DragData } from './shared';
import { dateKey, calcHours, roleColor, DAY_NAMES, OPEN_ROW_ID } from './shared';
import type { VenueTimePrefs } from '../../../lib/rosterTime';
import { formatClock } from '../../../lib/rosterTime';

interface WeekGridProps {
  staffRows: StaffRow[];
  days: Date[];
  /** Venue clock — shift times are shown in it, not the viewer's. */
  prefs: VenueTimePrefs;
  editingShiftId: string | null;
  onSelectShift: (shift: Shift) => void;
  onSelectDay: (date: Date) => void;
  interactive: boolean;
}

function DraggableShift({ shift, staffId, interactive, isSelected, onSelect, prefs }: {
  shift: Shift;
  prefs: VenueTimePrefs;
  staffId: string;
  interactive: boolean;
  isSelected: boolean;
  onSelect: () => void;
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `shift-${shift.id}`,
    data: { shift, sourceStaffId: staffId } satisfies DragData,
    disabled: !interactive,
  });

  const hrs = calcHours(shift.clockinTime, shift.clockoutTime);
  const color = roleColor(shift.roleId || '');
  // An open (unassigned) shift reads as a slot still to fill: its own tint, a dashed edge.
  const open = !shift.staffMemberId;

  return (
    <div
      ref={setNodeRef}
      {...listeners}
      {...attributes}
      onClick={onSelect}
      style={{
        display: 'flex', alignItems: 'stretch',
        borderRadius: 'var(--radius-sm)', overflow: 'hidden',
        cursor: interactive ? 'grab' : 'default',
        border: isSelected
          ? '1px solid var(--focus)'
          : open ? '1px dashed var(--line-strong)' : '1px solid var(--line)',
        boxShadow: isSelected ? '0 0 0 1px var(--focus)' : undefined,
        backgroundColor: isSelected ? 'var(--accent-soft)' : open ? 'var(--open-bg)' : 'var(--bg)',
        opacity: isDragging ? 0.4 : 1,
        transition: 'border-color 0.15s, opacity 0.15s',
      }}
    >
      {/* Role stripe — a 3px category mark, never a fill under text. */}
      <div style={{ width: 3, backgroundColor: color, flexShrink: 0 }} />
      <div style={{ padding: '4px 6px', flex: 1, minWidth: 0, lineHeight: 1.3 }}>
        {/* Wraps after the dash rather than cutting off the finish time in a narrow column. */}
        <div style={{ fontSize: 'var(--fs-sm)', fontWeight: 500, color: open ? 'var(--open)' : 'var(--text)', overflowWrap: 'anywhere' }}>
          {formatClock(shift.clockinTime as string, prefs)}–{formatClock(shift.clockoutTime as string, prefs)}
        </div>
        {hrs > 0 && (() => {
          const activeBreaks = (shift.breaks || []).filter((b: ShiftBreak) => !b.deletedAt);
          const breakMins = activeBreaks.reduce((sum: number, b: ShiftBreak) =>
            sum + Math.max(0, (new Date(b.breakEnd).getTime() - new Date(b.breakStart).getTime())) / 60000, 0);
          return (
            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
              {hrs.toFixed(1)}h{breakMins > 0 && <span style={{ marginLeft: 3 }}>({Math.round(breakMins)}m brk)</span>}
            </div>
          );
        })()}
      </div>
    </div>
  );
}

function DroppableCell({ staffId, dk, isToday, isLastCol, children }: {
  staffId: string;
  dk: string;
  isToday: boolean;
  isLastCol: boolean;
  children: React.ReactNode;
}) {
  const droppableId = `${staffId}_${dk}`;
  const { setNodeRef, isOver } = useDroppable({ id: droppableId });

  return (
    <div
      ref={setNodeRef}
      style={{
        display: 'flex', flexDirection: 'column', gap: 4,
        padding: 4, minWidth: 0, minHeight: 44,
        borderRight: isLastCol ? undefined : '1px solid var(--line-soft)',
        backgroundColor: isOver ? 'var(--accent-soft)' : isToday ? 'var(--surface)' : undefined,
        transition: 'background-color 0.15s',
      }}
    >
      {children}
    </div>
  );
}

export default function WeekGrid({ staffRows, days, prefs, editingShiftId, onSelectShift, onSelectDay, interactive }: WeekGridProps) {
  const cols = `120px repeat(${days.length}, minmax(80px, 1fr))`;

  return (
    <div style={{ overflowX: 'auto', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', backgroundColor: 'var(--bg)' }}>
      <div style={{ minWidth: 700, fontSize: 'var(--fs-sm)', fontVariantNumeric: 'tabular-nums' }}>
        {/* Header row — the table-header look: 12px/600, sentence case, on the header fill */}
        <div style={{
          display: 'grid', gridTemplateColumns: cols,
          borderBottom: '1px solid var(--line-strong)', backgroundColor: 'var(--surface-alt)',
          fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--text-soft)',
        }}>
          <div style={{
            padding: '10px 12px',
            borderRight: '1px solid var(--line)',
            position: 'sticky', left: 0, zIndex: 1, backgroundColor: 'var(--surface-alt)',
          }}>Staff</div>
          {days.map((d, i) => {
            const isToday = dateKey(d) === dateKey(new Date());
            return (
              <div
                key={i}
                onClick={() => onSelectDay(d)}
                title="Open in day view"
                style={{
                  padding: '10px 6px', textAlign: 'center',
                  borderRight: i < days.length - 1 ? '1px solid var(--line-soft)' : 'none',
                  color: isToday ? 'var(--accent)' : undefined,
                  backgroundColor: isToday ? 'var(--accent-soft)' : undefined,
                  boxShadow: isToday ? 'inset 0 -2px 0 var(--brand-soft)' : undefined,
                  whiteSpace: 'nowrap', cursor: 'pointer',
                }}
              >
                {DAY_NAMES[d.getDay()]} {d.getDate()}
              </div>
            );
          })}
        </div>

        {/* Staff rows */}
        {staffRows.map((row, ri) => (
          <div
            key={row.id}
            style={{
              display: 'grid', gridTemplateColumns: cols,
              borderTop: ri > 0 ? '1px solid var(--line)' : undefined,
            }}
          >
            {/* Staff name cell */}
            <div style={{
              padding: '8px 12px', minWidth: 0,
              borderRight: '1px solid var(--line)',
              position: 'sticky', left: 0, zIndex: 1,
              backgroundColor: 'var(--bg)',
            }}>
              <div style={{
                fontWeight: 500, lineHeight: 1.3,
                color: row.id === OPEN_ROW_ID ? 'var(--open)' : 'var(--text)',
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }} title={row.name}>{row.name}</div>
              {row.role && (
                <div style={{
                  fontSize: 'var(--fs-xs)', color: 'var(--muted)', lineHeight: 1.3,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }} title={row.role}>{row.role}</div>
              )}
            </div>

            {/* Day cells */}
            {days.map((d, di) => {
              const dk = dateKey(d);
              const dayShifts = row.shiftsByDay.get(dk) || [];
              const isToday = dk === dateKey(new Date());
              return (
                <DroppableCell key={di} staffId={row.id} dk={dk} isToday={isToday} isLastCol={di === days.length - 1}>
                  {dayShifts.map((shift, si) => (
                    <DraggableShift
                      key={shift.id || `${row.id}-${dk}-${si}`}
                      shift={shift}
                      staffId={row.id}
                      interactive={interactive}
                      isSelected={editingShiftId === shift.id}
                      onSelect={() => onSelectShift(editingShiftId === shift.id ? { id: undefined } as Shift : shift)}
                      prefs={prefs}
                    />
                  ))}
                </DroppableCell>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
