'use client';

import { useState, useEffect, useCallback, useMemo, useId, type CSSProperties, type KeyboardEvent, type ReactNode } from 'react';
import { ChevronDown, ChevronRight, Plus, X } from 'lucide-react';
import { apiFetch, getStoredUser, getToken } from '../../lib/api';
import { can } from '../../lib/permissions';
import { useTeam, type TeamApp } from '../../hooks/useTeam';
import type { OrgMember, Role } from '../../types';
import { AGENTS } from '../layout/Sidebar';
import AppIcon from '../ui/AppIcon';
import Avatar from '../ui/Avatar';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import { ConfirmDialog } from '../ui/Dialog';
import Icon from '../ui/Icon';
import PageState from '../ui/PageState';

/**
 * GET /api/permissions: every scope key, and the editor's groups as
 * group name → scope keys (app/auth/permissions.py PERMISSION_GROUPS).
 * Platform-admin scopes are in no group, so the editor never offers them.
 */
interface PermissionCatalogue {
  permissions: string[];
  groups: Record<string, string[]>;
}

/** What each scope means, in words. A scope missing here shows its key. */
const PERMISSION_TEXT: Record<string, { label: string; description: string }> = {
  'tasks:read': { label: 'See conversations', description: 'Read conversations with Norm' },
  'tasks:write': { label: 'Start conversations', description: 'Ask Norm for things and reply' },
  'tasks:approve': { label: 'Approve actions', description: 'Approve what Norm asks to do' },
  'orders:read': { label: 'See orders', description: 'Stock orders and invoices' },
  'orders:write': { label: 'Draft orders', description: 'Create and edit stock orders' },
  'orders:approve': { label: 'Approve orders', description: 'Approve or reject an order' },
  'orders:submit': { label: 'Place orders', description: 'Send an order to the supplier' },
  'roster:read': { label: 'See rosters', description: 'Shifts and rosters' },
  'roster:write': { label: 'Edit rosters', description: 'Add and change shifts' },
  'hr:read': { label: 'See staff records', description: 'Staff, training and hiring' },
  'hr:write': { label: 'Edit staff records', description: 'Change staff and training records' },
  'hr:hire': { label: 'Hire', description: 'Take candidates through hiring' },
  'reports:read': { label: 'See reports', description: 'Sales, labour and stock reports' },
  'reports:create': { label: 'Create reports', description: 'Build and save reports' },
  'billing:read': { label: 'See billing', description: 'The plan, usage and invoices' },
  'billing:manage': { label: 'Manage billing', description: 'Change the plan or card, buy tokens' },
  'org:read': { label: 'See the organization', description: 'Its users, roles and venues' },
  'org:manage': { label: 'Edit the organization', description: 'Its name and details' },
  'org:members': { label: 'Manage users', description: 'Invite and remove people, set their role and venues' },
  'org:roles': { label: 'Manage roles', description: 'Create, edit and delete custom roles' },
  'org:venues': { label: 'Manage venues', description: 'Add, edit and delete venues' },
  'settings:connectors': { label: 'Manage connections', description: 'Connect venues to systems like LoadedHub' },
  'settings:agents': { label: 'AI agent settings', description: 'See how the AI agents are set up' },
  'email:read': { label: 'See email', description: 'Connected mailboxes and the mail Norm sent' },
  'email:manage': { label: 'Manage email', description: 'Connect mailboxes, retry failed mail' },
  'apps:build': { label: 'Build apps', description: 'Make apps for their own use' },
  'apps:share': { label: 'Share apps', description: 'Share an app they built with others' },
};

const permissionText = (key: string) => PERMISSION_TEXT[key] ?? { label: key, description: '' };

/** The API's reason a call failed ("Role is currently assigned to members"). */
async function failureReason(res: Response): Promise<string> {
  const body = await res.json().catch(() => null);
  return typeof body?.detail === 'string' ? body.detail : `error ${res.status}`;
}

/** One row of GET /api/apps, as much of it as the role editor reads. */
interface AppRow {
  slug: string;
  name: string;
  icon?: string | null;
  builtin?: boolean;
  /** agent | team | both — where the app shows. Older APIs leave it out. */
  audience?: string;
}

/** An app the role may name: a built-in slug, or `custom:<slug>` for an org-built app. */
interface AppOption {
  key: string;
  label: string;
  icon: { slug?: string | null; icon?: string | null };
  /** Named by the shipped venue roles but not built yet (the Orbit port). */
  coming?: boolean;
  /** An AI agent's job app: what ticking it lets Norm use. */
  hint?: string;
}

