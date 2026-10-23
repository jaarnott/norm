'use client';

import { Fragment, useState, useEffect, useCallback, useId, useMemo, useRef, type CSSProperties, type ReactNode } from 'react';
import { ChevronDown, ChevronRight, Info, Plus, RefreshCw, Upload, UserPlus } from 'lucide-react';
import { apiFetch, getStoredUser } from '../../lib/api';
import { can } from '../../lib/permissions';
import { AGENTS } from '../layout/Sidebar';
import { COMING_APPS } from './RolesPanel';
import { useBreakpoint } from '../../hooks/useBreakpoint';
import Avatar from '../ui/Avatar';
import Badge, { type BadgeTone } from '../ui/Badge';
import Button from '../ui/Button';
import Dialog, { ConfirmDialog } from '../ui/Dialog';
import IconButton from '../ui/IconButton';
import PageState from '../ui/PageState';

/**
 * Settings → People: everyone in the organisation — office users who were
 * invited, and venue staff copied from Loaded or added by hand — with each
 * person's role, venues, status and tablet PIN; the Loaded staff switch; and
 * the venue tablets. It replaced the Users tab (docs/plans/staff-access.md,
 * 11.7). The tab keeps its id, `members`, so saved tests still find it.
 */

// --- What the API returns ---

/** One person from GET /api/organizations/{org}/people (plan 11.6). */
interface Person {
  /** member = an invited account; staff = a directory row with no account yet; both = one person, both records. */
  kind: 'member' | 'staff' | 'both';
  user_id: string | null;
  staff_id: string | null;
  name: string | null;
  email: string | null;
  phone: string | null;
  role_id: string | null;
  role_name: string | null;
  venue_ids: string[];
  is_active: boolean;
  /** loadedhub | manual, or null for an office user with no staff record. */
  source: string | null;
  loaded_roles: string[];
  signed_in: boolean;
  invited: boolean;
  last_seen_at: string | null;
  has_pin: boolean;
}

interface PeopleRole {
  id: string;
  name: string;
  display_name: string;
  is_system: boolean;
  /** From GET .../roles, for the "what does this role allow" dialog. */
  permissions?: string[];
  agents?: string[] | null;
  apps?: string[] | null;
}

interface PeopleVenue { id: string; name: string }

interface SyncSummary {
  added: number;
  updated: number;
  deactivated: number;
  reactivated: number;
  errors?: string[];
}

interface SyncStatus {
  enabled: boolean;
  loaded_connected: boolean;
  last_run_at: string | null;
  last_summary: SyncSummary | null;
}

interface Device {
  id: string;
  name: string;
  venue_id: string;
  venue_name: string | null;
  pin_required: boolean;
  role_id: string | null;
  created_at: string | null;
  last_seen_at: string | null;
}

interface ImportResult {
  created: number;
  updated: number;
  errors: { line: number; message: string }[];
}

// --- Usage figures (kept from the Users tab: cost and billable tokens per person) ---

interface UsageFigures {
  input_tokens: number; output_tokens: number; llm_call_count: number;
  cache_read_tokens?: number; cache_write_tokens?: number;
  billable_tokens?: number; cost_usd?: number;
}
interface UsageBreakdown {
  by_kind: Record<string, { calls: number; cost_usd: number; billable_tokens: number }>;
  top_threads: { thread_id: string; title: string; calls: number; cost_usd: number; billable_tokens: number; cache_ratio: number | null }[];
}
interface UsageTotals { input: number; output: number; cacheRead: number; cacheWrite: number; billable: number; cost: number; calls: number }
const NO_USAGE: UsageTotals = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, billable: 0, cost: 0, calls: 0 };

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
  return String(n);
}

function fmtUsd(n: number | undefined): string {
  return `$${(n || 0).toFixed(2)}`;
}

function usageTitle(d: UsageFigures): string {
  return [
    `Cost ${fmtUsd(d.cost_usd)}`,
    `Billable ${(d.billable_tokens || 0).toLocaleString()}`,
    `Full-price input ${(d.input_tokens || 0).toLocaleString()}`,
    `Cache read ${(d.cache_read_tokens || 0).toLocaleString()}`,
    `Cache write ${(d.cache_write_tokens || 0).toLocaleString()}`,
    `Output ${(d.output_tokens || 0).toLocaleString()}`,
  ].join('\n');
}

/** A figure under its small label (the usage summaries). */
function Stat({ label, value, unit, large = false }: { label: string; value: ReactNode; unit?: string; large?: boolean }) {
  return (
    <div>
      <div className="n-eyebrow">{label}</div>
      <div style={{ marginTop: 2, fontSize: large ? 'var(--fs-lg)' : 'var(--fs-base)', fontWeight: 600, color: 'var(--text)', fontVariantNumeric: 'tabular-nums' }}>
        {value}
        {unit && <span style={{ marginLeft: 4, fontSize: 'var(--fs-xs)', fontWeight: 400, color: 'var(--muted)' }}>{unit}</span>}
      </div>
    </div>
  );
}

/** Daily bars by cost — the figure that matters — with every token on hover. */
function DailyCostBars({ days, compact }: { days: Record<string, UsageFigures>; compact?: boolean }) {
  const entries = Object.entries(days).sort(([a], [b]) => a.localeCompare(b));
  if (entries.length === 0) return <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>No daily data yet</div>;
  const max = Math.max(...entries.map(([, d]) => d.cost_usd || 0), 0.0001);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: compact ? 2 : 4 }}>
      {entries.map(([date, d]) => {
        const label = new Date(date + 'T00:00:00').toLocaleDateString('en-NZ', compact
          ? { weekday: 'short', day: 'numeric' } : { weekday: 'short', day: 'numeric', month: 'short' });
        return (
          <div key={date} style={{ display: 'flex', alignItems: 'center', gap: 8 }} title={usageTitle(d)}>
            <span style={{ width: compact ? 56 : 84, flexShrink: 0, textAlign: 'right', fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{label}</span>
            <div style={{ flex: 1, height: compact ? 10 : 12, overflow: 'hidden', borderRadius: 3, backgroundColor: 'var(--surface-alt)' }}>
              <div style={{ width: `${((d.cost_usd || 0) / max) * 100}%`, height: '100%', backgroundColor: 'var(--accent)' }} />
            </div>
            <span style={{ width: 96, flexShrink: 0, fontSize: 'var(--fs-xs)', color: 'var(--muted)', fontVariantNumeric: 'tabular-nums' }}>
              {fmtUsd(d.cost_usd)} · {fmtTokens(d.billable_tokens || 0)}
            </span>
          </div>
        );
      })}
      <div style={{ marginTop: 4, paddingLeft: compact ? 64 : 92, fontSize: 'var(--fs-2xs)', color: 'var(--muted)' }}>
        Cost · billable tokens per day — hover a day for every token type
      </div>
    </div>
  );
}

const KIND_LABEL: Record<string, string> = { chat: 'Chat', 'invoice extraction': 'Invoice extraction', other: 'Other' };

