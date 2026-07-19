'use client';

import { useState, useEffect, useMemo, useCallback } from 'react';
import { DndContext, DragOverlay, PointerSensor, TouchSensor, useSensor, useSensors, type DragStartEvent, type DragEndEvent } from '@dnd-kit/core';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import type { Shift, ShiftFormData, RosterMeta, DragData } from './roster/shared';
import { extractShifts, extractRosterMeta, getWeekDays, dateKey, buildStaffRows, DAY_NAMES, calcHours, roleColor, formatWithOffset } from './roster/shared';
import { apiFetch } from '../../lib/api';
import { venueTimePrefs, formatClock, venueOffset, formatInTz, wallClockToInstant } from '../../lib/rosterTime';
import WeekGrid from './roster/WeekGrid';
import DayTimeline from './roster/DayTimeline';
import ShiftModal from './roster/ShiftModal';
import type { StaffOption, RoleOption } from './roster/ShiftModal';

type ViewMode = 'week' | 'day';

interface VenueOption {
  id: string;
  name: string;
  // /api/venues already returns these; the grid needs them to place shifts in
  // the venue's clock rather than the viewer's.
  timezone?: string | null;
  day_start_time?: string | null;
}

export default function RosterEditor({ data, props, onAction, threadId }: DisplayBlockProps) {
  // Detect working document mode
  const initialDocId = (data as Record<string, unknown>)?.working_document_id as string | undefined;
  const [currentDocId, setCurrentDocId] = useState<string | undefined>(initialDocId);
  const workingDocId = currentDocId;

  const [docData, setDocData] = useState<Record<string, unknown> | null>(initialDocId ? null : data);
  const [venues, setVenues] = useState<VenueOption[]>([]);
  const [selectedVenue, setSelectedVenue] = useState<string | null>(null);
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

  // Build working document URL — taskless or task-scoped
  const docUrl = workingDocId
    ? (threadId ? `/api/threads/${threadId}/working-documents/${workingDocId}` : `/api/working-documents/${workingDocId}`)
    : null;

  // Fetch working document data
  useEffect(() => {
    if (!docUrl) return;
    apiFetch(docUrl)
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
  }, [docUrl]);

  // Fetch venues for venue selector.
  // `embedded` marks a host outside the Norm app (an MCP App iframe in Claude)
  // where there is no session and no route back to the API — the request would
  // simply be blocked. The selector hides itself when venues is empty.
  useEffect(() => {
    if (props?.embedded) return;
    apiFetch('/api/venues')
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.venues && d.venues.length > 0) {
          setVenues(d.venues);
        }
      })
      .catch(() => {});
  }, [props?.embedded]);

  // Reload roster for a different venue
  const handleVenueChange = useCallback(async (venueId: string) => {
    setSelectedVenue(venueId);
    // Get current week range for the new venue
    const now = new Date();
    const day = now.getDay();
    const monday = new Date(now);
    monday.setDate(now.getDate() - (day === 0 ? 6 : day - 1));
    monday.setHours(0, 0, 0, 0);
    const sunday = new Date(monday);
    sunday.setDate(monday.getDate() + 6);
    sunday.setHours(23, 59, 59, 0);
    const pad = (n: number) => String(n).padStart(2, '0');
    const fmt = (d: Date) => formatWithOffset(d, venueOffset(d, timePrefs));

    try {
      const res = await apiFetch('/api/working-documents/from-connector', {
        method: 'POST',
        body: JSON.stringify({
          connector_name: connectorName,
          action: 'get_roster',
          params: { start_datetime: fmt(monday), end_datetime: fmt(sunday), venue_id: venueId },
          doc_type: 'roster',
          venue_id: venueId,
        }),
      });
      if (res.ok) {
        const result = await res.json();
        setDocData(result.data);
        setShifts(extractShifts(result.data));
        setMeta(extractRosterMeta(result.data));
        // Update working doc reference for patch operations
        if (result.id) {
          setCurrentDocId(result.id);
          setDocVersion(result.version || 1);
          setSyncStatus(result.sync_status || 'synced');
        }
      }
    } catch (e) { console.error('Venue change failed:', e); }
  }, []);

  // Fallback: update from props data (non-working-document mode)
  useEffect(() => {
    if (workingDocId) return;
    setShifts(extractShifts(data));
    setMeta(extractRosterMeta(data));
  }, [data, workingDocId]);

  // Timezone + business-day start for whichever venue is in view. Falls back to
  // Norm's defaults when the venue list isn't available (e.g. embedded in an MCP
  // host, where there's no session to fetch it with).
  const activeVenueId = selectedVenue || (props?.activeVenueId as string) || null;
  const timePrefs = useMemo(
    () => venueTimePrefs(venues.find(v => v.id === activeVenueId) ?? null),
    [venues, activeVenueId],
  );

  const days = useMemo(() => getWeekDays(meta.startDate, meta.endDate), [meta.startDate, meta.endDate]);
  const staffRows = useMemo(() => buildStaffRows(shifts, days, timePrefs), [shifts, days, timePrefs]);
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
  const [loadingWeek, setLoadingWeek] = useState(false);

  const dateRange = days.length >= 2
    ? `${days[0].toLocaleDateString('en-NZ', { month: 'short', day: 'numeric' })} – ${days[days.length - 1].toLocaleDateString('en-NZ', { month: 'short', day: 'numeric', year: 'numeric' })}`
    : '';

  // Load roster for a specific week (Monday start)
  const loadWeek = useCallback(async (monday: Date) => {
    const venueId = selectedVenue || (props?.activeVenueId as string);
    if (!venueId) return;
    setLoadingWeek(true);
    const sunday = new Date(monday);
    sunday.setDate(monday.getDate() + 6);
    sunday.setHours(23, 59, 59, 0);
    const pad = (n: number) => String(n).padStart(2, '0');
    const fmt = (d: Date) => formatWithOffset(d, venueOffset(d, timePrefs));
    try {
      const res = await apiFetch('/api/working-documents/from-connector', {
        method: 'POST',
        body: JSON.stringify({
          connector_name: connectorName,
          action: 'get_roster',
          params: { start_datetime: fmt(monday), end_datetime: fmt(sunday), venue_id: venueId },
          doc_type: 'roster',
          venue_id: venueId,
        }),
      });
      if (res.ok) {
        const result = await res.json();
        setDocData(result.data);
        setShifts(extractShifts(result.data));
        setMeta(extractRosterMeta(result.data));
        if (result.id) {
          setCurrentDocId(result.id);
          setDocVersion(result.version || 1);
          setSyncStatus(result.sync_status || 'synced');
        }
      }
    } catch { /* ignore */ }
    setLoadingWeek(false);
  }, [selectedVenue, props?.activeVenueId]);

  // Navigate weeks
  const goWeek = useCallback((direction: number) => {
    const current = days[0] || new Date();
    const next = new Date(current);
    next.setDate(current.getDate() + direction * 7);
    next.setHours(0, 0, 0, 0);
    loadWeek(next);
  }, [days, loadWeek]);

  // Jump to a specific date's week
  const goToDate = useCallback((dateStr: string) => {
    const d = new Date(dateStr);
    const day = d.getDay();
    const monday = new Date(d);
    monday.setDate(d.getDate() - (day === 0 ? 6 : day - 1));
    monday.setHours(0, 0, 0, 0);
    loadWeek(monday);
  }, [loadWeek]);

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
    if (!docUrl) return;
    setSyncStatus('syncing');
    try {
      const res = await apiFetch(docUrl, {
        method: 'PATCH',
        body: JSON.stringify({ ops, version: docVersion }),
      });
      if (res.ok) {
        const updated = await res.json();
        console.log('[patchDoc] success, version:', updated.version, 'sync:', updated.sync_status);
        setDocData(updated.data);
        setDocVersion(updated.version);
        setSyncStatus(updated.sync_status);
        setShifts(extractShifts(updated.data));
        setMeta(extractRosterMeta(updated.data));
      } else {
        const errText = await res.text().catch(() => '');
        console.error('[patchDoc] failed:', res.status, errText);
        setSyncStatus('error');
      }
    } catch (e) { console.error('[patchDoc] error:', e); setSyncStatus('error'); }
  }, [docUrl, docVersion]);

  const handleSave = async (formData: ShiftFormData) => {
    setSaving(true);
    try {
      if (workingDocId && threadId) {
        // Find the role name from the staff's existing shifts or the editing shift
        const roleName = editingShift?.roleName || shifts.find(s => s.roleId === formData.role_id)?.roleName || '';
        if (editingShift) {
          await patchDoc([{
            op: 'update_shift',
            shift_id: editingShift.id,
            fields: {
              rosterId: editingShift.rosterId || meta.rosterId,
              staffMemberId: formData.staff_member_id,
              roleId: formData.role_id,
              roleName,
              clockinTime: formData.clockin_time,
              clockoutTime: formData.clockout_time,
              venueId: editingShift.venueId || '',
              hourlyRate: editingShift.hourlyRate ?? editingShift.adjustedHourlyRate ?? 0,
            },
          }]);
        } else {
          await patchDoc([{
            op: 'add_shift',
            fields: {
              rosterId: meta.rosterId,
              staffMemberId: formData.staff_member_id,
              roleId: formData.role_id,
              roleName,
              clockinTime: formData.clockin_time,
              clockoutTime: formData.clockout_time,
            },
          }]);
        }
      } else if (onAction) {
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
      if (workingDocId && threadId) {
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

  // --- Drag and drop (dnd-kit) ---
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 200, tolerance: 6 } })
  );

  const [activeShift, setActiveShift] = useState<Shift | null>(null);

  const handleDragStart = useCallback((event: DragStartEvent) => {
    const dragData = event.active.data.current as DragData | undefined;
    if (dragData?.shift) setActiveShift(dragData.shift);
  }, []);

  const handleDragEnd = useCallback(async (event: DragEndEvent) => {
    setActiveShift(null);
    const { active, over } = event;
    if (!over) { console.log('[DnD] no drop target'); return; }

    const dragData = active.data.current as DragData | undefined;
    if (!dragData) return;

    // Parse droppable ID: "staffId_dateKey" (WeekGrid) or "staffId_lane" (DayTimeline)
    const targetId = over.id as string;
    const sepIdx = targetId.indexOf('_');
    if (sepIdx < 0) return;
    const targetStaffId = targetId.substring(0, sepIdx);
    const targetSuffix = targetId.substring(sepIdx + 1); // dateKey or "lane"
    const targetDateKey = targetSuffix !== 'lane' ? targetSuffix : null;

    // Determine if staff or day changed
    const shift = dragData.shift;
    const staffChanged = targetStaffId !== dragData.sourceStaffId;

    // Check if the day changed by comparing target dateKey to the shift's current day
    let dayChanged = false;
    let newClockIn = shift.clockinTime || '';
    let newClockOut = shift.clockoutTime || '';
    if (targetDateKey && shift.clockinTime) {
      const shiftDate = shift.clockinTime.substring(0, 10); // "YYYY-MM-DD"
      if (targetDateKey !== shiftDate) {
        dayChanged = true;
        // Keep the same time of day at the venue, on the new date. Resolving the
        // wall clock through the venue's zone (rather than doing string surgery
        // or reading the browser's clock) keeps this right across a DST change,
        // where the new date's offset may differ from the old one's.
        const tz = timePrefs.timeZone;
        const localIn = formatInTz(new Date(shift.clockinTime), tz); // YYYY-MM-DDTHH:MM:SS±HH:MM
        const [hh, mm] = localIn.substring(11, 16).split(':').map(Number);
        const newInInstant = wallClockToInstant(targetDateKey, hh * 60 + mm, tz);
        newClockIn = formatInTz(newInInstant, tz);
        if (shift.clockoutTime) {
          // Overnight shifts keep their duration, so the clockout may land on
          // the following day.
          const durationMs = new Date(shift.clockoutTime).getTime() - new Date(shift.clockinTime).getTime();
          newClockOut = formatInTz(new Date(newInInstant.getTime() + durationMs), tz);
        }
      }
    }

    if (!staffChanged && !dayChanged) { console.log('[DnD] same staff+day, ignoring'); return; }

    // Look up target staff name from staffRows
    const targetRow = staffRows.find(r => r.id === targetStaffId);
    const firstName = targetRow?.firstName ?? '';
    const lastName = targetRow?.lastName ?? '';

    console.log('[DnD] drop:', { shiftId: shift.id, from: dragData.sourceStaffId, to: targetStaffId, dayChanged, targetDateKey, workingDocId, threadId });

    // Optimistic local update + re-sort by clockinTime
    setShifts(prev => prev.map(s =>
      s.id === shift.id
        ? {
            ...s,
            staffMemberId: targetStaffId,
            staffMemberFirstName: firstName,
            staffMemberLastName: lastName,
            ...(dayChanged ? { clockinTime: newClockIn, clockoutTime: newClockOut } : {}),
          }
        : s
    ).sort((a, b) => (a.clockinTime || '').localeCompare(b.clockinTime || '')));

    const patchFields: Record<string, unknown> = {
      staffMemberId: targetStaffId,
      staffMemberFirstName: firstName,
      staffMemberLastName: lastName,
      rosterId: shift.rosterId || meta.rosterId,
      roleId: shift.roleId || '',
      roleName: shift.roleName || '',
      venueId: shift.venueId || '',
      hourlyRate: shift.hourlyRate ?? shift.adjustedHourlyRate ?? 0,
      clockinTime: dayChanged ? newClockIn : (shift.clockinTime || ''),
      clockoutTime: dayChanged ? newClockOut : (shift.clockoutTime || ''),
    };

    if (workingDocId && threadId) {
      await patchDoc([{
        op: 'update_shift',
        shift_id: shift.id,
        fields: patchFields,
      }]);
    } else if (onAction) {
      await onAction({
        connector_name: connectorName, action: 'update_shift',
        params: {
          shift_id: shift.id || '', roster_id: shift.rosterId || meta.rosterId,
          staff_member_id: targetStaffId, role_id: shift.roleId || '',
          clockin_time: newClockIn, clockout_time: newClockOut,
        },
      });
    }
  }, [staffRows, workingDocId, threadId, patchDoc, onAction, connectorName, meta.rosterId]);

  const handleResizeShift = useCallback(async (shiftId: string, clockinTime: string, clockoutTime: string) => {
    setShifts(prev => prev.map(s =>
      s.id === shiftId ? { ...s, clockinTime, clockoutTime } : s
    ).sort((a, b) => (a.clockinTime || '').localeCompare(b.clockinTime || '')));

    if (workingDocId && threadId) {
      const shift = shifts.find(s => s.id === shiftId);
      await patchDoc([{ op: 'update_shift', shift_id: shiftId, fields: {
        clockinTime, clockoutTime,
        rosterId: shift?.rosterId || meta.rosterId,
        staffMemberId: shift?.staffMemberId || '',
        roleId: shift?.roleId || '',
        roleName: shift?.roleName || '',
        venueId: shift?.venueId || '',
        hourlyRate: shift?.hourlyRate ?? (shift as Record<string, unknown>)?.adjustedHourlyRate ?? 0,
      } }]);
    } else if (onAction) {
      const shift = shifts.find(s => s.id === shiftId);
      await onAction({
        connector_name: connectorName, action: 'update_shift',
        params: {
          shift_id: shiftId, roster_id: shift?.rosterId || meta.rosterId,
          staff_member_id: shift?.staffMemberId || '', role_id: shift?.roleId || '',
          clockin_time: clockinTime, clockout_time: clockoutTime,
        },
      });
    }
  }, [shifts, workingDocId, threadId, patchDoc, onAction, connectorName, meta.rosterId]);

  const handleCreateShift = useCallback(async (staffId: string, clockinTime: string, clockoutTime: string) => {
    const row = staffRows.find(r => r.id === staffId);
    const fields: Record<string, unknown> = {
      staffMemberId: staffId,
      staffMemberFirstName: row?.firstName || '',
      staffMemberLastName: row?.lastName || '',
      clockinTime,
      clockoutTime,
      roleId: '',
      roleName: '',
    };

    if (workingDocId && threadId) {
      await patchDoc([{ op: 'add_shift', fields }]);
    } else if (onAction) {
      await onAction({
        connector_name: connectorName, action: 'create_rostered_shift',
        params: {
          staff_member_id: staffId, role_id: '', role_name: '',
          clockin_time: clockinTime, clockout_time: clockoutTime,
        },
      });
    }
  }, [staffRows, workingDocId, threadId, patchDoc, onAction, connectorName]);

  if (activeShifts.length === 0 && !addingNew) return null;

  // --- Toggle button style ---
  const toggleBtn = (mode: ViewMode, label: string) => (
    <button
      onClick={() => setViewMode(mode)}
      style={{
        padding: '3px 10px', fontSize: '0.72rem', fontWeight: viewMode === mode ? 600 : 400,
        border: '1px solid var(--line)',
        backgroundColor: viewMode === mode ? 'var(--text)' : 'var(--bg)',
        color: viewMode === mode ? 'var(--bg)' : 'var(--text-soft)',
        cursor: 'pointer', fontFamily: 'inherit',
        ...(mode === 'week' ? { borderRadius: '4px 0 0 4px' } : { borderRadius: '0 4px 4px 0', borderLeft: 'none' }),
      }}
    >{label}</button>
  );

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.75rem', flexWrap: 'wrap' }}>
        <span style={{ fontSize: '0.9rem', fontWeight: 600, color: 'var(--text)' }}>Roster</span>
        {venues.length > 1 && (
          <select
            value={selectedVenue || ''}
            onChange={e => handleVenueChange(e.target.value)}
            style={{
              padding: '3px 8px', fontSize: '0.75rem', border: '1px solid var(--line)',
              borderRadius: 6, fontFamily: 'inherit', color: 'var(--text-soft)', backgroundColor: 'var(--bg)',
            }}
          >
            {!selectedVenue && <option value="">Select venue</option>}
            {venues.map(v => (
              <option key={v.id} value={v.id}>{v.name}</option>
            ))}
          </select>
        )}

        {viewMode === 'week' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
            <button onClick={() => goWeek(-1)} disabled={loadingWeek} style={{
              border: 'none', background: 'none', cursor: loadingWeek ? 'default' : 'pointer',
              fontSize: '1rem', color: loadingWeek ? 'var(--line)' : 'var(--text-soft)', padding: '0 4px', fontFamily: 'inherit',
            }}>&lsaquo;</button>
            <label style={{ cursor: 'pointer', position: 'relative' }}>
              <span style={{ fontSize: '0.82rem', color: loadingWeek ? 'var(--muted-soft)' : 'var(--text-soft)', fontWeight: 500 }}>
                {loadingWeek ? 'Loading...' : dateRange || 'Select week'}
              </span>
              <input
                type="date"
                onChange={e => { if (e.target.value) goToDate(e.target.value); }}
                style={{
                  position: 'absolute', top: 0, left: 0, width: '100%', height: '100%',
                  opacity: 0, cursor: 'pointer',
                }}
              />
            </label>
            <button onClick={() => goWeek(1)} disabled={loadingWeek} style={{
              border: 'none', background: 'none', cursor: loadingWeek ? 'default' : 'pointer',
              fontSize: '1rem', color: loadingWeek ? 'var(--line)' : 'var(--text-soft)', padding: '0 4px', fontFamily: 'inherit',
            }}>&rsaquo;</button>
          </div>
        )}

        {viewMode === 'day' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
            <button onClick={() => goDay(-1)} disabled={!canPrev} style={{
              border: 'none', background: 'none', cursor: canPrev ? 'pointer' : 'default',
              fontSize: '0.9rem', color: canPrev ? 'var(--text-soft)' : 'var(--line)', padding: '0 4px', fontFamily: 'inherit',
            }}>&lsaquo;</button>
            <span style={{ fontSize: '0.82rem', color: 'var(--text-soft)', fontWeight: 500, minWidth: 90, textAlign: 'center' }}>
              {DAY_NAMES[effectiveDate.getDay()]} {effectiveDate.toLocaleDateString('en-NZ', { day: 'numeric', month: 'short' })}
            </span>
            <button onClick={() => goDay(1)} disabled={!canNext} style={{
              border: 'none', background: 'none', cursor: canNext ? 'pointer' : 'default',
              fontSize: '0.9rem', color: canNext ? 'var(--text-soft)' : 'var(--line)', padding: '0 4px', fontFamily: 'inherit',
            }}>&rsaquo;</button>
          </div>
        )}

        <span style={{ fontSize: '0.75rem', color: 'var(--muted-soft)' }}>
          {activeShifts.length} shifts
          {meta.totalHours > 0 && ` · ${meta.totalHours.toFixed(1)}h`}
        </span>
        {workingDocId && (
          <span title={syncStatus} style={{
            width: 8, height: 8, borderRadius: '50%', display: 'inline-block',
            backgroundColor: syncStatus === 'synced' ? 'var(--ok)' : syncStatus === 'syncing' || syncStatus === 'dirty' ? 'var(--warn)' : syncStatus === 'error' ? 'var(--error)' : 'var(--muted)',
          }} />
        )}

        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <div data-testid="roster-view-toggle">{toggleBtn('week', 'Week')}{toggleBtn('day', 'Day')}</div>
          {onAction && (
            <button onClick={() => { setAddingNew(true); setEditingShift(null); }} disabled={saving} style={{
              padding: '4px 12px', fontSize: '0.75rem', fontWeight: 500,
              border: '1px solid var(--ok)', borderRadius: 4, backgroundColor: 'var(--bg)',
              color: 'var(--ok)', cursor: 'pointer', fontFamily: 'inherit',
            }}>+ Add Shift</button>
          )}
        </div>
      </div>

      {/* Views — wrapped in DndContext */}
      <DndContext sensors={sensors} onDragStart={handleDragStart} onDragEnd={handleDragEnd}>
        {viewMode === 'week' && (
          <div data-testid="roster-week-view">
          <WeekGrid
            staffRows={staffRows}
            days={days}
            prefs={timePrefs}
            editingShiftId={editingShift?.id || null}
            onSelectShift={handleSelectShift}
            onSelectDay={handleSelectDay}
            interactive={!!onAction}
          />
          </div>
        )}

        {viewMode === 'day' && (
          <DayTimeline
            shifts={shifts}
            selectedDate={effectiveDate}
            prefs={timePrefs}
            editingShiftId={editingShift?.id || null}
            onSelectShift={handleSelectShift}
            onResizeShift={handleResizeShift}
            onCreateShift={handleCreateShift}
            interactive={!!onAction}
          />
        )}

        <DragOverlay dropAnimation={null}>
          {activeShift && (() => {
            const hrs = calcHours(activeShift.clockinTime, activeShift.clockoutTime);
            const color = roleColor(activeShift.roleId || '');
            return (
              <div style={{
                display: 'flex', alignItems: 'stretch', gap: 0,
                borderRadius: 4, overflow: 'hidden',
                border: '1px solid var(--focus)',
                backgroundColor: 'var(--bg)',
                boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
                fontSize: '0.78rem',
                width: 'max-content',
                opacity: 0.9,
              }}>
                <div style={{ width: 3, backgroundColor: color, flexShrink: 0 }} />
                <div style={{ padding: '3px 8px' }}>
                  <div style={{ fontWeight: 500, color: 'var(--text)', whiteSpace: 'nowrap' }}>
                    {formatClock(activeShift.clockinTime as string, timePrefs)}–{formatClock(activeShift.clockoutTime as string, timePrefs)}
                  </div>
                  {hrs > 0 && <div style={{ fontSize: '0.68rem', color: 'var(--muted)' }}>{hrs.toFixed(1)}h</div>}
                </div>
              </div>
            );
          })()}
        </DragOverlay>
      </DndContext>

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
