'use client';

import { useState, useEffect, useMemo, useCallback } from 'react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import type { Shift, ShiftFormData, RosterMeta } from './roster/shared';
import { extractShifts, extractRosterMeta, getWeekDays, dateKey, buildStaffRows, DAY_NAMES } from './roster/shared';
import { apiFetch } from '../../lib/api';
import WeekGrid from './roster/WeekGrid';
import DayTimeline from './roster/DayTimeline';
import ShiftModal from './roster/ShiftModal';
import type { StaffOption, RoleOption } from './roster/ShiftModal';

type ViewMode = 'week' | 'day';

export default function RosterEditor({ data, props, onAction, taskId }: DisplayBlockProps) {
  // Detect working document mode
  const workingDocId = (data as Record<string, unknown>)?.working_document_id as string | undefined;

  const [docData, setDocData] = useState<Record<string, unknown> | null>(workingDocId ? null : data);
  const [docVersion, setDocVersion] = useState<number>(1);
  const [syncStatus, setSyncStatus] = useState<string>('synced');
  const [shifts, setShifts] = useState<Shift[]>(() => workingDocId ? [] : extractShifts(data));
  const [meta, setMeta] = useState<RosterMeta>(() => workingDocId ? { startDate: null, endDate: null, totalHours: 0, rosterId: '' } : extractRosterMeta(data));
  const connectorName = (props?.connector_name as string) || 'loadedhub';

  const [viewMode, setViewMode] = useState<ViewMode>('week');
  const [selectedDate, setSelectedDate] = useState<Date | null>(null);
  const [editingShift, setEditingShift] = useState<Shift | null>(null);
  const [addingNew, setAddingNew] = useState(false);
  const [saving, setSaving] = useState(false);

  // Fetch working document data
  useEffect(() => {
    if (!workingDocId || !taskId) return;
    apiFetch(`/api/tasks/${taskId}/working-documents/${workingDocId}`)
      .then(res => res.ok ? res.json() : null)
      .then(doc => {
        if (doc) {
          setDocData(doc.data);
          setDocVersion(doc.version);
          setSyncStatus(doc.sync_status);
          setShifts(extractShifts(doc.data));
          setMeta(extractRosterMeta(doc.data));
        }
      })
      .catch(() => {});
  }, [workingDocId, taskId]);

  // Fallback: update from props data (non-working-document mode)
  useEffect(() => {
    if (workingDocId) return;
    setShifts(extractShifts(data));
    setMeta(extractRosterMeta(data));
  }, [data, workingDocId]);

  const days = useMemo(() => getWeekDays(meta.startDate, meta.endDate), [meta.startDate, meta.endDate]);
  const staffRows = useMemo(() => buildStaffRows(shifts, days), [shifts, days]);
  const activeShifts = shifts.filter(s => !s.datestampDeleted);

  // Build staff and role options from shift data
  const staffOptions = useMemo<StaffOption[]>(() => {
    const map = new Map<string, string>();
    for (const s of activeShifts) {
      if (s.staffMemberId && !map.has(s.staffMemberId)) {
        const first = s.staffMemberFirstName || '';
        const last = s.staffMemberLastName || '';
        map.set(s.staffMemberId, (first && last) ? `${first} ${last}` : first || last || s.staffMemberId);
      }
    }
    return Array.from(map.entries()).map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name));
  }, [activeShifts]);

  const roleOptions = useMemo<RoleOption[]>(() => {
    const map = new Map<string, string>();
    for (const s of activeShifts) {
      if (s.roleId && !map.has(s.roleId)) {
        map.set(s.roleId, s.roleName || s.roleId);
      }
    }
    return Array.from(map.entries()).map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name));
  }, [activeShifts]);

  // Default selected date for day view
  const effectiveDate = selectedDate || days[0] || new Date();

  const dateRange = days.length >= 2
    ? `${days[0].toLocaleDateString('en-NZ', { month: 'short', day: 'numeric' })} – ${days[days.length - 1].toLocaleDateString('en-NZ', { month: 'short', day: 'numeric', year: 'numeric' })}`
    : '';

  // Navigation for day view
  const dayIndex = days.findIndex(d => dateKey(d) === dateKey(effectiveDate));
  const canPrev = dayIndex > 0;
  const canNext = dayIndex < days.length - 1;
  const goDay = useCallback((offset: number) => {
    const idx = dayIndex + offset;
    if (idx >= 0 && idx < days.length) setSelectedDate(days[idx]);
  }, [dayIndex, days]);

  const handleSelectDay = useCallback((date: Date) => {
    setSelectedDate(date);
    setViewMode('day');
  }, []);

  const handleSelectShift = useCallback((shift: Shift) => {
    if (shift.id) {
      setEditingShift(shift);
      setAddingNew(false);
    } else {
      setEditingShift(null);
    }
  }, []);

  // --- Action handlers ---

  const patchDoc = useCallback(async (ops: Record<string, unknown>[]) => {
    if (!workingDocId || !taskId) return;
    setSyncStatus('syncing');
    try {
      const res = await apiFetch(`/api/tasks/${taskId}/working-documents/${workingDocId}`, {
        method: 'PATCH',
        body: JSON.stringify({ ops, version: docVersion }),
      });
      if (res.ok) {
        const updated = await res.json();
        setDocData(updated.data);
        setDocVersion(updated.version);
        setSyncStatus(updated.sync_status);
        setShifts(extractShifts(updated.data));
        setMeta(extractRosterMeta(updated.data));
      } else {
        setSyncStatus('error');
      }
    } catch {
      setSyncStatus('error');
    }
  }, [workingDocId, taskId, docVersion]);

  const handleSave = async (formData: ShiftFormData) => {
    setSaving(true);
    try {
      if (workingDocId && taskId) {
        // Working document mode — patch locally, sync in background
        if (editingShift) {
          await patchDoc([{
            op: 'update_shift',
            shift_id: editingShift.id,
            fields: {
              staffMemberId: formData.staff_member_id,
              roleId: formData.role_id,
              clockinTime: formData.clockin_time,
              clockoutTime: formData.clockout_time,
            },
          }]);
        } else {
          await patchDoc([{
            op: 'add_shift',
            fields: {
              rosterId: meta.rosterId,
              staffMemberId: formData.staff_member_id,
              roleId: formData.role_id,
              clockinTime: formData.clockin_time,
              clockoutTime: formData.clockout_time,
            },
          }]);
        }
      } else if (onAction) {
        // Legacy widget-action mode
        if (editingShift) {
          await onAction({
            connector_name: connectorName, action: 'update_shift',
            params: {
              shift_id: editingShift.id || '', roster_id: editingShift.rosterId || meta.rosterId,
              staff_member_id: formData.staff_member_id, role_id: formData.role_id,
              clockin_time: formData.clockin_time, clockout_time: formData.clockout_time,
            },
          });
        } else {
          await onAction({
            connector_name: connectorName, action: 'create_shift',
            params: {
              roster_id: meta.rosterId, staff_member_id: formData.staff_member_id,
              role_id: formData.role_id, clockin_time: formData.clockin_time,
              clockout_time: formData.clockout_time,
            },
          });
        }
      }
      setEditingShift(null);
      setAddingNew(false);
    } finally { setSaving(false); }
  };

  const handleDelete = async (shift: Shift) => {
    setSaving(true);
    try {
      if (workingDocId && taskId) {
        await patchDoc([{ op: 'delete_shift', shift_id: shift.id }]);
      } else if (onAction) {
        await onAction({
          connector_name: connectorName, action: 'delete_shift',
          params: {
            shift_id: shift.id || '', roster_id: shift.rosterId || meta.rosterId,
            staff_member_id: shift.staffMemberId || '', role_id: shift.roleId || '',
            clockin_time: shift.clockinTime || '', clockout_time: shift.clockoutTime || '',
          },
        });
      }
      setEditingShift(null);
    } finally { setSaving(false); }
  };

  if (activeShifts.length === 0 && !addingNew) return null;

  // --- Toggle button style ---
  const toggleBtn = (mode: ViewMode, label: string) => (
    <button
      onClick={() => setViewMode(mode)}
      style={{
        padding: '3px 10px', fontSize: '0.72rem', fontWeight: viewMode === mode ? 600 : 400,
        border: '1px solid #ddd', borderRadius: 0,
        backgroundColor: viewMode === mode ? '#333' : '#fff',
        color: viewMode === mode ? '#fff' : '#666',
        cursor: 'pointer', fontFamily: 'inherit',
        ...(mode === 'week' ? { borderRadius: '4px 0 0 4px' } : { borderRadius: '0 4px 4px 0', borderLeft: 'none' }),
      }}
    >{label}</button>
  );

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.75rem', flexWrap: 'wrap' }}>
        <span style={{ fontSize: '0.9rem', fontWeight: 600, color: '#333' }}>Roster</span>

        {viewMode === 'week' && dateRange && (
          <span style={{ fontSize: '0.82rem', color: '#888' }}>{dateRange}</span>
        )}

        {viewMode === 'day' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
            <button onClick={() => goDay(-1)} disabled={!canPrev} style={{
              border: 'none', background: 'none', cursor: canPrev ? 'pointer' : 'default',
              fontSize: '0.9rem', color: canPrev ? '#555' : '#ddd', padding: '0 4px', fontFamily: 'inherit',
            }}>&lsaquo;</button>
            <span style={{ fontSize: '0.82rem', color: '#555', fontWeight: 500, minWidth: 90, textAlign: 'center' }}>
              {DAY_NAMES[effectiveDate.getDay()]} {effectiveDate.toLocaleDateString('en-NZ', { day: 'numeric', month: 'short' })}
            </span>
            <button onClick={() => goDay(1)} disabled={!canNext} style={{
              border: 'none', background: 'none', cursor: canNext ? 'pointer' : 'default',
              fontSize: '0.9rem', color: canNext ? '#555' : '#ddd', padding: '0 4px', fontFamily: 'inherit',
            }}>&rsaquo;</button>
          </div>
        )}

        <span style={{ fontSize: '0.75rem', color: '#aaa' }}>
          {activeShifts.length} shifts
          {meta.totalHours > 0 && ` · ${meta.totalHours.toFixed(1)}h`}
        </span>
        {workingDocId && (
          <span title={syncStatus} style={{
            width: 8, height: 8, borderRadius: '50%', display: 'inline-block',
            backgroundColor: syncStatus === 'synced' ? '#28a745' : syncStatus === 'syncing' || syncStatus === 'dirty' ? '#ffc107' : syncStatus === 'error' ? '#dc3545' : '#888',
          }} />
        )}

        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <div>{toggleBtn('week', 'Week')}{toggleBtn('day', 'Day')}</div>
          {onAction && (
            <button onClick={() => { setAddingNew(true); setEditingShift(null); }} disabled={saving} style={{
              padding: '4px 12px', fontSize: '0.75rem', fontWeight: 500,
              border: '1px solid #28a745', borderRadius: 4, backgroundColor: '#fff',
              color: '#28a745', cursor: 'pointer', fontFamily: 'inherit',
            }}>+ Add Shift</button>
          )}
        </div>
      </div>

      {/* Views */}
      {viewMode === 'week' && (
        <WeekGrid
          staffRows={staffRows}
          days={days}
          editingShiftId={editingShift?.id || null}
          onSelectShift={handleSelectShift}
          onSelectDay={handleSelectDay}
          interactive={!!onAction}
        />
      )}

      {viewMode === 'day' && (
        <DayTimeline
          shifts={shifts}
          selectedDate={effectiveDate}
          editingShiftId={editingShift?.id || null}
          onSelectShift={handleSelectShift}
          interactive={!!onAction}
        />
      )}

      {/* Shift modal */}
      <ShiftModal
        editingShift={editingShift}
        addingNew={addingNew}
        saving={saving}
        onSave={handleSave}
        onDelete={onAction ? handleDelete : undefined}
        onClose={() => { setEditingShift(null); setAddingNew(false); }}
        staffOptions={staffOptions}
        roleOptions={roleOptions}
      />
    </div>
  );
}