/** Where the month's cost went: by kind of work, and the costliest chats. */
function UsageBreakdownPanel({ data }: { data: UsageBreakdown | null }) {
  if (!data) return <div style={{ marginTop: 12, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>Loading breakdown…</div>;
  const kinds = Object.entries(data.by_kind).sort(([, a], [, b]) => b.cost_usd - a.cost_usd);
  const total = kinds.reduce((t, [, k]) => t + k.cost_usd, 0) || 1;
  const row: CSSProperties = { display: 'flex', gap: 12, padding: '2px 0', fontSize: 'var(--fs-sm)', color: 'var(--text)', fontVariantNumeric: 'tabular-nums' };
  return (
    <div>
      <div className="n-eyebrow" style={{ margin: '16px 0 6px' }}>By kind of work</div>
      {kinds.map(([kind, k]) => (
        <div key={kind} style={row}>
          <span style={{ width: 140 }}>{KIND_LABEL[kind] || kind}</span>
          <span style={{ width: 70, fontWeight: 600 }}>{fmtUsd(k.cost_usd)}</span>
          <span style={{ width: 44, color: 'var(--muted)' }}>{Math.round((k.cost_usd / total) * 100)}%</span>
          <span style={{ color: 'var(--muted)' }}>{k.calls} calls · {fmtTokens(k.billable_tokens)} billable</span>
        </div>
      ))}
      {data.top_threads.length > 0 && (
        <>
          <div className="n-eyebrow" style={{ margin: '16px 0 6px' }}>Most expensive chats</div>
          {data.top_threads.map(t => (
            <div key={t.thread_id} style={row}
              title="Cache ratio: tokens read back from the cache for each token written — higher is better">
              <span style={{ width: 70, fontWeight: 600 }}>{fmtUsd(t.cost_usd)}</span>
              <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.title || t.thread_id}</span>
              <span style={{ color: 'var(--muted)', whiteSpace: 'nowrap' }}>
                {t.calls} calls{t.cache_ratio != null ? ` · cache ${t.cache_ratio}:1` : ''}
              </span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

// --- Shared bits ---

/** A section's header row: 18px title, a muted count, actions on the right. */
function SectionHeader({ title, meta, actions }: { title: string; meta?: ReactNode; actions?: ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', minWidth: 0 }}>
        <h2 style={{ margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>{title}</h2>
        {meta && <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{meta}</span>}
      </div>
      {actions && <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>{actions}</div>}
    </div>
  );
}

/** On/off, the way the Connections and AI team pages draw it. */
function Switch({ checked, onChange, label, disabled, testId }: { checked: boolean; onChange: () => void; label: string; disabled?: boolean; testId?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={onChange}
      data-testid={testId}
      style={{
        display: 'flex', alignItems: 'center', justifyContent: checked ? 'flex-end' : 'flex-start',
        flex: '0 0 auto', width: 36, height: 20, boxSizing: 'border-box', padding: 2,
        border: 'none', borderRadius: 999, cursor: disabled ? 'default' : 'pointer',
        backgroundColor: checked ? 'var(--primary)' : 'var(--field)',
        opacity: disabled ? 0.45 : 1,
        transition: 'background-color 0.2s',
      }}
    >
      <span style={{ width: 16, height: 16, borderRadius: '50%', backgroundColor: 'var(--bg)' }} />
    </button>
  );
}

/**
 * Runs a write and throws when it fails, with the API's reason — what a
 * ConfirmDialog shows in place of failing silently.
 */
async function mustSucceed(call: () => Promise<Response>, failed: string): Promise<void> {
  let res: Response;
  try {
    res = await call();
  } catch {
    throw new Error(`${failed} — check your connection and try again.`);
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail = typeof body?.detail === 'string' ? body.detail : `error ${res.status}`;
    throw new Error(`${failed}: ${detail}`);
  }
}

/** "just now", "12 minutes ago", "3 hours ago", "yesterday", else the date. */
function timeAgo(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return null;
  const mins = Math.round((Date.now() - t) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} minute${mins === 1 ? '' : 's'} ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
  const days = Math.round(hours / 24);
  if (days === 1) return 'yesterday';
  if (days < 14) return `${days} days ago`;
  return new Date(iso).toLocaleDateString();
}

/** Text for screen readers only (an icon column's header). */
const srOnly: CSSProperties = { position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)', whiteSpace: 'nowrap' };
/** An open row is selected, joined to its details band below (as on Orders). */
const expandedCell: CSSProperties = { backgroundColor: 'var(--selected)', borderBottom: 'none' };
const small: CSSProperties = { fontSize: 'var(--fs-sm)', color: 'var(--muted)' };
const checkLabel: CSSProperties = { display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--text)', cursor: 'pointer' };

const LOADED = 'loadedhub';

/** One key per person: the staff row when there is one, else the account. */
const personKey = (p: Person) => (p.staff_id ? `s:${p.staff_id}` : `u:${p.user_id}`);

/** Signed in · Invited · Not yet · Left */
function statusOf(p: Person): { label: string; tone: BadgeTone } {
  if (!p.is_active) return p.invited ? { label: 'Invited', tone: 'warn' } : { label: 'Left', tone: 'neutral' };
  if (p.signed_in) return { label: 'Signed in', tone: 'ok' };
  return { label: 'Not yet', tone: 'neutral' };
}

type Filter = 'all' | 'office' | 'staff' | 'not_signed_in';
const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'office', label: 'Office' },
  { id: 'staff', label: 'Venue staff' },
  { id: 'not_signed_in', label: 'Not signed in' },
];

function matchesFilter(p: Person, f: Filter): boolean {
  switch (f) {
    case 'office': return p.kind === 'member';
    case 'staff': return p.staff_id !== null;
    case 'not_signed_in': return !p.signed_in && (p.is_active || p.invited);
    default: return true;
  }
}

/** The form behind Add staff and Edit details. */
interface StaffForm { id: string | null; name: string; email: string; phone: string; venue_ids: string[]; role_id: string }
const EMPTY_STAFF: StaffForm = { id: null, name: '', email: '', phone: '', venue_ids: [], role_id: '' };

export default function PeoplePanel({ orgId, orgName }: { orgId: string | null; orgName?: string | null }) {
  const user = getStoredUser();
  // org:members — inviting, adding staff, roles, venues, PINs, removing.
  // org:manage — the Loaded switch, Sync now, and the tablets.
  const canManage = can(user, 'org:members');
  const canManageOrg = can(user, 'org:manage');
  const { isMobile } = useBreakpoint();
  const formId = useId();

  const [people, setPeople] = useState<Person[]>([]);
  const [roles, setRoles] = useState<PeopleRole[]>([]);
  // App key (built-in slug or custom:<slug>) -> name, for the role dialog.
  const [appNames, setAppNames] = useState<Record<string, string>>({});
  const [venues, setVenues] = useState<PeopleVenue[]>([]);
  const [sync, setSync] = useState<SyncStatus | null>(null);
  const [devices, setDevices] = useState<Device[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>('all');
  const [expanded, setExpanded] = useState<string | null>(null);

  // Usage this month: per person (by user id) and the organisation's total.
  const [usage, setUsage] = useState<Record<string, UsageFigures>>({});
  const [usageTotals, setUsageTotals] = useState<UsageTotals>(NO_USAGE);
  const [usageBreakdown, setUsageBreakdown] = useState<UsageBreakdown | null>(null);
  const [personDailyUsage, setPersonDailyUsage] = useState<Record<string, Record<string, UsageFigures>>>({});
  const [showDailyUsage, setShowDailyUsage] = useState(false);
  const [dailyUsage, setDailyUsage] = useState<Record<string, UsageFigures>>({});

  // Invite (office users) — the same dialog and endpoint as before.
  const [showInvite, setShowInvite] = useState(false);
  const [addEmail, setAddEmail] = useState('');
  const [addRole, setAddRole] = useState('');
  const [addVenueIds, setAddVenueIds] = useState<string[]>([]);
  const [addError, setAddError] = useState('');
  const [addSuccess, setAddSuccess] = useState('');
  const [resendStatus, setResendStatus] = useState<Record<string, 'sending' | 'sent' | 'failed'>>({});

  // Add staff / edit a manual staff member.
  const [staffForm, setStaffForm] = useState<StaffForm | null>(null);
  const [staffError, setStaffError] = useState('');
  const [savingStaff, setSavingStaff] = useState(false);

  // Import a spreadsheet.
  const [showImport, setShowImport] = useState(false);
  const [csvText, setCsvText] = useState('');
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState('');
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // A PIN just made, shown once.
  const [pinShown, setPinShown] = useState<{ name: string; pin: string } | null>(null);
  const [pinBusy, setPinBusy] = useState<string | null>(null);

  // Who is waiting on "Remove …?" / which tablet on "Sign out …?".
  const [removing, setRemoving] = useState<Person | null>(null);
  const [revoking, setRevoking] = useState<Device | null>(null);
  const [viewingRoleId, setViewingRoleId] = useState<string | null>(null);
  // Why the last role, venue or PIN change didn't stick, beside its control.
  const [changeError, setChangeError] = useState<{ key: string; field: 'role' | 'venues' | 'pin'; message: string } | null>(null);
  const [syncBusy, setSyncBusy] = useState<'toggle' | 'sync' | null>(null);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [deviceBusy, setDeviceBusy] = useState<string | null>(null);
  const [deviceError, setDeviceError] = useState<string | null>(null);

  const loadPeople = useCallback(async () => {
    if (!orgId) return;
    try {
      const res = await apiFetch(`/api/organizations/${orgId}/people`);
      if (!res.ok) {
        setLoadError(res.status === 403
          ? 'You don’t have permission to see the people in this organisation.'
          : `The people list couldn’t be loaded (error ${res.status}).`);
        return;
      }
      const data = await res.json();
      setPeople((data.people || []) as Person[]);
      setVenues((data.venues || []) as PeopleVenue[]);
      setSync((data.sync as SyncStatus | null) ?? null);
      setLoadError(null);
      // The roles endpoint carries permissions and reach for the info dialog;
      // the people payload's roles are the fallback for the picker.
      const rolesRes = await apiFetch(`/api/organizations/${orgId}/roles`).catch(() => null);
      if (rolesRes?.ok) {
        const rolesData = await rolesRes.json();
        setRoles((rolesData.roles || []) as PeopleRole[]);
      } else {
        setRoles((data.roles || []) as PeopleRole[]);
      }
      // Plain names for the "what this role allows" dialog, not app keys.
      const appsRes = await apiFetch('/api/apps').catch(() => null);
      if (appsRes?.ok) {
        const appsData = await appsRes.json();
        const names: Record<string, string> = {};
        for (const a of (appsData.apps || []) as { slug: string; name: string; builtin?: boolean }[]) {
          names[a.builtin ? a.slug : `custom:${a.slug}`] = a.name;
        }
        setAppNames(names);
      }
    } catch {
      setLoadError('The people list couldn’t be loaded — check your connection and try again.');
    }
  }, [orgId]);

  const loadUsage = useCallback(async () => {
    if (!orgId) return;
    try {
      const res = await apiFetch(`/api/organizations/${orgId}/usage`);
      if (!res.ok) return;
      const d = await res.json();
      setUsage(d.by_user || {});
      setUsageTotals({
        input: d.total_input_tokens || 0,
        output: d.total_output_tokens || 0,
        cacheRead: d.total_cache_read_tokens || 0,
        cacheWrite: d.total_cache_write_tokens || 0,
        billable: d.total_billable_tokens || 0,
        cost: d.total_cost_usd || 0,
        calls: d.total_llm_calls || 0,
      });
    } catch { /* usage is a nicety */ }
  }, [orgId]);

  const loadDevices = useCallback(async () => {
    if (!orgId || !canManageOrg) return;
    try {
      const res = await apiFetch(`/api/organizations/${orgId}/devices`);
      if (res.ok) {
        const d = await res.json();
        setDevices((d.devices || []) as Device[]);
      }
    } catch { /* the block just stays empty */ }
  }, [orgId, canManageOrg]);

  useEffect(() => {
    if (!orgId) { setLoading(false); return; }
    setLoading(true);
    Promise.all([loadPeople(), loadUsage(), loadDevices()]).finally(() => setLoading(false));
  }, [orgId, loadPeople, loadUsage, loadDevices]);

  const venueName = useCallback((id: string) => venues.find(v => v.id === id)?.name ?? null, [venues]);
  const roleOf = useCallback((id: string | null) => (id ? roles.find(r => r.id === id) ?? null : null), [roles]);

  // --- Invite (office users) ---

  const openInvite = () => {
    setShowInvite(true);
    setAddError('');
    setAddSuccess('');
    setAddEmail('');
    setAddVenueIds([]);
  };

  const handleInvite = async () => {
    if (!addEmail.trim() || !orgId) return;
    setAddError('');
    setAddSuccess('');
    try {
      // The least-privileged role unless one is chosen, never whichever comes first.
      const roleId = addRole || roles.find(r => r.name === 'team_member')?.id || (roles.length > 0 ? roles[0].id : '');
      const res = await apiFetch('/api/auth/invite', {
        method: 'POST',
        body: JSON.stringify({ email: addEmail, org_id: orgId, role_id: roleId, venue_ids: addVenueIds }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setAddError(typeof data.detail === 'string' ? data.detail : 'The invite wasn’t sent.');
        return;
      }
      setAddSuccess(`Invite sent to ${addEmail}`);
      setAddEmail('');
      setAddVenueIds([]);
      loadPeople();
    } catch { setAddError('The invite wasn’t sent — check your connection and try again.'); }
  };

  const handleResendInvite = async (email: string) => {
    if (!orgId) return;
    setResendStatus(prev => ({ ...prev, [email]: 'sending' }));
    try {
      // A resend changes nothing about the person: no role, no venues.
      const res = await apiFetch('/api/auth/invite', { method: 'POST', body: JSON.stringify({ email, org_id: orgId }) });
      setResendStatus(prev => ({ ...prev, [email]: res.ok ? 'sent' : 'failed' }));
      setTimeout(() => setResendStatus(prev => { const n = { ...prev }; delete n[email]; return n; }), 3000);
    } catch {
      setResendStatus(prev => ({ ...prev, [email]: 'failed' }));
    }
  };

  // --- Staff added by hand ---

  const openStaffForm = (p?: Person) => {
    setStaffError('');
    setStaffForm(p && p.staff_id
      ? { id: p.staff_id, name: p.name || '', email: p.email || '', phone: p.phone || '', venue_ids: [...p.venue_ids], role_id: p.role_id || '' }
      : { ...EMPTY_STAFF });
  };

  const handleSaveStaff = async () => {
    if (!staffForm || !orgId) return;
    if (!staffForm.name.trim()) { setStaffError('A name is needed.'); return; }
    setSavingStaff(true);
    setStaffError('');
    const body = {
      name: staffForm.name.trim(),
      email: staffForm.email.trim() || null,
      phone: staffForm.phone.trim() || null,
      venue_ids: staffForm.venue_ids,
      role_id: staffForm.role_id || null,
    };
    try {
      const res = staffForm.id
        ? await apiFetch(`/api/organizations/${orgId}/staff/${staffForm.id}`, { method: 'PUT', body: JSON.stringify(body) })
        : await apiFetch(`/api/organizations/${orgId}/staff`, { method: 'POST', body: JSON.stringify(body) });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setStaffError(typeof data.detail === 'string' ? data.detail : `They weren’t saved (error ${res.status}).`);
        return;
      }
      setStaffForm(null);
      await loadPeople();
    } catch {
      setStaffError('They weren’t saved — check your connection and try again.');
    } finally {
      setSavingStaff(false);
    }
  };

  // --- Import ---

  const openImport = () => {
    setShowImport(true);
    setCsvText('');
    setImportError('');
    setImportResult(null);
  };

  const readCsvFile = (file: File | undefined) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => setCsvText(String(reader.result || ''));
    reader.readAsText(file);
  };

  const handleImport = async () => {
    if (!orgId || !csvText.trim()) return;
    setImporting(true);
    setImportError('');
    setImportResult(null);
    try {
      const res = await apiFetch(`/api/organizations/${orgId}/staff/import`, { method: 'POST', body: JSON.stringify({ csv: csvText }) });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setImportError(typeof data.detail === 'string' ? data.detail : `The import didn’t run (error ${res.status}).`);
        return;
      }
      setImportResult((await res.json()) as ImportResult);
      await loadPeople();
    } catch {
      setImportError('The import didn’t run — check your connection and try again.');
    } finally {
      setImporting(false);
    }
  };

  // --- Staff from Loaded ---

  const handleToggleStaffAccess = async () => {
    if (!orgId || !sync) return;
    setSyncBusy('toggle');
    setSyncError(null);
    try {
      const res = await apiFetch(`/api/organizations/${orgId}/staff-access`, { method: 'PUT', body: JSON.stringify({ enabled: !sync.enabled }) });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setSyncError(typeof data.detail === 'string' ? data.detail : `The switch didn’t change (error ${res.status}).`);
        return;
      }
      const d = await res.json();
      setSync((d.sync as SyncStatus) ?? null);
      // Switching it on makes the venue roles, which the pickers should offer.
      await loadPeople();
    } catch {
      setSyncError('The switch didn’t change — check your connection and try again.');
    } finally {
      setSyncBusy(null);
    }
  };

  const handleSyncNow = async () => {
    if (!orgId) return;
    setSyncBusy('sync');
    setSyncError(null);
    try {
      const res = await apiFetch(`/api/organizations/${orgId}/staff/sync`, { method: 'POST' });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setSyncError(typeof data.detail === 'string' ? data.detail : `The sync didn’t run (error ${res.status}).`);
        return;
      }
      const d = await res.json();
      setSync((d.sync as SyncStatus) ?? null);
      await loadPeople();
    } catch {
      setSyncError('The sync didn’t run — check your connection and try again.');
    } finally {
      setSyncBusy(null);
    }
  };

  // --- Per-person changes ---

  const patchPerson = (key: string, patch: Partial<Person>) =>
    setPeople(prev => prev.map(p => (personKey(p) === key ? { ...p, ...patch } : p)));

  // The picker shows the new role at once; a refusal puts the old one back.
  // An account's role lives on its membership; a staff row's on the row.
  const handleRoleChange = async (p: Person, roleId: string) => {
    if (!orgId) return;
    const key = personKey(p);
    setChangeError(null);
    const before = { role_id: p.role_id, role_name: p.role_name };
    const role = roleOf(roleId);
    patchPerson(key, { role_id: roleId || null, role_name: role?.display_name ?? null });
    try {
      if (p.kind !== 'staff' && p.user_id) {
        await mustSucceed(
          () => apiFetch(`/api/organizations/${orgId}/members/${p.user_id}/role`, { method: 'PUT', body: JSON.stringify({ role_id: roleId }) }),
          'The role wasn’t changed',
        );
      } else if (p.staff_id) {
        await mustSucceed(
          () => apiFetch(`/api/organizations/${orgId}/staff/${p.staff_id}`, { method: 'PUT', body: JSON.stringify({ role_id: roleId || null }) }),
          'The role wasn’t changed',
        );
      }
    } catch (e) {
      patchPerson(key, before);
      setChangeError({ key, field: 'role', message: (e as Error).message });
    }
  };

  // Ticks at once; if the API refuses, the box flips back and says why.
  const handleToggleVenue = async (p: Person, venueId: string, checked: boolean) => {
    if (!orgId) return;
    const key = personKey(p);
    setChangeError(null);
    const before = p.venue_ids;
    const updated = checked ? [...before, venueId] : before.filter(id => id !== venueId);
    patchPerson(key, { venue_ids: updated });
    try {
      if (p.kind !== 'staff' && p.user_id) {
        await mustSucceed(
          () => apiFetch(`/api/users/${p.user_id}/venues`, { method: 'PUT', body: JSON.stringify({ venue_ids: updated, organization_id: orgId }) }),
          'Venue access wasn’t changed',
        );
      } else if (p.staff_id) {
        await mustSucceed(
          () => apiFetch(`/api/organizations/${orgId}/staff/${p.staff_id}`, { method: 'PUT', body: JSON.stringify({ venue_ids: updated }) }),
          'Venue access wasn’t changed',
        );
      }
    } catch (e) {
      patchPerson(key, { venue_ids: before });
      setChangeError({ key, field: 'venues', message: (e as Error).message });
    }
  };

  const handleSetPin = async (p: Person) => {
    if (!orgId || !p.staff_id) return;
    const key = personKey(p);
    setPinBusy(key);
    setChangeError(null);
    try {
      const res = await apiFetch(`/api/organizations/${orgId}/staff/${p.staff_id}/pin`, { method: 'POST' });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setChangeError({ key, field: 'pin', message: typeof data.detail === 'string' ? data.detail : `The PIN wasn’t set (error ${res.status}).` });
        return;
      }
      const d = await res.json();
      setPinShown({ name: p.name || p.email || 'this person', pin: String(d.pin) });
      patchPerson(key, { has_pin: true });
    } catch {
      setChangeError({ key, field: 'pin', message: 'The PIN wasn’t set — check your connection and try again.' });
    } finally {
      setPinBusy(null);
    }
  };

  const handleClearPin = async (p: Person) => {
    if (!orgId || !p.staff_id) return;
    const key = personKey(p);
    setPinBusy(key);
    setChangeError(null);
    try {
      await mustSucceed(
        () => apiFetch(`/api/organizations/${orgId}/staff/${p.staff_id}/pin`, { method: 'DELETE' }),
        'The PIN wasn’t removed',
      );
      patchPerson(key, { has_pin: false });
    } catch (e) {
      setChangeError({ key, field: 'pin', message: (e as Error).message });
    } finally {
      setPinBusy(null);
    }
  };

  // Throws on failure, so the confirmation stays open and says why. A person
  // with a staff record goes through the staff route, which also ends their
  // membership; an office user through the members route.
  const handleRemove = async (p: Person) => {
    if (!orgId) throw new Error('No organization found for your account.');
    if (p.staff_id) {
      await mustSucceed(() => apiFetch(`/api/organizations/${orgId}/staff/${p.staff_id}`, { method: 'DELETE' }), 'They weren’t removed');
    } else if (p.user_id) {
      await mustSucceed(() => apiFetch(`/api/organizations/${orgId}/members/${p.user_id}`, { method: 'DELETE' }), 'They weren’t removed');
    }
    setExpanded(null);
    await loadPeople();
  };

  // --- Tablets ---

  const handleDevicePin = async (d: Device) => {
    if (!orgId) return;
    setDeviceBusy(d.id);
    setDeviceError(null);
    const next = !d.pin_required;
    setDevices(prev => prev.map(x => (x.id === d.id ? { ...x, pin_required: next } : x)));
    try {
      await mustSucceed(
        () => apiFetch(`/api/organizations/${orgId}/devices/${d.id}`, { method: 'PUT', body: JSON.stringify({ pin_required: next }) }),
        'The tablet wasn’t changed',
      );
    } catch (e) {
      setDevices(prev => prev.map(x => (x.id === d.id ? { ...x, pin_required: !next } : x)));
      setDeviceError((e as Error).message);
    } finally {
      setDeviceBusy(null);
    }
  };

  const handleRevoke = async (d: Device) => {
    if (!orgId) throw new Error('No organization found for your account.');
    await mustSucceed(() => apiFetch(`/api/organizations/${orgId}/devices/${d.id}`, { method: 'DELETE' }), 'The tablet wasn’t signed out');
    await loadDevices();
  };

  const toggleExpanded = async (p: Person) => {
    const key = personKey(p);
    const next = expanded === key ? null : key;
    setExpanded(next);
    if (next && orgId && p.user_id && !personDailyUsage[p.user_id]) {
      const res = await apiFetch(`/api/organizations/${orgId}/usage/daily?user_id=${p.user_id}`).catch(() => null);
      if (res?.ok) {
        const d = await res.json();
        setPersonDailyUsage(prev => ({ ...prev, [p.user_id as string]: d.days || {} }));
      }
    }
  };

  const shown = useMemo(() => people.filter(p => matchesFilter(p, filter)), [people, filter]);
  const counts = useMemo(() => Object.fromEntries(FILTERS.map(f => [f.id, people.filter(p => matchesFilter(p, f.id)).length])) as Record<Filter, number>, [people]);

  if (!orgId) return <PageState kind="empty" title="No organisation" detail="Your account isn’t in an organisation yet, so there is nobody to list." />;
  if (loading) return <PageState kind="loading" title="Loading people…" />;
  if (loadError) return <PageState kind="error" title={loadError} action={<Button size="sm" onClick={() => { setLoading(true); loadPeople().finally(() => setLoading(false)); }}>Retry</Button>} />;

  const pendingCount = people.filter(p => p.invited).length;
  const peopleMeta = people.length > 0
    ? `${people.length} ${people.length === 1 ? 'person' : 'people'}${pendingCount > 0 ? ` · ${pendingCount} invite${pendingCount === 1 ? '' : 's'} pending` : ''}`
    : undefined;

  /** Why a change just failed, in small error text under the control. */
  const changeFailure = (p: Person, field: 'role' | 'venues' | 'pin') =>
    changeError?.key === personKey(p) && changeError.field === field ? (
      <div role="alert" style={{ marginTop: 6, fontSize: 'var(--fs-sm)', color: 'var(--error)', maxWidth: 320 }}>
        {changeError.message}
      </div>
    ) : null;

  /** "$1.23 · 45.6K" — the month's cost, then the billable tokens plan limits count. */
  const personUsage = (p: Person): UsageFigures | null => (p.user_id ? usage[p.user_id] ?? null : null);
  const personTokens = (p: Person): string | null => {
    const u = personUsage(p);
    return u ? `${fmtUsd(u.cost_usd)} · ${fmtTokens(u.billable_tokens || 0)}` : null;
  };

  const canEditVenues = (p: Person) => canManage && p.is_active && p.source !== LOADED;
  const canHavePin = (p: Person) => canManage && p.is_active && p.staff_id !== null;

  // Role picker and the "what does this role allow" button. Clicks here don't open the row.
  const roleControl = (p: Person) => {
    const role = roleOf(p.role_id);
    const label = role?.display_name || p.role_name || 'Unassigned';
    return (
      <div onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          {!canManage || !p.is_active ? (
            <span style={{ color: role || p.role_name ? 'var(--text)' : 'var(--muted)', whiteSpace: 'nowrap' }}>{label}</span>
          ) : roles.length > 0 ? (
            <select
              className="n-select"
              aria-label={`Role for ${p.name || p.email}`}
              value={p.role_id || ''}
              onChange={e => handleRoleChange(p, e.target.value)}
              style={{ fontSize: 'var(--fs-sm)' }}
              data-testid="person-role"
            >
              {!p.role_id && <option value="">{p.kind === 'staff' ? '— From their Loaded role —' : '— Unassigned —'}</option>}
              {roles.map(r => (
                <option key={r.id} value={r.id}>{r.display_name}</option>
              ))}
            </select>
          ) : (
            <Badge>{label}</Badge>
          )}
          {p.role_id && (
            <IconButton
              icon={Info}
              iconSize={16}
              label="What this role allows"
              onClick={() => setViewingRoleId(viewingRoleId === p.role_id ? null : p.role_id)}
            />
          )}
        </div>
        {changeFailure(p, 'role')}
      </div>
    );
  };

  const statusControl = (p: Person) => {
    const s = statusOf(p);
    const status = p.email ? resendStatus[p.email] : undefined;
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <Badge tone={s.tone} title={p.last_seen_at ? `Last seen ${timeAgo(p.last_seen_at)}` : undefined}>{s.label}</Badge>
        {p.invited && p.email && (
          status === 'sending' ? <span style={small}>Sending…</span>
          : status === 'sent' ? <span style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--ok)' }}>Sent!</span>
          : status === 'failed' ? <span style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--error)' }}>Failed</span>
          : canManage ? <Button variant="link" size="sm" onClick={e => { e.stopPropagation(); handleResendInvite(p.email as string); }}>Resend invite</Button>
          : null
        )}
      </div>
    );
  };

  // The venues a person works at, as badges.
  const venueAccess = (p: Person) => {
    const named = p.venue_ids.map(venueName).filter((n): n is string => !!n);
    if (named.length === 0) return <span style={small}>No venues</span>;
    if (venues.length > 1 && named.length === venues.length) return <Badge>All venues</Badge>;
    return (
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {named.map(n => <Badge key={n}>{n}</Badge>)}
      </div>
    );
  };

  const pinControl = (p: Person) => {
    if (!p.staff_id) return <span style={{ color: 'var(--muted)' }}>—</span>;
    const busy = pinBusy === personKey(p);
    return (
      <div onClick={e => e.stopPropagation()} style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
        {p.has_pin ? <Badge tone="info">PIN set</Badge> : <span style={small}>No PIN</span>}
        {canHavePin(p) && (
          <Button size="sm" variant="quiet" disabled={busy} onClick={() => handleSetPin(p)} data-testid="set-pin">
            {busy ? 'Working…' : p.has_pin ? 'Reset' : 'Set'}
          </Button>
        )}
        {changeFailure(p, 'pin')}
      </div>
    );
  };

  const chevron = (p: Person, isExpanded: boolean) => (
    <IconButton
      icon={isExpanded ? ChevronDown : ChevronRight}
      iconSize={16}
      aria-expanded={isExpanded}
      label={`${isExpanded ? 'Hide' : 'Show'} details for ${p.name || p.email}`}
    />
  );

  const nameCell = (p: Person) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
      <Avatar name={p.name || p.email || '?'} size={28} />
      <div style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 600, color: 'var(--text)' }}>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name || p.email}</span>
          {p.source === LOADED && <Badge title="Copied from Loaded">Loaded</Badge>}
        </div>
        {p.name && p.email && <div style={{ ...small, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.email}</div>}
        {p.name && !p.email && <div style={small}>No email yet</div>}
      </div>
    </div>
  );

  const personDetails = (p: Person) => {
    const u = personUsage(p);
    const days = (p.user_id && personDailyUsage[p.user_id]) || {};
    const removable = canManage && (p.staff_id || p.kind === 'member');
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {(p.phone || p.loaded_roles.length > 0 || p.last_seen_at) && (
          <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
            {p.phone && <Stat label="Phone" value={p.phone} />}
            {p.loaded_roles.length > 0 && <Stat label="Loaded roles" value={p.loaded_roles.join(', ')} />}
            {p.last_seen_at && <Stat label="Last seen" value={timeAgo(p.last_seen_at)} />}
          </div>
        )}

        {p.user_id && (
          u ? (
            <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
              <Stat label="Cost" value={fmtUsd(u.cost_usd)} />
              <div title={usageTitle(u)}><Stat label="Billable tokens" value={fmtTokens(u.billable_tokens || 0)} /></div>
              <Stat label="Calls" value={u.llm_call_count || 0} />
            </div>
          ) : (
            <div style={small}>No usage this month</div>
          )
        )}

        {Object.keys(days).length > 0 && (
          <div>
            <div className="n-eyebrow" style={{ marginBottom: 6 }}>Daily usage</div>
            <DailyCostBars days={days} compact />
          </div>
        )}

        {/* Venues — ticked here by someone who may change them, listed otherwise */}
        <div>
          <div className="n-eyebrow" style={{ marginBottom: 6 }}>Venues</div>
          {!canEditVenues(p) ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              {venueAccess(p)}
              {p.source === LOADED && <span style={small}>From Loaded — change their venues there.</span>}
            </div>
          ) : (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 16px' }}>
              {venues.map(v => (
                <label key={v.id} style={checkLabel}>
                  <input type="checkbox" checked={p.venue_ids.includes(v.id)}
                    onChange={e => { e.stopPropagation(); handleToggleVenue(p, v.id, e.target.checked); }}
                    style={{ accentColor: 'var(--accent)' }} />
                  {v.name}
                </label>
              ))}
              {venues.length === 0 && <span style={small}>No venues created yet</span>}
            </div>
          )}
          {changeFailure(p, 'venues')}
        </div>

        {/* Tablet PIN */}
        {p.staff_id && (
          <div>
            <div className="n-eyebrow" style={{ marginBottom: 6 }}>Tablet PIN</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--text)' }}>
                {p.has_pin ? 'They have a PIN for the kitchen tablet.' : 'They have no PIN yet, so they can’t use a kitchen tablet.'}
              </span>
              {canHavePin(p) && (
                <>
                  <Button size="sm" disabled={pinBusy === personKey(p)} onClick={e => { e.stopPropagation(); handleSetPin(p); }}>
                    {p.has_pin ? 'Reset PIN' : 'Set PIN'}
                  </Button>
                  {p.has_pin && (
                    <Button size="sm" variant="quiet" disabled={pinBusy === personKey(p)} onClick={e => { e.stopPropagation(); handleClearPin(p); }} data-testid="remove-pin">
                      Remove PIN
                    </Button>
                  )}
                </>
              )}
            </div>
            {changeFailure(p, 'pin')}
          </div>
        )}

        {(removable || (canManage && p.staff_id && p.source !== LOADED)) && (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', paddingTop: 12, borderTop: '1px solid var(--line)' }}>
            {canManage && p.staff_id && p.source !== LOADED && (
              <Button size="sm" onClick={e => { e.stopPropagation(); openStaffForm(p); }} data-testid="edit-staff">Edit details</Button>
            )}
            {removable && (
              <Button variant="danger" size="sm" onClick={e => { e.stopPropagation(); setRemoving(p); }} data-testid="remove-person">
                Remove from organization
              </Button>
            )}
          </div>
        )}
      </div>
    );
  };

  const lastSummary = sync?.last_summary;
  const summaryText = lastSummary
    ? [`${lastSummary.added} added`, `${lastSummary.updated} updated`, lastSummary.deactivated > 0 ? `${lastSummary.deactivated} left` : null, lastSummary.reactivated > 0 ? `${lastSummary.reactivated} back` : null].filter(Boolean).join(' · ')
    : null;

  const devicesByVenue = devices.reduce<Record<string, Device[]>>((acc, d) => {
    const k = d.venue_name || venueName(d.venue_id) || 'Venue';
    (acc[k] ||= []).push(d);
    return acc;
  }, {});

  const viewingRole = viewingRoleId ? roleOf(viewingRoleId) : null;
  // The role dialog says "Executive Chef" and "Kitchen", never the stored keys.
  const agentLabel = (key: string) => AGENTS.find(a => a.id === key)?.label ?? key;
  const appLabel = (key: string) => appNames[key] ?? COMING_APPS.find(c => c.key === key)?.label ?? key.replace(/^custom:/, '');

  return (
    <div data-testid="people-panel">
      <SectionHeader
        title="People"
        meta={peopleMeta}
        actions={canManage ? (
          <>
            <Button icon={UserPlus} onClick={() => openStaffForm()} data-testid="add-staff">Add staff</Button>
            <Button icon={Upload} onClick={openImport} data-testid="import-staff">Import CSV</Button>
            <Button variant="primary" icon={Plus} onClick={openInvite} data-testid="invite-user">Invite</Button>
          </>
        ) : undefined}
      />

      {/* Staff from Loaded: the switch, the last sync, Sync now */}
      {sync && (
        <div className="n-card" style={{ display: 'flex', alignItems: 'center', gap: '8px 16px', flexWrap: 'wrap', padding: '12px 16px', marginBottom: 16 }} data-testid="staff-access-card">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Switch
              checked={sync.enabled}
              onChange={handleToggleStaffAccess}
              label="Staff from Loaded"
              disabled={!canManageOrg || syncBusy !== null}
              testId="staff-access-switch"
            />
            <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>Staff from Loaded</span>
          </div>
          <span style={{ ...small, flex: '1 1 240px' }}>
            {!sync.enabled
              ? 'Switched off. Turn it on to copy the staff list from Loaded every hour and let venue staff sign in with an email code.'
              : !sync.loaded_connected
                ? 'No venue is connected to Loaded yet, so there is nothing to copy. Add staff by hand or from a spreadsheet.'
                : sync.last_run_at
                  ? `Last synced ${timeAgo(sync.last_run_at)}${summaryText ? ` · ${summaryText}` : ''}`
                  : 'Not synced yet — the first copy runs within the hour, or press Sync now.'}
          </span>
          {sync.enabled && sync.loaded_connected && canManageOrg && (
            <Button size="sm" icon={RefreshCw} onClick={handleSyncNow} disabled={syncBusy !== null} data-testid="sync-now">
              {syncBusy === 'sync' ? 'Syncing…' : 'Sync now'}
            </Button>
          )}
          {(syncError || (lastSummary?.errors?.length ?? 0) > 0) && (
            <div role="alert" style={{ flexBasis: '100%', fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>
              {syncError ?? (lastSummary?.errors ?? []).join(' · ')}
            </div>
          )}
        </div>
      )}

      {removing && (
        <ConfirmDialog
          title={`Remove ${removing.name || removing.email}?`}
          confirmLabel="Remove"
          busyLabel="Removing…"
          danger
          onConfirm={() => handleRemove(removing)}
          onClose={() => setRemoving(null)}
        >
          <p style={{ margin: 0 }}>
            They lose access to {orgName || 'this organization'} straight away. Their conversations are kept.
            {removing.source === LOADED && ' They still come from Loaded, so they stay here switched off — the hourly copy won’t switch them back on until someone does it here.'}
          </p>
        </ConfirmDialog>
      )}

      {revoking && (
        <ConfirmDialog
          title={`Sign out ${revoking.name}?`}
          confirmLabel="Sign out tablet"
          busyLabel="Signing out…"
          danger
          onConfirm={() => handleRevoke(revoking)}
          onClose={() => setRevoking(null)}
        >
          <p style={{ margin: 0 }}>
            The tablet stops working straight away. To use it again, set it up on the tablet itself.
          </p>
        </ConfirmDialog>
      )}

      {/* The PIN, once */}
      {pinShown && (
        <Dialog title={`${pinShown.name}’s PIN`} width={360} onClose={() => setPinShown(null)}>
          <div data-testid="pin-dialog">
            <div style={{ textAlign: 'center', padding: '8px 0 16px', fontSize: 36, fontWeight: 600, letterSpacing: '0.3em', color: 'var(--text)', fontVariantNumeric: 'tabular-nums' }} data-testid="pin-value">
              {pinShown.pin}
            </div>
            <p style={{ margin: 0, fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>
              Write it down, Norm won’t show it again.
            </p>
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 20 }}>
              <Button variant="primary" onClick={() => setPinShown(null)}>Done</Button>
            </div>
          </div>
        </Dialog>
      )}

      {/* Invite an office user */}
      {showInvite && (
        <Dialog title="Invite user" width={420} onClose={() => setShowInvite(false)}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }} data-testid="invite-dialog">
            <div>
              <label className="n-label" htmlFor={`${formId}-email`}>Email</label>
              <input id={`${formId}-email`} className="n-input" value={addEmail} onChange={e => { setAddEmail(e.target.value); setAddError(''); setAddSuccess(''); }}
                placeholder="user@example.com" style={{ width: '100%' }} data-testid="invite-email" />
            </div>
            <div>
              <label className="n-label" htmlFor={`${formId}-role`}>Role</label>
              <select id={`${formId}-role`} className="n-select" value={addRole} onChange={e => setAddRole(e.target.value)} style={{ width: '100%' }} data-testid="invite-role">
                {roles.map(r => (
                  <option key={r.id} value={r.id}>{r.display_name}</option>
                ))}
                {roles.length === 0 && <option value="">No roles</option>}
              </select>
            </div>
            {venues.length > 0 && (
              <div>
                <div className="n-label">Venues</div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 16px' }}>
                  {venues.map(v => (
                    <label key={v.id} style={checkLabel}>
                      <input type="checkbox" checked={addVenueIds.includes(v.id)}
                        onChange={e => setAddVenueIds(prev => e.target.checked ? [...prev, v.id] : prev.filter(id => id !== v.id))}
                        style={{ accentColor: 'var(--accent)' }} />
                      {v.name}
                    </label>
                  ))}
                </div>
              </div>
            )}
            {addError && <div role="alert" style={{ fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>{addError}</div>}
            {addSuccess && <div role="status" style={{ fontSize: 'var(--fs-sm)', color: 'var(--ok)' }}>{addSuccess}</div>}
          </div>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 20 }}>
            <Button onClick={() => setShowInvite(false)}>Cancel</Button>
            <Button variant="primary" onClick={() => { handleInvite(); }} data-testid="send-invite">Send invite</Button>
          </div>
        </Dialog>
      )}

      {/* Add staff by hand, or edit someone added that way */}
      {staffForm && (
        <Dialog title={staffForm.id ? 'Edit details' : 'Add staff'} width={440} onClose={() => setStaffForm(null)}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }} data-testid="staff-dialog">
            {!staffForm.id && (
              <p style={{ margin: 0, ...small }}>
                For someone who isn’t in Loaded. They sign in at /team with their email and a code.
              </p>
            )}
            <div>
              <label className="n-label" htmlFor={`${formId}-staff-name`}>Name</label>
              <input id={`${formId}-staff-name`} className="n-input" value={staffForm.name} onChange={e => setStaffForm({ ...staffForm, name: e.target.value })}
                placeholder="Sam Taylor" style={{ width: '100%' }} data-testid="staff-name" />
            </div>
            <div>
              <label className="n-label" htmlFor={`${formId}-staff-email`}>Email</label>
              <input id={`${formId}-staff-email`} className="n-input" type="email" value={staffForm.email} onChange={e => setStaffForm({ ...staffForm, email: e.target.value })}
                placeholder="sam@example.com" style={{ width: '100%' }} data-testid="staff-email" />
            </div>
            <div>
              <label className="n-label" htmlFor={`${formId}-staff-phone`}>Phone</label>
              <input id={`${formId}-staff-phone`} className="n-input" type="tel" value={staffForm.phone} onChange={e => setStaffForm({ ...staffForm, phone: e.target.value })}
                placeholder="Optional" style={{ width: '100%' }} />
            </div>
            <div>
              <label className="n-label" htmlFor={`${formId}-staff-role`}>Role</label>
              <select id={`${formId}-staff-role`} className="n-select" value={staffForm.role_id} onChange={e => setStaffForm({ ...staffForm, role_id: e.target.value })} style={{ width: '100%' }} data-testid="staff-role">
                <option value="">Team Member (the default)</option>
                {roles.map(r => (
                  <option key={r.id} value={r.id}>{r.display_name}</option>
                ))}
              </select>
            </div>
            {venues.length > 0 && (
              <div>
                <div className="n-label">Venues</div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 16px' }}>
                  {venues.map(v => (
                    <label key={v.id} style={checkLabel}>
                      <input type="checkbox" checked={staffForm.venue_ids.includes(v.id)}
                        onChange={e => setStaffForm({ ...staffForm, venue_ids: e.target.checked ? [...staffForm.venue_ids, v.id] : staffForm.venue_ids.filter(id => id !== v.id) })}
                        style={{ accentColor: 'var(--accent)' }} />
                      {v.name}
                    </label>
                  ))}
                </div>
              </div>
            )}
            {staffError && <div role="alert" style={{ fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>{staffError}</div>}
          </div>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 20 }}>
            <Button onClick={() => setStaffForm(null)} disabled={savingStaff}>Cancel</Button>
            <Button variant="primary" onClick={handleSaveStaff} disabled={savingStaff || !staffForm.name.trim()} data-testid="save-staff">
              {savingStaff ? 'Saving…' : staffForm.id ? 'Save' : 'Add'}
            </Button>
          </div>
        </Dialog>
      )}

      {/* Import a spreadsheet */}
      {showImport && (
        <Dialog title="Import staff" width={520} onClose={() => setShowImport(false)}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }} data-testid="import-dialog">
            <p style={{ margin: 0, ...small }}>
              A CSV with the columns <code>name,email,phone,venues,role</code>. Separate several venues with a semicolon; the role is one of this organisation’s role names. People already listed by email are updated, not added twice.
            </p>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <input ref={fileRef} type="file" accept=".csv,text/csv,text/plain" onChange={e => readCsvFile(e.target.files?.[0])} style={{ display: 'none' }} />
              <Button size="sm" icon={Upload} onClick={() => fileRef.current?.click()}>Choose a file</Button>
              <span style={small}>or paste it below</span>
            </div>
            <textarea
              className="n-input"
              aria-label="CSV"
              value={csvText}
              onChange={e => setCsvText(e.target.value)}
              rows={8}
              placeholder={'name,email,phone,venues,role\nSam Taylor,sam@example.com,021 000 0000,La Zeppa,kitchen'}
              style={{ width: '100%', fontFamily: 'ui-monospace, monospace', fontSize: 'var(--fs-sm)' }}
              data-testid="import-csv"
            />
            {importError && <div role="alert" style={{ fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>{importError}</div>}
            {importResult && (
              <div role="status" style={{ fontSize: 'var(--fs-sm)', color: 'var(--text)' }} data-testid="import-result">
                <div style={{ fontWeight: 600 }}>{importResult.created} added, {importResult.updated} updated{importResult.errors.length > 0 ? `, ${importResult.errors.length} line${importResult.errors.length === 1 ? '' : 's'} skipped` : ''}.</div>
                {importResult.errors.length > 0 && (
                  <ul style={{ margin: '6px 0 0', paddingLeft: 18, color: 'var(--error)' }}>
                    {importResult.errors.map((e, i) => <li key={i}>Line {e.line}: {e.message}</li>)}
                  </ul>
                )}
              </div>
            )}
          </div>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 20 }}>
            <Button onClick={() => setShowImport(false)} disabled={importing}>{importResult ? 'Done' : 'Cancel'}</Button>
            <Button variant="primary" onClick={handleImport} disabled={importing || !csvText.trim()} data-testid="run-import">
              {importing ? 'Importing…' : 'Import'}
            </Button>
          </div>
        </Dialog>
      )}

      {/* What a role allows */}
      {viewingRole && (
        <Dialog title={`${viewingRole.display_name} role`} width={400} onClose={() => setViewingRoleId(null)} closeButton>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12, fontSize: 'var(--fs-sm)' }}>
            <div>
              <div className="n-eyebrow" style={{ marginBottom: 4 }}>AI agents</div>
              <div style={{ color: 'var(--text)' }}>
                {viewingRole.agents === undefined ? '—' : viewingRole.agents === null ? 'Every hired AI agent' : viewingRole.agents.length === 0 ? 'None' : viewingRole.agents.map(agentLabel).join(', ')}
              </div>
            </div>
            <div>
              <div className="n-eyebrow" style={{ marginBottom: 4 }}>Apps</div>
              <div style={{ color: 'var(--text)' }}>
                {viewingRole.apps === undefined ? '—' : viewingRole.apps === null ? 'Every app that is on' : viewingRole.apps.length === 0 ? 'None' : viewingRole.apps.map(appLabel).join(', ')}
              </div>
            </div>
            <div>
              <div className="n-eyebrow" style={{ marginBottom: 6 }}>Permissions</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {(viewingRole.permissions ?? []).map(p => <Badge key={p}>{p}</Badge>)}
                {(viewingRole.permissions ?? []).length === 0 && <span style={small}>None</span>}
              </div>
            </div>
          </div>
        </Dialog>
      )}

      {/* Filter chips */}
      {people.length > 0 && (
        <div role="group" aria-label="Show" style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
          {FILTERS.map(f => {
            const active = filter === f.id;
            return (
              <button
                key={f.id}
                type="button"
                aria-pressed={active}
                onClick={() => setFilter(f.id)}
                className={`n-btn n-btn--sm ${active ? 'n-btn--primary' : 'n-btn--secondary'}`}
                data-testid={`people-filter-${f.id}`}
              >
                {f.label}
                <span style={{ marginLeft: 6, opacity: 0.7, fontVariantNumeric: 'tabular-nums' }}>{counts[f.id]}</span>
              </button>
            );
          })}
        </div>
      )}

      {/* The list */}
      {people.length === 0 ? (
        <PageState kind="empty" title="Nobody here yet" detail="Invite an office user, add staff, or switch on Staff from Loaded." />
      ) : shown.length === 0 ? (
        <PageState kind="empty" title="Nobody matches this filter" />
      ) : isMobile ? (
        // Phones: one card per person — who, their role, then status and venues.
        <div className="n-card" style={{ overflow: 'hidden' }}>
          {shown.map((p, i) => {
            const key = personKey(p);
            const isExpanded = expanded === key;
            const tokens = personTokens(p);
            return (
              <div key={key} style={i > 0 ? { borderTop: '1px solid var(--line)' } : undefined} data-testid="person-row" data-email={p.email ?? undefined}>
                <div onClick={() => toggleExpanded(p)} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '12px 4px 12px 12px', cursor: 'pointer' }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    {nameCell(p)}
                    <div style={{ marginTop: 8 }}>{roleControl(p)}</div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
                      {statusControl(p)}
                      {venueAccess(p)}
                      {p.staff_id && pinControl(p)}
                      {tokens && <span title={personUsage(p) ? usageTitle(personUsage(p) as UsageFigures) : undefined} style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{tokens} billable</span>}
                    </div>
                  </div>
                  {chevron(p, isExpanded)}
                </div>
                {isExpanded && <div style={{ padding: '0 12px 16px' }}>{personDetails(p)}</div>}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="n-card" style={{ overflowX: 'auto' }}>
          <table className="n-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Role</th>
                <th>Venues</th>
                <th>Status</th>
                <th>PIN</th>
                <th className="num" title="Cost and billable tokens this month">Usage</th>
                <th style={{ width: 48 }}><span style={srOnly}>Details</span></th>
              </tr>
            </thead>
            <tbody>
              {shown.map(p => {
                const key = personKey(p);
                const isExpanded = expanded === key;
                const cell = isExpanded ? expandedCell : undefined;
                const tokens = personTokens(p);
                const u = personUsage(p);
                return (
                  <Fragment key={key}>
                    <tr onClick={() => toggleExpanded(p)} style={{ cursor: 'pointer' }} data-testid="person-row" data-email={p.email ?? undefined}>
                      <td style={cell}>{nameCell(p)}</td>
                      <td style={cell}>{roleControl(p)}</td>
                      <td style={cell}>{venueAccess(p)}</td>
                      <td style={cell}>{statusControl(p)}</td>
                      <td style={cell}>{pinControl(p)}</td>
                      <td className="num" title={u ? usageTitle(u) : undefined} style={{ ...cell, color: tokens ? 'var(--text-soft)' : 'var(--muted)', whiteSpace: 'nowrap' }}>{tokens ?? '—'}</td>
                      <td style={{ ...cell, padding: '4px 8px', textAlign: 'right' }}>{chevron(p, isExpanded)}</td>
                    </tr>
                    {isExpanded && (
                      <tr>
                        {/* Indented to line up with the name, past the avatar. */}
                        <td colSpan={7} style={{ padding: '8px 12px 16px 50px', backgroundColor: 'var(--surface-alt)' }}>
                          {personDetails(p)}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Tablets — one per venue device; set up on the tablet itself */}
      {canManageOrg && (
        <div style={{ marginTop: 32 }} data-testid="tablets-block">
          <SectionHeader title="Tablets" meta={devices.length > 0 ? `${devices.length} signed in` : undefined} />
          <div className="n-card">
            {devices.length === 0 ? (
              <PageState kind="empty" title="No tablets yet" detail="To set one up, open Norm at /tablet on the tablet and sign in as a manager." />
            ) : (
              Object.entries(devicesByVenue).map(([venue, list], gi) => (
                <div key={venue}>
                  <div className="n-eyebrow" style={{ padding: '10px 16px 4px', borderTop: gi > 0 ? '1px solid var(--line)' : undefined }}>{venue}</div>
                  {list.map((d, i) => (
                    <div key={d.id} style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '8px 12px', padding: '8px 16px 12px', borderBottom: i < list.length - 1 ? '1px solid var(--line-soft)' : undefined }} data-testid="device-row">
                      <div style={{ flex: '1 1 200px', minWidth: 0 }}>
                        <div style={{ fontWeight: 600, color: 'var(--text)' }}>{d.name}</div>
                        <div style={small}>
                          {roleOf(d.role_id)?.display_name ?? 'No role'}{d.last_seen_at ? ` · Last used ${timeAgo(d.last_seen_at)}` : ''}
                        </div>
                      </div>
                      <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
                        <Switch checked={d.pin_required} onChange={() => handleDevicePin(d)} label={`${d.name} asks for a PIN`} disabled={deviceBusy === d.id} testId="device-pin" />
                        {d.pin_required ? 'Asks for a PIN' : 'No PIN'}
                      </label>
                      <Button size="sm" variant="danger" onClick={() => setRevoking(d)} data-testid="revoke-device">Sign out</Button>
                    </div>
                  ))}
                </div>
              ))
            )}
            {deviceError && <div role="alert" style={{ padding: '0 16px 12px', fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>{deviceError}</div>}
          </div>
          {devices.length > 0 && (
            <p style={{ ...small, margin: '8px 0 0' }}>To set up another tablet, open Norm at /tablet on it and sign in as a manager.</p>
          )}
        </div>
      )}

      {/* Usage this month — the whole organisation */}
      {usageTotals.calls > 0 && (
        <div className="n-card" style={{ marginTop: 32 }} data-testid="people-usage">
          <div style={{ display: 'flex', alignItems: 'flex-end', gap: '12px 32px', flexWrap: 'wrap', padding: '12px 16px' }}>
            <Stat label="This month" value={fmtUsd(usageTotals.cost)} unit={`· ${fmtTokens(usageTotals.billable)} billable tokens`} large />
            <Stat label="Full-price input" value={fmtTokens(usageTotals.input)} />
            <Stat label="Cache read" value={fmtTokens(usageTotals.cacheRead)} />
            <Stat label="Cache write" value={fmtTokens(usageTotals.cacheWrite)} />
            <Stat label="Output" value={fmtTokens(usageTotals.output)} />
            <Stat label="LLM calls" value={usageTotals.calls} />
            <Button
              variant="quiet"
              size="sm"
              icon={showDailyUsage ? ChevronDown : ChevronRight}
              aria-expanded={showDailyUsage}
              style={{ marginLeft: 'auto' }}
              onClick={async () => {
                const next = !showDailyUsage;
                setShowDailyUsage(next);
                if (next && Object.keys(dailyUsage).length === 0) {
                  const res = await apiFetch(`/api/organizations/${orgId}/usage/daily`).catch(() => null);
                  if (res?.ok) {
                    const d = await res.json();
                    setDailyUsage(d.days || {});
                  }
                }
                if (next && !usageBreakdown) {
                  const res = await apiFetch(`/api/organizations/${orgId}/usage/breakdown`).catch(() => null);
                  if (res?.ok) setUsageBreakdown(await res.json());
                }
              }}
            >
              Daily usage
            </Button>
          </div>
          {showDailyUsage && (
            <div style={{ padding: '12px 16px 16px', borderTop: '1px solid var(--line-soft)' }}>
              <DailyCostBars days={dailyUsage} />
              <UsageBreakdownPanel data={usageBreakdown} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