/** One AI agent's job apps (GET /api/team → apps[]), offered under its
 *  label. A role may name any of these slugs: ticking one gives Norm that
 *  app's tools in a chat (role_access.allows_app) without showing the agent. */
interface AppGroup { label: string; options: AppOption[] }

/** "Norm can use Get recipes, Draft recipe and Cost a menu." — up to three
 *  tool names, so the owner knows what a tick hands Norm. A label that is
 *  really a description (some catalog tools carry one) gives way to the key. */
export function toolHint(tools: { key: string; label?: string }[]): string {
  if (tools.length === 0) return 'No tools for Norm to use in a chat.';
  const name = (t: { key: string; label?: string }) => {
    if (t.label && t.label.length <= 40) return t.label;
    // "loadedhub.get_sales" → "Get sales": the connector prefix is not a name.
    const words = (t.key.split('.').pop() ?? t.key).replace(/_/g, ' ').trim();
    return words.charAt(0).toUpperCase() + words.slice(1);
  };
  const shown = tools.slice(0, 3).map(name);
  const rest = tools.length - shown.length;
  const list = shown.length === 1 ? shown[0] : `${shown.slice(0, -1).join(', ')} and ${shown[shown.length - 1]}`;
  return rest > 0 ? `Norm can use ${list} and ${rest} more.` : `Norm can use ${list}.`;
}

/** The AI agents' job apps, grouped by agent in the rail's order. Norm Core
 *  (every agent) and App Builder are not for a role; a built-in or custom
 *  app already offered by its own key is not repeated. */
export function catalogAppGroups(apps: TeamApp[], listed: { key: string }[]): AppGroup[] {
  const taken = (a: TeamApp) => listed.some(o =>
    o.key === a.slug || (!!a.app_slug && (o.key === a.app_slug || o.key === `custom:${a.app_slug}`)));
  const groups: AppGroup[] = [];
  for (const agent of AGENTS) {
    if (agent.id === 'home' || agent.id === 'app_builder') continue;
    const rows = apps.filter(a => a.kind === 'app' && a.enabled && a.member === agent.id && !taken(a));
    if (rows.length === 0) continue;
    groups.push({
      label: agent.label,
      options: rows.map(a => ({ key: a.slug, label: a.name, icon: { slug: a.slug, icon: a.icon }, hint: toolHint(a.tools ?? []) })),
    });
  }
  return groups;
}

/** Team apps the venue roles name before they exist in Norm (plan 11.3). */
export const COMING_APPS: { key: string; label: string }[] = [
  { key: 'kitchen', label: 'Kitchen' },
  { key: 'front_of_house', label: 'Front of house' },
  { key: 'managers', label: 'Managers' },
];

/** The role's reach in one line: "All AI agents · 2 apps · 3 Loaded roles". */
function accessSummary(role: Pick<Role, 'agents' | 'apps' | 'loaded_roles'>): string {
  const count = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
  const agents = role.agents === null ? 'All AI agents'
    : role.agents.length === 0 ? 'No AI agents'
    : count(role.agents.length, 'AI agent', 'AI agents');
  const apps = role.apps === null ? 'All apps'
    : role.apps.length === 0 ? 'No apps'
    : count(role.apps.length, 'app', 'apps');
  const mapped = (role.loaded_roles ?? []).length;
  return [agents, apps, mapped > 0 ? count(mapped, 'Loaded role', 'Loaded roles') : null].filter(Boolean).join(' · ');
}

interface RolesPanelProps {
  orgId: string;
}

