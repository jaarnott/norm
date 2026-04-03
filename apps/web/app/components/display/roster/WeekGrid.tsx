'use client';

import { useDraggable, useDroppable } from '@dnd-kit/core';
import type { Shift, ShiftBreak, StaffRow, DragData } from './shared';
import { dateKey, formatTimeShort, calcHours, roleColor, DAY_NAMES } from './shared';

interface WeekGridProps {
  staffRows: StaffRow[];
  days: Date[];
  editingShiftId: string | null;
  onSelectShift: (shift: Shift) => void;
  onSelectDay: (date: Date) => void;
  interactive: boolean;
}

function DraggableShift({ shift, staffId, interactive, isSelected, onSelect }: {
  shift: Shift;
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

  return (
    <div
      ref={setNodeRef}
      {...listeners}
      {...attributes}
      onClick={onSelect}
      style={{
        display: 'flex', alignItems: 'stretch', gap: 0,
        marginBottom: 2, borderRadius: 4, overflow: 'hidden',
        cursor: interactive ? 'grab' : 'default',
        border: isSelected ? '1px solid #2563eb' : '1px solid #e2e8f0',
        backgroundColor: isSelected ? '#eff6ff' : '#fff',
        opacity: isDragging ? 0.4 : 1,
        transition: 'border-color 0.15s, opacity 0.15s',
      }}
    >
      <div style={{ width: 3, backgroundColor: color, flexShrink: 0 }} />
      <div style={{ padding: '3px 5px', flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 500, color: '#333', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {formatTimeShort(shift.clockinTime)}–{formatTimeShort(shift.clockoutTime)}
        </div>
        {hrs > 0 && (() => {
          const activeBreaks = (shift.breaks || []).filter((b: ShiftBreak) => !b.deletedAt);
          const breakMins = activeBreaks.reduce((sum: number, b: ShiftBreak) =>
            sum + Math.max(0, (new Date(b.breakEnd).getTime() - new Date(b.breakStart).getTime())) / 60000, 0);
          return (
            <div style={{ fontSize: '0.68rem', color: '#888' }}>
              {hrs.toFixed(1)}h{breakMins > 0 && <span style={{ marginLeft: 3, color: '#bbb' }}>({Math.round(breakMins)}m brk)</span>}
            </div>
          );
        })()}
      </div>
    </div>
  );
}

function DroppableCell({ staffId, dk, isToday, children }: {
  staffId: string;
  dk: string;
  isToday: boolean;
  children: React.ReactNode;
}) {
  const droppableId = `${staffId}_${dk}`;
  const { setNodeRef, isOver } = useDroppable({ id: droppableId });

  return (
    <div
      ref={setNodeRef}
      style={{
        padding: '0.25rem 0.2rem',
        borderBottom: '1px solid #eee',
        borderRight: '1px solid #f0f0f0',
        minHeight: 40,
        backgroundColor: isOver ? '#dbeafe' : isToday ? '#f8fbff' : undefined,
        transition: 'background-color 0.15s',
      }}
    >
      {children}
    </div>
  );
}

export default function WeekGrid({ staffRows, days, editingShiftId, onSelectShift, onSelectDay, interactive }: WeekGridProps) {
  const cols = `120px repeat(${days.length}, minmax(80px, 1fr))`;

  return (
    <div style={{ overflowX: 'auto', border: '1px solid #e2e8f0', borderRadius: 8 }}>
      <div style={{ minWidth: 700, fontSize: '0.78rem' }}>
        {/* Header row */}
        <div style={{ display: 'grid', gridTemplateColumns: cols, borderBottom: '2px solid #e2e8f0', backgroundColor: '#fafafa' }}>
          <div style={{
            padding: '0.5rem 0.6rem', fontWeight: 600, color: '#555',
            borderRight: '1px solid #e2e8f0',
            position: 'sticky', left: 0, zIndex: 1, backgroundColor: '#fafafa',
          }}>Staff</div>
          {days.map((d, i) => {
            const isToday = dateKey(d) === dateKey(new Date());
            return (
              <div
                key={i}
                onClick={() => onSelectDay(d)}
                style={{
                  padding: '0.5rem 0.4rem', textAlign: 'center',
                  borderRight: i < days.length - 1 ? '1px solid #f0f0f0' : 'none',
                  fontWeight: 600, color: isToday ? '#2563eb' : '#555',
                  backgroundColor: isToday ? '#eff6ff' : '#fafafa',
                  whiteSpace: 'nowrap', cursor: 'pointer',
                }}
              >
                <div>{DAY_NAMES[d.getDay()]}</div>
                <div style={{ fontSize: '0.85em', fontWeight: 500, color: isToday ? '#2563eb' : '#999' }}>{d.getDate()}</div>
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
              backgroundColor: ri % 2 === 1 ? '#fafafa' : '#fff',
            }}
          >
            {/* Staff name cell */}
            <div style={{
              padding: '0.4rem 0.6rem',
              borderBottom: '1px solid #eee', borderRight: '1px solid #e2e8f0',
              position: 'sticky', left: 0, zIndex: 1,
              backgroundColor: ri % 2 === 1 ? '#fafafa' : '#fff',
            }}>
              <div style={{ fontWeight: 500, color: '#333', lineHeight: 1.3 }}>{row.name}</div>
              <div style={{ fontSize: '0.7rem', color: '#999' }}>{row.role}</div>
            </div>

            {/* Day cells */}
            {days.map((d, di) => {
              const dk = dateKey(d);
              const dayShifts = row.shiftsByDay.get(dk) || [];
              const isToday = dk === dateKey(new Date());
              return (
                <DroppableCell key={di} staffId={row.id} dk={dk} isToday={isToday}>
                  {dayShifts.map((shift, si) => (
                    <DraggableShift
                      key={shift.id || `${row.id}-${dk}-${si}`}
                      shift={shift}
                      staffId={row.id}
                      interactive={interactive}
                      isSelected={editingShiftId === shift.id}
                      onSelect={() => onSelectShift(editingShiftId === shift.id ? { id: undefined } as Shift : shift)}
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
