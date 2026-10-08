'use client';

import { useState, useEffect, useMemo, useCallback, useRef, type CSSProperties } from 'react';
import { DndContext, DragOverlay, PointerSensor, TouchSensor, useSensor, useSensors, type DragStartEvent, type DragEndEvent } from '@dnd-kit/core';
import { CalendarDays, ChevronLeft, ChevronRight, Plus, TriangleAlert } from 'lucide-react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import PageHeader from '../ui/PageHeader';
import VenueSelect from '../ui/VenueSelect';
import Button from '../ui/Button';
import IconButton from '../ui/IconButton';
import Icon from '../ui/Icon';
import Badge from '../ui/Badge';
import type { Shift, ShiftFormData, RosterMeta, DragData } from './roster/shared';
import { extractShifts, extractRosterMeta, venueWeekDays, dateKey, buildStaffRows, DAY_NAMES, calcHours, roleColor, OPEN_ROW_ID } from './roster/shared';
import { apiFetch, callComponentApi } from '../../lib/api';
import { useActiveVenue } from '../../hooks/useActiveVenue';
import { computeWarnings, summarise } from './roster/warnings';
import type { LeaveRecord, UnavailabilityRecord } from './roster/warnings';
import { venueTimePrefs, formatClock, formatInTz, wallClockToInstant, tradingWeekRange } from '../../lib/rosterTime';
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
  // Set once the venue list has answered (or failed), so a load that needs the
  // venue's timezone doesn't run on the defaults first.
  const [venuesLoaded, setVenuesLoaded] = useState(false);
  // The venue's whole staff roll and role list, so you can roster someone who
  // isn't on this week yet. Empty when embedded (no session to fetch with), in
  // which case we fall back to whoever is already on the roster.
  const [staffRoll, setStaffRoll] = useState<StaffOption[]>([]);
  const [roleRoll, setRoleRoll] = useState<RoleOption[]>([]);
  // Leave and unavailability for the week on screen, so the warnings can say
  // "Sam is on leave" rather than only "two shifts overlap".
  const [leave, setLeave] = useState<LeaveRecord[]>([]);
  const [unavailability, setUnavailability] = useState<UnavailabilityRecord[]>([]);

  const [selectedVenue, setSelectedVenue] = useState<string | null>(null);
  const activeVenueId = selectedVenue || (props?.activeVenueId as string) || null;
  // Persist the page's venue choice so other pages open on it. Only when this
  // is a PAGE instance (persistVenue) and not an MCP-embedded iframe — a roster
  // shown inside a conversation must not move the shared page venue.
  const persistVenue = !!props?.persistVenue && !props?.embedded;
  const [, setActiveVenue] = useActiveVenue();
  const [docVersion, setDocVersion] = useState<number>(1);
  const [syncStatus, setSyncStatus] = useState<string>('synced');
  const [syncError, setSyncError] = useState<string | null>(null);
  const [shifts, setShifts] = useState<Shift[]>(() => workingDocId ? [] : extractShifts(data));
  const [meta, setMeta] = useState<RosterMeta>(() => workingDocId ? { startDate: null, endDate: null, totalHours: 0, rosterId: '', publishedAt: null, lockedAt: null } : extractRosterMeta(data));
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
      .catch(() => {})
      .finally(() => setVenuesLoaded(true));
  }, [props?.embedded]);

  useEffect(() => {
    if (props?.embedded || !activeVenueId) return;
    let cancelled = false;
    const load = async (action: string): Promise<Record<string, unknown>[]> => {
      try {
        const res = await callComponentApi('roster_editor', action, {}, activeVenueId);
        const d = res?.data as unknown;
        return Array.isArray(d) ? (d as Record<string, unknown>[]) : [];
      } catch { return []; }
    };
    (async () => {
      const [staff, roles] = await Promise.all([load('staff_list'), load('roles_list')]);
      if (cancelled) return;
      setStaffRoll(staff
        .filter(r => !r.datestampDeleted)
        .map(r => ({ id: String(r.id), name: String(r.name || r.id) }))
        .sort((a, b) => a.name.localeCompare(b.name)));
      setRoleRoll(roles
        .filter(r => !r.datestampDeleted)
        .map(r => ({ id: String(r.id), name: String(r.name || r.id) }))
        .sort((a, b) => a.name.localeCompare(b.name)));
    })();
    return () => { cancelled = true; };
  }, [props?.embedded, activeVenueId]);

  // Reload roster for a different venue
  const handleVenueChange = useCallback(async (venueId: string) => {
    setSelectedVenue(venueId);
    if (persistVenue) setActiveVenue(venueId);
    // Get current week range for the new venue
    const now = new Date();
    const day = now.getDay();
    const monday = new Date(now);
    monday.setDate(now.getDate() - (day === 0 ? 6 : day - 1));
    monday.setHours(0, 0, 0, 0);

    try {
      const res = await apiFetch('/api/working-documents/from-connector', {
        method: 'POST',
        body: JSON.stringify({
          connector_name: connectorName,
          action: 'get_roster',
          params: { ...tradingWeekRange(dateKey(monday), timePrefs), venue_id: venueId },
          doc_type: 'roster',
          venue_id: venueId,
          // A chat card's document belongs to its conversation: each saved
          // edit is recorded there, and can't be recorded without one.
          thread_id: threadId || undefined,
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
  }, [persistVenue, setActiveVenue, connectorName, threadId]);

  // Fallback: update from props data (non-working-document mode)
  useEffect(() => {
    if (workingDocId) return;
    setShifts(extractShifts(data));
    setMeta(extractRosterMeta(data));
  }, [data, workingDocId]);

  // Timezone + business-day start for whichever venue is in view. Falls back to
  // Norm's defaults when the venue list isn't available (e.g. embedded in an MCP
  // host, where there's no session to fetch it with).
  const timePrefs = useMemo(
    () => venueTimePrefs(venues.find(v => v.id === activeVenueId) ?? null),
    [venues, activeVenueId],
  );

  // Venue clock, not the browser's: the roster's start is an instant, and
  // reading it locally shifts the whole week for an out-of-timezone viewer.
  const days = useMemo(() => venueWeekDays(meta.startDate, timePrefs), [meta.startDate, timePrefs]);

  // Leave + unavailability follow the week being viewed, not the venue alone.
  useEffect(() => {
    if (props?.embedded || !activeVenueId || days.length === 0) return;
    let cancelled = false;
    const from = dateKey(days[0]);
    const to = dateKey(days[days.length - 1]);
    (async () => {
      const fetchList = async (action: string): Promise<Record<string, unknown>[]> => {
        try {
          const res = await callComponentApi('roster_editor', action,
            { from_date: from, to_date: to }, activeVenueId);
          const d = res?.data as unknown;
          return Array.isArray(d) ? (d as Record<string, unknown>[]) : [];
        } catch { return []; }
      };
      const [lv, un] = await Promise.all([fetchList('leave_list'), fetchList('unavailability_list')]);
      if (cancelled) return;
      setLeave(lv as LeaveRecord[]);
      setUnavailability(un as UnavailabilityRecord[]);
    })();
    return () => { cancelled = true; };
  }, [props?.embedded, activeVenueId, days]);

  const staffRows = useMemo(() => buildStaffRows(shifts, days, timePrefs), [shifts, days, timePrefs]);
  // Problems in the roster as it currently stands. Pure, so the same result can
  // badge the grid and be read aloud by the agent — see roster/warnings.ts.
  const warnings = useMemo(
    () => (days.length ? computeWarnings(shifts, days, timePrefs, {}, { leave, unavailability }) : []),
    [shifts, days, timePrefs, leave, unavailability],
  );
  const activeShifts = shifts.filter(s => !s.datestampDeleted);
  // Computed, not read off the payload: the connector's totalHours is a
  // snapshot from load time and goes stale the moment anything is edited.
  const rosteredHours = useMemo(
    () => activeShifts.reduce((sum, s) => sum + calcHours(s.clockinTime, s.clockoutTime), 0),
    [activeShifts],
  );
  const openShiftCount = useMemo(
    () => activeShifts.filter(s => !s.staffMemberId).length,
    [activeShifts],
  );

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
    const derived = Array.from(map.entries())
      .map(([id, name]) => ({ id, name }))
      .sort((a, b) => a.name.localeCompare(b.name));
    return staffRoll.length ? staffRoll : derived;
  }, [activeShifts, staffRoll]);

  const roleOptions = useMemo<RoleOption[]>(() => {
    const map = new Map<string, string>();
    for (const s of activeShifts) {
      if (s.roleId && !map.has(s.roleId)) {
        map.set(s.roleId, s.roleName || s.roleId);
      }
    }
    const derived = Array.from(map.entries())
      .map(([id, name]) => ({ id, name }))
      .sort((a, b) => a.name.localeCompare(b.name));
    return roleRoll.length ? roleRoll : derived;
  }, [activeShifts, roleRoll]);

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
    try {
      const res = await apiFetch('/api/working-documents/from-connector', {
        method: 'POST',
        body: JSON.stringify({
          connector_name: connectorName,
          action: 'get_roster',
          params: { ...tradingWeekRange(dateKey(monday), timePrefs), venue_id: venueId },
          doc_type: 'roster',
          venue_id: venueId,
          // A chat card's document belongs to its conversation: each saved
          // edit is recorded there, and can't be recorded without one.
          thread_id: threadId || undefined,
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
  }, [selectedVenue, props?.activeVenueId, timePrefs, connectorName, threadId]);

  // In a conversation the card arrives with the agent's copy of the roster.
  // That copy is trimmed for the model — no shift ids, rates or pay rules — so
  // nothing on it could be saved: a click on a shift did nothing. Load the same
  // week the way the Roster page does, as a working document with every field,
  // and every edit then saves through it (Oct 2026). Not when embedded: an MCP
  // host has no session to load with, and its card stays read-only.
  const upgraded = useRef(false);
  useEffect(() => {
    if (upgraded.current || workingDocId || props?.embedded) return;
    if (!activeVenueId || !venuesLoaded || days.length === 0) return;
    upgraded.current = true;
    const monday = new Date(days[0]);
    monday.setHours(0, 0, 0, 0);
    loadWeek(monday);
  }, [workingDocId, props?.embedded, activeVenueId, venuesLoaded, days, loadWeek]);

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

  /**
   * Fields core-api clears if you don't send them back.
   *
   * The shift endpoints are read-modify-write: a PUT replaces the whole shift,
   * so omitting `breaks` deletes the breaks and omitting `rules` blanks the
   * rostered times. Every write must carry the current values through.
   */
  const preserved = useCallback((shift: Shift | null | undefined) => ({
    breaks: shift?.breaks ?? [],
    rules: (shift?.rules as unknown[]) ?? [],
    remunerationType: (shift?.remunerationType as string) ?? 'HourlyRate',
  }), []);

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
        setDocData(updated.data);
        setDocVersion(updated.version);
        setSyncStatus(updated.sync_status);
        setShifts(extractShifts(updated.data));
        setMeta(extractRosterMeta(updated.data));
        // The write to Loaded happens after this returns. Read the document
        // again until it has: the dot then says whether it worked (it stayed
        // amber forever, and a failed write was never shown), and a shift you
        // just added gets the id Loaded gave it, so it can be edited or
        // deleted without reloading the week (Oct 2026).
        for (let i = 0; i < 15 && ['dirty', 'syncing'].includes(updated.sync_status); i++) {
          await new Promise(r => setTimeout(r, 1500));
          const doc = await apiFetch(docUrl).then(r => (r.ok ? r.json() : null)).catch(() => null);
          if (!doc || ['dirty', 'syncing'].includes(doc.sync_status)) continue;
          setDocData(doc.data); setDocVersion(doc.version);
          setSyncStatus(doc.sync_status); setSyncError(doc.sync_error || null);
          setShifts(extractShifts(doc.data)); setMeta(extractRosterMeta(doc.data));
          break;
        }
      } else if (res.status === 409) {
        // Someone else changed the document. Our optimistic edit was rejected,
        // so reload rather than leaving state the server never accepted.
        setSyncStatus('conflict');
        const fresh = await apiFetch(docUrl).then(r => r.ok ? r.json() : null).catch(() => null);
        if (fresh) {
          setDocData(fresh.data); setDocVersion(fresh.version);
          setShifts(extractShifts(fresh.data)); setMeta(extractRosterMeta(fresh.data));
        }
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
      if (workingDocId) {
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
              ...preserved(editingShift),
              // The modal may have edited these, so they win over the originals.
              breaks: formData.breaks ?? editingShift.breaks ?? [],
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
              breaks: formData.breaks ?? [],
            },
          }]);
        }
      } else if (onAction) {
        if (editingShift) {
          await onAction({
            connector_name: connectorName, action: 'update_shift',
            params: {
              shift_id: editingShift.id || '', roster_id: editingShift.rosterId || meta.rosterId,
              staff_member_id: formData.staff_member_id || null, role_id: formData.role_id,
              clockin_time: formData.clockin_time, clockout_time: formData.clockout_time,
              ...preserved(editingShift),
              breaks: formData.breaks ?? editingShift.breaks ?? [],
            },
          });
        } else {
          await onAction({
            connector_name: connectorName, action: 'create_rostered_shift',
            params: {
              roster_id: meta.rosterId, staff_member_id: formData.staff_member_id || null,
              role_id: formData.role_id, clockin_time: formData.clockin_time,
              clockout_time: formData.clockout_time,
              breaks: formData.breaks ?? [],
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
      if (workingDocId) {
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
      ...preserved(shift),
    };
    // An unassigned target is an OPEN shift. core-api represents that as a null
    // staffMemberId; sending a placeholder string would write junk to the roster.
    if (!targetStaffId || targetStaffId === OPEN_ROW_ID) {
      patchFields.staffMemberId = null;
      patchFields.staffMemberFirstName = '';
      patchFields.staffMemberLastName = '';
    }

    if (workingDocId) {
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
          staff_member_id: (!targetStaffId || targetStaffId === OPEN_ROW_ID) ? null : targetStaffId,
          role_id: shift.roleId || '',
          clockin_time: newClockIn, clockout_time: newClockOut,
          ...preserved(shift),
        },
      });
    }
  }, [staffRows, workingDocId, patchDoc, onAction, connectorName, meta.rosterId, preserved]);

  const handleResizeShift = useCallback(async (shiftId: string, clockinTime: string, clockoutTime: string) => {
    setShifts(prev => prev.map(s =>
      s.id === shiftId ? { ...s, clockinTime, clockoutTime } : s
    ).sort((a, b) => (a.clockinTime || '').localeCompare(b.clockinTime || '')));

    if (workingDocId) {
      const shift = shifts.find(s => s.id === shiftId);
      await patchDoc([{ op: 'update_shift', shift_id: shiftId, fields: {
        clockinTime, clockoutTime,
        rosterId: shift?.rosterId || meta.rosterId,
        staffMemberId: shift?.staffMemberId ?? null,
        roleId: shift?.roleId || '',
        roleName: shift?.roleName || '',
        venueId: shift?.venueId || '',
        hourlyRate: shift?.hourlyRate ?? (shift as Record<string, unknown>)?.adjustedHourlyRate ?? 0,
        ...preserved(shift),
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
  }, [shifts, workingDocId, patchDoc, onAction, connectorName, meta.rosterId, preserved]);

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

    if (workingDocId) {
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

  // An empty week still renders: the header, week navigation and the add
  // button are how you start a roster from nothing. Returning null here meant
  // an empty week had no UI at all, so a new week could never be begun.

  // --- Header ---
  // A PAGE gets the shared page header: the title with the roster's state
  // beside it, the week as its meta line, and the venue, week and view
  // controls on the right with the one primary. In a conversation, or inside
  // Claude, the same pieces sit in one compact row.
  const isPage = persistVenue;
  const weekLabel = loadingWeek ? 'Loading…' : (dateRange || 'Select week');
  const stats = [
    `${activeShifts.length} ${activeShifts.length === 1 ? 'shift' : 'shifts'}`,
    rosteredHours > 0 ? `${rosteredHours.toFixed(1)}h` : null,
    openShiftCount > 0 ? `${openShiftCount} open` : null,
  ].filter(Boolean).join(' · ');

  const statusBadge = (
    <Badge
      tone={meta.lockedAt ? 'neutral' : meta.publishedAt ? 'ok' : 'info'}
      title={meta.lockedAt
        ? 'This roster is locked — edits may be rejected upstream.'
        : meta.publishedAt
          ? `Published ${new Date(meta.publishedAt).toLocaleDateString('en-NZ')} — staff can see it`
          : 'Draft — staff cannot see this yet'}
    >
      {meta.lockedAt ? 'Locked' : meta.publishedAt ? 'Published' : 'Draft'}
    </Badge>
  );
  // The full list is in the tooltip; the pill only counts.
  const warningsBadge = warnings.length > 0 && (
    <span style={{ display: 'inline-flex', cursor: 'help' }}>
      <Badge
        tone={warnings.some(w => w.severity === 'error') ? 'error' : 'warn'}
        title={warnings.map(w => `• ${w.message}`).join('\n')}
      >
        <Icon icon={TriangleAlert} size={12} />
        {summarise(warnings)}
      </Badge>
    </span>
  );
  const syncDot = workingDocId && (
    <span title={syncError || syncStatus} style={{
      width: 8, height: 8, borderRadius: '50%', display: 'inline-block', flex: '0 0 auto',
      backgroundColor: syncStatus === 'synced' ? 'var(--ok)' : syncStatus === 'syncing' || syncStatus === 'dirty' ? 'var(--warn)' : syncStatus === 'error' ? 'var(--error)' : 'var(--muted)',
    }} />
  );

  // Shows the venue whose roster is on screen — the page's venue until you
  // pick another here.
  const venuePicker = venues.length > 1 && (
    <VenueSelect venues={venues} value={venues.some(v => v.id === activeVenueId) ? activeVenueId : null} onChange={handleVenueChange} />
  );

  // A date field laid invisibly over its trigger: picking a date loads that
  // date's week. A click on an invisible field only focuses it in desktop
  // browsers, so it opens the picker itself (not allowed in a cross-origin
  // frame such as Claude's — there the field behaves as before).
  const weekPicker = (
    <input
      type="date"
      aria-label="Go to the week of a date"
      onClick={e => { try { e.currentTarget.showPicker(); } catch { /* unsupported or blocked */ } }}
      onChange={e => { if (e.target.value) goToDate(e.target.value); }}
      style={{
        position: 'absolute', top: 0, left: 0, width: '100%', height: '100%',
        margin: 0, padding: 0, border: 0, opacity: 0, cursor: 'pointer',
      }}
    />
  );
  const weekNav = (
    <div role="group" aria-label="Week" style={{ display: 'inline-flex', alignItems: 'center' }}>
      <IconButton icon={ChevronLeft} label="Previous week" onClick={() => goWeek(-1)} disabled={loadingWeek} />
      {isPage ? (
        // The page's meta line already names the week, so here it is an icon.
        <label className="n-icon-btn" title="Go to the week of a date" style={{ position: 'relative' }}>
          <Icon icon={CalendarDays} size={18} />
          {weekPicker}
        </label>
      ) : (
        <label style={{
          position: 'relative', padding: '0 4px', cursor: 'pointer', whiteSpace: 'nowrap',
          fontSize: 'var(--fs-sm)', fontWeight: 500, color: loadingWeek ? 'var(--muted)' : 'var(--text)',
        }}>
          {weekLabel}
          {weekPicker}
        </label>
      )}
      <IconButton icon={ChevronRight} label="Next week" onClick={() => goWeek(1)} disabled={loadingWeek} />
    </div>
  );
  const dayNav = (
    <div role="group" aria-label="Day" style={{ display: 'inline-flex', alignItems: 'center' }}>
      <IconButton icon={ChevronLeft} label="Previous day" onClick={() => goDay(-1)} disabled={!canPrev} />
      <span style={{
        minWidth: 90, padding: '0 4px', textAlign: 'center', whiteSpace: 'nowrap',
        fontSize: 'var(--fs-sm)', fontWeight: 500, color: 'var(--text)',
      }}>
        {DAY_NAMES[effectiveDate.getDay()]} {effectiveDate.toLocaleDateString('en-NZ', { day: 'numeric', month: 'short' })}
      </span>
      <IconButton icon={ChevronRight} label="Next day" onClick={() => goDay(1)} disabled={!canNext} />
    </div>
  );

  // Week | Day: two joined secondary buttons; the chosen one takes the
  // selected fill and tan edge.
  const viewButton = (mode: ViewMode, label: string) => {
    const on = viewMode === mode;
    const style: CSSProperties = {
      ...(mode === 'week'
        ? { borderTopRightRadius: 0, borderBottomRightRadius: 0 }
        : { borderTopLeftRadius: 0, borderBottomLeftRadius: 0, marginLeft: -1 }),
      ...(on
        ? { position: 'relative', zIndex: 1, background: 'var(--selected)', borderColor: 'var(--brand-soft)', fontWeight: 600 }
        : { color: 'var(--text-soft)' }),
    };
    return (
      <Button size={isPage ? 'md' : 'sm'} aria-pressed={on} onClick={() => setViewMode(mode)} style={style}>
        {label}
      </Button>
    );
  };
  const viewToggle = (
    <div data-testid="roster-view-toggle" role="group" aria-label="View" style={{ display: 'inline-flex' }}>
      {viewButton('week', 'Week')}{viewButton('day', 'Day')}
    </div>
  );

  const addButton = onAction && (
    <Button
      variant="primary"
      size={isPage ? 'md' : 'sm'}
      icon={Plus}
      onClick={() => { setAddingNew(true); setEditingShift(null); }}
      disabled={saving}
    >
      Add shift
    </Button>
  );

  return (
    <div>
      {isPage ? (
        <PageHeader
          title="Roster"
          status={<>{statusBadge}{warningsBadge}{syncDot}</>}
          meta={`${weekLabel} · ${stats}`}
          actions={<>{venuePicker}{viewMode === 'week' ? weekNav : dayNav}{viewToggle}{addButton}</>}
        />
      ) : (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)', marginRight: 4 }}>Roster</span>
          {venuePicker}
          {viewMode === 'week' ? weekNav : dayNav}
          <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', whiteSpace: 'nowrap' }}>{stats}</span>
          {warningsBadge}
          {statusBadge}
          {syncDot}
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
            {viewToggle}
            {addButton}
          </div>
        </div>
      )}

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
            interactive={!!onAction || !!workingDocId}
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
            interactive={!!onAction || !!workingDocId}
          />
        )}

        <DragOverlay dropAnimation={null}>
          {activeShift && (() => {
            const hrs = calcHours(activeShift.clockinTime, activeShift.clockoutTime);
            const color = roleColor(activeShift.roleId || '');
            return (
              <div style={{
                display: 'flex', alignItems: 'stretch', gap: 0,
                borderRadius: 'var(--radius-sm)', overflow: 'hidden',
                border: '1px solid var(--focus)',
                backgroundColor: 'var(--bg)',
                boxShadow: '0 6px 18px rgba(26, 26, 26, 0.16)',
                fontSize: 'var(--fs-xs)',
                width: 'max-content',
                opacity: 0.95,
              }}>
                <div style={{ width: 3, backgroundColor: color, flexShrink: 0 }} />
                <div style={{ padding: '3px 8px' }}>
                  <div style={{ fontWeight: 500, color: 'var(--text)', whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>
                    {formatClock(activeShift.clockinTime as string, timePrefs)}–{formatClock(activeShift.clockoutTime as string, timePrefs)}
                  </div>
                  {hrs > 0 && <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--muted)' }}>{hrs.toFixed(1)}h</div>}
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