// A section: 18px title with a muted count beside it, its action on the right.
const sectionHead: CSSProperties = { display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12, marginBottom: 12 };
const sectionTitle: CSSProperties = { margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' };
const metaText: CSSProperties = { fontSize: 'var(--fs-sm)', color: 'var(--muted)' };
// One row of a list card: a hairline between rows, none after the last. It
// wraps on a phone, so the actions drop under the name instead of squeezing it.
const listRow = (last: boolean): CSSProperties => ({
  display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '8px 12px',
  padding: '12px 16px', borderBottom: last ? 'none' : '1px solid var(--line)',
});
const permissionGrid: CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 8 };
/** Why a save or an assignment failed, in small error text by its control. */
const errorText: CSSProperties = { margin: 0, fontSize: 'var(--fs-sm)', color: 'var(--error)' };
/** A block of the form: its title, a line of help, then its controls. */
const blockTitle: CSSProperties = { fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' };
const helpText: CSSProperties = { margin: '2px 0 8px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' };
const radioRow: CSSProperties = { display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-base)', color: 'var(--text)', cursor: 'pointer' };

/** A ticked tile is tinted with a tan edge, so a grid reads at a glance. */
function tileStyle(checked: boolean): CSSProperties {
  return {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
    padding: '10px 12px',
    border: `1px solid ${checked ? 'var(--brand-soft)' : 'var(--line)'}`,
    borderRadius: 'var(--radius)',
    backgroundColor: checked ? 'var(--accent-soft)' : 'var(--bg)',
    cursor: 'pointer',
  };
}
const tickStyle: CSSProperties = { flex: '0 0 auto', width: 16, height: 16, margin: '1px 0 0', accentColor: 'var(--accent)', cursor: 'pointer' };

/** One tile of a ReachBlock: an agent or an app the role may reach. */
interface ReachOption { key: string; label: string; detail?: string; icon?: ReactNode }

/** All / only these, then the tiles; `groups` are more tiles, each set
 *  under its own heading (the AI agents' apps). `value` null = all. */
function ReachBlock({ title, help, allLabel, value, onChange, options, groups = [], testId }: {
  title: string;
  help: string;
  allLabel: string;
  value: string[] | null;
  onChange: (next: string[] | null) => void;
  options: ReachOption[];
  groups?: { label: string; options: ReachOption[] }[];
  testId: string;
}) {
  const groupId = useId();
  const picked = value ?? [];
  // A key the role names that no option shows (an app this viewer can't see):
  // kept, and shown by its key so it can still be unticked.
  const offered = [...options, ...groups.flatMap(g => g.options)];
  const extras = picked.filter(k => !offered.some(o => o.key === k));
  const all: ReachOption[] = [...options, ...extras.map(k => ({ key: k, label: k }))];
  const toggle = (key: string) => {
    onChange(picked.includes(key) ? picked.filter(k => k !== key) : [...picked, key]);
  };
  const tile = (o: ReachOption) => {
    const checked = picked.includes(o.key);
    return (
      <label key={o.key} style={tileStyle(checked)}>
        <input type="checkbox" checked={checked} onChange={() => toggle(o.key)} style={tickStyle} data-testid={`${testId}-${o.key}`} />
        {o.icon}
        <span style={{ minWidth: 0 }}>
          <span style={{ display: 'block', fontSize: 'var(--fs-base)', fontWeight: 500, color: 'var(--text)' }}>{o.label}</span>
          {o.detail && (
            <span style={{ display: 'block', marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{o.detail}</span>
          )}
        </span>
      </label>
    );
  };
  return (
    <div style={{ marginBottom: 20 }} data-testid={testId}>
      <div style={blockTitle}>{title}</div>
      <p style={helpText}>{help}</p>
      <div role="radiogroup" aria-label={title} style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 20px', marginBottom: 10 }}>
        <label style={radioRow}>
          <input type="radio" name={groupId} checked={value === null} onChange={() => onChange(null)} style={{ accentColor: 'var(--accent)' }} />
          {allLabel}
        </label>
        <label style={radioRow}>
          <input type="radio" name={groupId} checked={value !== null} onChange={() => onChange(value ?? [])} style={{ accentColor: 'var(--accent)' }} />
          Only these
        </label>
      </div>
      {value !== null && (
        all.length === 0 && groups.length === 0 ? (
          <div style={metaText}>Nothing to choose from yet.</div>
        ) : (
          <>
            {all.length > 0 && <div style={permissionGrid}>{all.map(tile)}</div>}
            {groups.map(g => (
              <div key={g.label} style={{ marginTop: all.length > 0 ? 14 : 0 }} data-testid={`${testId}-group`}>
                <div style={{ ...metaText, fontWeight: 600, marginBottom: 6 }}>{g.label}</div>
                <div style={permissionGrid}>{g.options.map(tile)}</div>
              </div>
            ))}
          </>
        )
      )}
    </div>
  );
}

/**
 * Loaded role names as chips. Type a name and press Enter (or Add); the
 * names seen in the staff list that aren't mapped yet are offered as
 * suggestions. Matching is case-insensitive, as the API's is.
 */
function LoadedRolesInput({ value, onChange, suggestions }: {
  value: string[];
  onChange: (next: string[]) => void;
  suggestions: string[];
}) {
  const [draft, setDraft] = useState('');
  const inputId = useId();
  const has = (name: string) => value.some(v => v.toLowerCase() === name.toLowerCase());
  const add = (name: string) => {
    const clean = name.trim().replace(/\s+/g, ' ');
    if (!clean || has(clean)) { setDraft(''); return; }
    onChange([...value, clean]);
    setDraft('');
  };
  const remove = (name: string) => onChange(value.filter(v => v !== name));
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add(draft); }
    else if (e.key === 'Backspace' && !draft && value.length > 0) remove(value[value.length - 1]);
  };
  const unmapped = suggestions.filter(s => !has(s));
  return (
    <div style={{ marginBottom: 20 }} data-testid="role-form-loaded-roles">
      <div style={blockTitle}>Loaded roles</div>
      <p style={helpText}>People with one of these roles in Loaded land in this role when they first sign in. A manager can still move someone by hand.</p>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, padding: 6, border: '1px solid var(--field)', borderRadius: 'var(--radius)', backgroundColor: 'var(--bg)', maxWidth: 560 }}>
        {value.map(name => (
          <span key={name} className="n-badge" style={{ display: 'inline-flex', alignItems: 'center', gap: 4, paddingRight: 4 }} data-testid="loaded-role-chip">
            {name}
            <button
              type="button"
              aria-label={`Remove ${name}`}
              onClick={() => remove(name)}
              style={{ display: 'inline-flex', padding: 2, border: 'none', background: 'transparent', color: 'inherit', cursor: 'pointer', borderRadius: 999 }}
            >
              <Icon icon={X} size="meta" />
            </button>
          </span>
        ))}
        <input
          id={inputId}
          list={`${inputId}-list`}
          aria-label="Add a Loaded role"
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={onKey}
          onBlur={() => { if (draft.trim()) add(draft); }}
          placeholder={value.length === 0 ? 'Type a Loaded role name and press Enter' : 'Add another…'}
          style={{ flex: '1 1 160px', minWidth: 120, border: 'none', outline: 'none', background: 'transparent', color: 'var(--text)', fontFamily: 'inherit', fontSize: 'var(--fs-base)', padding: '4px 4px' }}
        />
        <datalist id={`${inputId}-list`}>
          {unmapped.map(s => <option key={s} value={s} />)}
        </datalist>
      </div>
      {unmapped.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, marginTop: 8 }}>
          <span style={metaText}>Seen in Loaded:</span>
          {unmapped.map(s => (
            <button
              key={s}
              type="button"
              className="n-btn n-btn--secondary n-btn--xs"
              onClick={() => add(s)}
              data-testid="loaded-role-suggestion"
            >
              <Icon icon={Plus} size="meta" /> {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function RolesPanel({ orgId }: RolesPanelProps) {
  const [roles, setRoles] = useState<Role[]>([]);
  const [members, setMembers] = useState<OrgMember[]>([]);
  const [permissionGroups, setPermissionGroups] = useState<PermissionCatalogue['groups']>({});
  const [catalogueFailed, setCatalogueFailed] = useState(false);
  const [appRows, setAppRows] = useState<AppRow[]>([]);
  const [loadedRoleNames, setLoadedRoleNames] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingRole, setEditingRole] = useState<Role | null>(null);
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [assigningUser, setAssigningUser] = useState<string | null>(null);
  const [assignError, setAssignError] = useState<{ userId: string; message: string } | null>(null);
  // The role waiting on "Delete …?" — nothing is deleted until confirmed.
  const [deleting, setDeleting] = useState<Role | null>(null);
  // A system role whose access is open for reading.
  const [viewingSystem, setViewingSystem] = useState<string | null>(null);

  // Creating and editing roles needs org:roles; assigning them, org:members.
  // A manager holds only the second, so sees the roles but can't change them.
  const user = getStoredUser();
  const canEditRoles = can(user, 'org:roles');
  const canAssign = can(user, 'org:members');

  // Which AI agents the organisation has hired (null = gating off: all of them).
  const team = useTeam(getToken());
  const hiredAgents = useMemo(
    () => AGENTS.filter(a => a.id !== 'home' && (!team.hired || team.hired.has(a.id))),
    [team.hired],
  );
  const agentOptions = useMemo(
    () => hiredAgents.map(a => ({ key: a.id, label: a.label, icon: <Icon icon={a.icon} size="menu" tone="muted" style={{ marginTop: 1 }} /> })),
    [hiredAgents],
  );
  const appOptions = useMemo<AppOption[]>(() => {
    const out: AppOption[] = appRows.map(a => ({
      key: a.builtin ? a.slug : `custom:${a.slug}`,
      label: a.name,
      icon: { slug: a.slug, icon: a.icon },
    }));
    for (const c of COMING_APPS) {
      if (!out.some(o => o.key === c.key)) out.push({ key: c.key, label: c.label, icon: { slug: c.key }, coming: true });
    }
    return out;
  }, [appRows]);
  // The AI agents' job apps, under their agent, after the team apps.
  const appGroups = useMemo(() => catalogAppGroups(team.apps, appOptions), [team.apps, appOptions]);

  // Form state
  const [formName, setFormName] = useState('');
  const [formDisplayName, setFormDisplayName] = useState('');
  const [formDescription, setFormDescription] = useState('');
  const [formPermissions, setFormPermissions] = useState<string[]>([]);
  const [formAgents, setFormAgents] = useState<string[] | null>(null);
  const [formApps, setFormApps] = useState<string[] | null>(null);
  const [formLoadedRoles, setFormLoadedRoles] = useState<string[]>([]);
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});

  const fetchRoles = useCallback(async () => {
    try {
      const res = await apiFetch(`/api/organizations/${orgId}/roles`);
      if (res.ok) {
        const data = await res.json();
        setRoles(((data.roles || []) as Partial<Role>[]).map(r => ({
          ...r,
          agents: r.agents ?? null,
          apps: r.apps ?? null,
          loaded_roles: r.loaded_roles ?? [],
        }) as Role));
      }
    } catch { /* ignore */ }
  }, [orgId]);

  const fetchMembers = useCallback(async () => {
    try {
      const res = await apiFetch(`/api/organizations/${orgId}`);
      if (res.ok) {
        const data = await res.json();
        setMembers(data.members || []);
      }
    } catch { /* ignore */ }
  }, [orgId]);

  const fetchPermissions = useCallback(async () => {
    try {
      const res = await apiFetch('/api/permissions');
      if (res.ok) {
        const data: PermissionCatalogue = await res.json();
        setPermissionGroups(data.groups || {});
        setCatalogueFailed(false);
      } else {
        setCatalogueFailed(true);
      }
    } catch { setCatalogueFailed(true); }
  }, []);

  // The apps a role may name, and the Loaded role names seen in the staff
  // list (suggestions). Either failing just leaves its list empty.
  const fetchAppsAndNames = useCallback(async () => {
    try {
      const res = await apiFetch('/api/apps');
      if (res.ok) {
        const data = await res.json();
        setAppRows((data.apps || []) as AppRow[]);
      }
    } catch { /* ignore */ }
    try {
      const res = await apiFetch(`/api/organizations/${orgId}/people`);
      if (res.ok) {
        const data = await res.json();
        setLoadedRoleNames((data.loaded_role_names || []) as string[]);
      }
    } catch { /* ignore */ }
  }, [orgId]);

  useEffect(() => {
    Promise.all([fetchRoles(), fetchMembers(), fetchPermissions(), fetchAppsAndNames()])
      .finally(() => setLoading(false));
  }, [fetchRoles, fetchMembers, fetchPermissions, fetchAppsAndNames]);

  const openCreateForm = () => {
    setEditingRole(null);
    setCreating(true);
    setFormName('');
    setFormDisplayName('');
    setFormDescription('');
    setFormPermissions([]);
    setFormAgents(null);
    setFormApps(null);
    setFormLoadedRoles([]);
    setExpandedGroups({});
    setSaveError(null);
  };

  const openEditForm = (role: Role) => {
    setCreating(false);
    setEditingRole(role);
    setFormName(role.name);
    setFormDisplayName(role.display_name);
    setFormDescription(role.description || '');
    setFormPermissions([...role.permissions]);
    setFormAgents(role.agents === null ? null : [...role.agents]);
    setFormApps(role.apps === null ? null : [...role.apps]);
    setFormLoadedRoles([...(role.loaded_roles ?? [])]);
    setExpandedGroups({});
    setSaveError(null);
  };

  const closeForm = () => {
    setEditingRole(null);
    setCreating(false);
  };

  // The form stays open when the save fails, with the reason under it
  // ("'Chef' already maps to Kitchen").
  const handleSave = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const body = JSON.stringify({
        name: formName,
        display_name: formDisplayName,
        description: formDescription || null,
        permissions: formPermissions,
        agents: formAgents,
        apps: formApps,
        loaded_roles: formLoadedRoles,
      });

      const res = editingRole
        ? await apiFetch(`/api/organizations/${orgId}/roles/${editingRole.id}`, { method: 'PUT', body })
        : await apiFetch(`/api/organizations/${orgId}/roles`, { method: 'POST', body });
      if (res.ok) {
        closeForm();
        await fetchRoles();
      } else {
        setSaveError(`The role wasn’t saved: ${await failureReason(res)}`);
      }
    } catch {
      setSaveError('The role wasn’t saved — check your connection and try again.');
    }
    setSaving(false);
  };

  // Throws on failure, so the confirmation stays open and says why.
  const handleDelete = async (roleId: string) => {
    let res: Response;
    try {
      res = await apiFetch(`/api/organizations/${orgId}/roles/${roleId}`, { method: 'DELETE' });
    } catch {
      throw new Error('The role wasn’t deleted — check your connection and try again.');
    }
    if (!res.ok) throw new Error(`The role wasn’t deleted: ${await failureReason(res)}`);
    await fetchRoles();
  };

  const handleAssignRole = async (userId: string, roleId: string) => {
    setAssigningUser(userId);
    setAssignError(null);
    try {
      const res = await apiFetch(`/api/organizations/${orgId}/members/${userId}/role`, {
        method: 'PUT',
        body: JSON.stringify({ role_id: roleId }),
      });
      if (res.ok) await fetchMembers();
      else setAssignError({ userId, message: `The role wasn’t changed: ${await failureReason(res)}` });
    } catch {
      setAssignError({ userId, message: 'The role wasn’t changed — check your connection and try again.' });
    }
    setAssigningUser(null);
  };

  const togglePermission = (perm: string) => {
    setFormPermissions(prev =>
      prev.includes(perm) ? prev.filter(p => p !== perm) : [...prev, perm]
    );
  };

  const toggleGroup = (groupKey: string) => {
    setExpandedGroups(prev => ({ ...prev, [groupKey]: !prev[groupKey] }));
  };

  if (loading) {
    return <PageState kind="loading" title="Loading roles…" />;
  }

  const isFormOpen = creating || editingRole !== null;
  // A member's role by id: `role` is the legacy name, which assigning a role
  // doesn't change — matched on that, the picker snapped back after a save.
  const roleOf = (m: OrgMember) => roles.find(r => r.id === m.role_id) ?? roles.find(r => r.name === m.role);
  const holders = deleting ? members.filter(m => roleOf(m)?.id === deleting.id).length : 0;

  const agentLabel = (key: string) => AGENTS.find(a => a.id === key)?.label ?? key;
  const appLabel = (key: string) =>
    [...appOptions, ...appGroups.flatMap(g => g.options)].find(o => o.key === key)?.label ?? key.replace(/^custom:/, '');

  // A system role's reach, in words, under its card when opened.
  const renderReadOnlyAccess = (role: Role) => {
    const line = (label: string, value: string) => (
      <div style={{ display: 'flex', gap: 8, fontSize: 'var(--fs-sm)' }}>
        <span style={{ flex: '0 0 96px', color: 'var(--muted)' }}>{label}</span>
        <span style={{ color: 'var(--text)' }}>{value}</span>
      </div>
    );
    return (
      <div style={{ flexBasis: '100%', display: 'flex', flexDirection: 'column', gap: 4, paddingTop: 4 }} data-testid="role-access-readonly">
        {line('AI agents', role.agents === null ? 'Every hired AI agent' : role.agents.length === 0 ? 'None' : role.agents.map(agentLabel).join(', '))}
        {line('Apps', role.apps === null ? 'Every app that is on' : role.apps.length === 0 ? 'None' : role.apps.map(appLabel).join(', '))}
        {line('Loaded roles', (role.loaded_roles ?? []).length === 0 ? 'None' : role.loaded_roles.join(', '))}
        {line('Permissions', role.permissions.length === 0 ? 'None' : role.permissions.map(p => permissionText(p).label).join(', '))}
      </div>
    );
  };

  // One permission as a tile: ticked tiles are tinted with a tan edge, so the
  // grid reads at a glance as well as by its checkboxes.
  const renderPermission = (key: string) => {
    const checked = formPermissions.includes(key);
    const { label, description } = permissionText(key);
    return (
      <label key={key} style={tileStyle(checked)}>
        <input
          type="checkbox"
          checked={checked}
          onChange={() => togglePermission(key)}
          style={tickStyle}
        />
        <span style={{ minWidth: 0 }}>
          <span style={{ display: 'block', fontSize: 'var(--fs-base)', fontWeight: 500, color: 'var(--text)' }}>{label}</span>
          {description && (
            <span style={{ display: 'block', marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{description}</span>
          )}
        </span>
      </label>
    );
  };

  return (
    <div>
      {deleting && (
        <ConfirmDialog
          title={`Delete the ${deleting.display_name} role?`}
          confirmLabel="Delete role"
          busyLabel="Deleting…"
          danger
          onConfirm={() => handleDelete(deleting.id)}
          onClose={() => setDeleting(null)}
        >
          <p style={{ margin: 0 }}>
            {holders > 0
              ? `${holders} ${holders === 1 ? 'person holds' : 'people hold'} this role, and a role in use can’t be deleted — give them another role first.`
              : 'Nobody holds this role. It’s removed for good and can’t be brought back.'}
          </p>
        </ConfirmDialog>
      )}

      {/* ---- Section 1: Roles List ---- */}
      <div style={sectionHead}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, minWidth: 0 }}>
          <h3 style={sectionTitle}>Organization roles</h3>
          {roles.length > 0 && <span style={metaText}>{roles.length} {roles.length === 1 ? 'role' : 'roles'}</span>}
        </div>
        {!isFormOpen && canEditRoles && (
          <Button variant="primary" icon={Plus} onClick={openCreateForm} data-testid="create-role">
            Create custom role
          </Button>
        )}
      </div>

      {/* Role list */}
      {!isFormOpen && (
        <div className="n-card" style={{ marginBottom: 32 }}>
          {roles.map((role, i) => {
            const open = viewingSystem === role.id;
            return (
              <div key={role.id} style={listRow(i === roles.length - 1)} data-testid={`role-card-${role.name}`}>
                <div style={{ flex: '1 1 240px', minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
                    <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>{role.display_name}</span>
                    <Badge>{role.is_system ? 'System' : 'Custom'}</Badge>
                  </div>
                  {role.description && (
                    <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{role.description}</div>
                  )}
                  <div style={{ marginTop: 2, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }} data-testid="role-access-summary">
                    {accessSummary(role)} · {role.permissions.length} permission{role.permissions.length !== 1 ? 's' : ''}
                  </div>
                </div>

                {!role.is_system && canEditRoles ? (
                  <div style={{ display: 'flex', gap: 8, marginLeft: 'auto' }}>
                    <Button size="sm" onClick={() => openEditForm(role)} data-testid={`edit-role-${role.name}`}>
                      Edit
                    </Button>
                    <Button size="sm" variant="danger" onClick={() => setDeleting(role)}>
                      Delete
                    </Button>
                  </div>
                ) : (
                  <div style={{ marginLeft: 'auto' }}>
                    <Button size="sm" variant="quiet" icon={open ? ChevronDown : ChevronRight} aria-expanded={open} onClick={() => setViewingSystem(open ? null : role.id)}>
                      {open ? 'Hide access' : 'See access'}
                    </Button>
                  </div>
                )}
                {open && renderReadOnlyAccess(role)}
              </div>
            );
          })}
          {roles.length === 0 && <PageState kind="empty" title="No roles defined yet." />}
        </div>
      )}

      {/* ---- Section 2: Create/Edit Form ---- */}
      {isFormOpen && (
        <div className="n-card" style={{ padding: 20, marginBottom: 32 }} data-testid="role-form">
          <h4 style={{ margin: '0 0 16px', fontSize: 'var(--fs-md)', fontWeight: 600, color: 'var(--text)' }}>
            {editingRole ? `Edit role: ${editingRole.display_name}` : 'Create custom role'}
          </h4>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 12, maxWidth: 560, marginBottom: 20 }}>
            <div>
              <label className="n-label" htmlFor="role-form-name">Name (slug)</label>
              <input
                id="role-form-name"
                className="n-input"
                value={formName}
                onChange={e => setFormName(e.target.value)}
                placeholder="e.g. venue-manager"
                style={{ width: '100%' }}
                disabled={!!editingRole}
              />
            </div>
            <div>
              <label className="n-label" htmlFor="role-form-display-name">Display name</label>
              <input
                id="role-form-display-name"
                className="n-input"
                value={formDisplayName}
                onChange={e => setFormDisplayName(e.target.value)}
                placeholder="e.g. Venue Manager"
                style={{ width: '100%' }}
              />
            </div>
            <div>
              <label className="n-label" htmlFor="role-form-description">Description</label>
              <input
                id="role-form-description"
                className="n-input"
                value={formDescription}
                onChange={e => setFormDescription(e.target.value)}
                placeholder="Optional description"
                style={{ width: '100%' }}
              />
            </div>
          </div>

          {/* What the role reaches: agents, apps, and which Loaded roles land here */}
          <ReachBlock
            title="AI agents"
            help="Which of the organisation’s hired AI agents people with this role see. In a chat, Norm may use the tools of these agents."
            allLabel="All hired AI agents"
            value={formAgents}
            onChange={setFormAgents}
            options={agentOptions}
            testId="role-form-agents"
          />
          <ReachBlock
            title="Apps"
            help="Which apps people with this role see in the apps section. Norm may use the tools of these apps too, so ticking one of an AI agent’s apps gives Norm its tools without showing the agent."
            allLabel="All apps that are on"
            value={formApps}
            onChange={setFormApps}
            options={appOptions.map(o => ({
              key: o.key,
              label: o.label,
              detail: o.coming ? 'Coming with the Orbit port' : undefined,
              icon: <AppIcon app={o.icon} size="menu" style={{ marginTop: 1 }} />,
            }))}
            groups={appGroups.map(g => ({
              label: g.label,
              options: g.options.map(o => ({
                key: o.key,
                label: o.label,
                detail: o.hint,
                icon: <AppIcon app={o.icon} size="menu" style={{ marginTop: 1 }} />,
              })),
            }))}
            testId="role-form-apps"
          />
          <LoadedRolesInput value={formLoadedRoles} onChange={setFormLoadedRoles} suggestions={loadedRoleNames} />

          {/* Permission checkboxes grouped */}
          <div style={{ marginBottom: 20 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
              <span style={blockTitle}>Permissions</span>
              <span style={metaText}>{formPermissions.length} selected</span>
            </div>
            {catalogueFailed && (
              <PageState
                kind="error"
                title="Couldn’t load the list of permissions."
                action={<Button size="sm" onClick={fetchPermissions}>Retry</Button>}
              />
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {Object.entries(permissionGroups).map(([groupName, keys]) => {
                const isOpen = !!expandedGroups[groupName];
                // "2 of 3" so a collapsed group still shows what it grants.
                const ticked = keys.filter(k => formPermissions.includes(k)).length;
                return (
                  <div key={groupName} style={{ border: '1px solid var(--line)', borderRadius: 'var(--radius)', overflow: 'hidden', backgroundColor: 'var(--bg)' }}>
                    <button
                      type="button"
                      className="n-row"
                      aria-expanded={isOpen}
                      onClick={() => toggleGroup(groupName)}
                      style={{ borderRadius: 0, padding: '10px 12px', fontWeight: 600 }}
                    >
                      <Icon icon={isOpen ? ChevronDown : ChevronRight} tone="muted" />
                      <span style={{ flex: 1, minWidth: 0 }}>{groupName}</span>
                      <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 500, color: 'var(--muted)', whiteSpace: 'nowrap' }}>
                        {ticked} of {keys.length}
                      </span>
                    </button>
                    {isOpen && (
                      <div style={{ ...permissionGrid, padding: 12, borderTop: '1px solid var(--line-soft)' }}>
                        {keys.map(k => renderPermission(k))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          {saveError && <p role="alert" style={{ ...errorText, marginBottom: 12 }}>{saveError}</p>}

          <div style={{ display: 'flex', gap: 8 }}>
            <Button
              variant="primary"
              onClick={handleSave}
              disabled={saving || !formName || !formDisplayName}
              data-testid="save-role"
            >
              {saving ? 'Saving…' : editingRole ? 'Update role' : 'Create role'}
            </Button>
            <Button onClick={closeForm}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {/* ---- Section 3: Members & Role Assignment ---- */}
      {!isFormOpen && (
        <>
          <div style={sectionHead}>
            <h3 style={sectionTitle}>Member role assignments</h3>
          </div>
          <div className="n-card">
            {members.map((member, i) => (
              <div key={member.id} style={listRow(i === members.length - 1)}>
                <Avatar name={member.full_name} size={28} />
                <div style={{ flex: '1 1 200px', minWidth: 0 }}>
                  <div style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>{member.full_name}</div>
                  <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', overflowWrap: 'anywhere' }}>{member.email}</div>
                </div>
                {canAssign ? (
                  <select
                    className="n-select"
                    aria-label={`Role for ${member.full_name}`}
                    value={roleOf(member)?.id || ''}
                    onChange={e => handleAssignRole(member.user_id, e.target.value)}
                    disabled={assigningUser === member.user_id}
                    style={{ marginLeft: 'auto', minWidth: 180 }}
                  >
                    {roles.map(role => (
                      <option key={role.id} value={role.id}>{role.display_name}</option>
                    ))}
                  </select>
                ) : (
                  <span style={{ marginLeft: 'auto', fontSize: 'var(--fs-base)', color: 'var(--text)' }}>
                    {roleOf(member)?.display_name || member.role || 'Unassigned'}
                  </span>
                )}
                {assignError?.userId === member.user_id && (
                  <p role="alert" style={{ ...errorText, flexBasis: '100%', textAlign: 'right' }}>{assignError.message}</p>
                )}
              </div>
            ))}
            {members.length === 0 && <PageState kind="empty" title="No members found." />}
          </div>
        </>
      )}
    </div>
  );
}
