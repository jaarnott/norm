'use client';

import { useState, useEffect, useCallback, type CSSProperties } from 'react';
import { ChevronDown, ChevronRight, Plus } from 'lucide-react';
import { apiFetch, getStoredUser } from '../../lib/api';
import { can } from '../../lib/permissions';
import type { OrgMember } from '../../types';
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
  'settings:agents': { label: 'AI team settings', description: 'See how the AI team is set up' },
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

interface Role {
  id: string;
  name: string;
  display_name: string;
  description: string | null;
  is_system: boolean;
  permissions: string[];
  member_count?: number;
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

export default function RolesPanel({ orgId }: RolesPanelProps) {
  const [roles, setRoles] = useState<Role[]>([]);
  const [members, setMembers] = useState<OrgMember[]>([]);
  const [permissionGroups, setPermissionGroups] = useState<PermissionCatalogue['groups']>({});
  const [catalogueFailed, setCatalogueFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [editingRole, setEditingRole] = useState<Role | null>(null);
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [assigningUser, setAssigningUser] = useState<string | null>(null);
  const [assignError, setAssignError] = useState<{ userId: string; message: string } | null>(null);
  // The role waiting on "Delete …?" — nothing is deleted until confirmed.
  const [deleting, setDeleting] = useState<Role | null>(null);

  // Creating and editing roles needs org:roles; assigning them, org:members.
  // A manager holds only the second, so sees the roles but can't change them.
  const user = getStoredUser();
  const canEditRoles = can(user, 'org:roles');
  const canAssign = can(user, 'org:members');

  // Form state
  const [formName, setFormName] = useState('');
  const [formDisplayName, setFormDisplayName] = useState('');
  const [formDescription, setFormDescription] = useState('');
  const [formPermissions, setFormPermissions] = useState<string[]>([]);
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});

  const fetchRoles = useCallback(async () => {
    try {
      const res = await apiFetch(`/api/organizations/${orgId}/roles`);
      if (res.ok) {
        const data = await res.json();
        setRoles(data.roles || []);
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

  useEffect(() => {
    Promise.all([fetchRoles(), fetchMembers(), fetchPermissions()])
      .finally(() => setLoading(false));
  }, [fetchRoles, fetchMembers, fetchPermissions]);

  const openCreateForm = () => {
    setEditingRole(null);
    setCreating(true);
    setFormName('');
    setFormDisplayName('');
    setFormDescription('');
    setFormPermissions([]);
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
    setExpandedGroups({});
    setSaveError(null);
  };

  const closeForm = () => {
    setEditingRole(null);
    setCreating(false);
  };

  // The form stays open when the save fails, with the reason under it.
  const handleSave = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const body = JSON.stringify({
        name: formName,
        display_name: formDisplayName,
        description: formDescription || null,
        permissions: formPermissions,
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

  // One permission as a tile: ticked tiles are tinted with a tan edge, so the
  // grid reads at a glance as well as by its checkboxes.
  const renderPermission = (key: string) => {
    const checked = formPermissions.includes(key);
    const { label, description } = permissionText(key);
    return (
      <label
        key={key}
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 10,
          padding: '10px 12px',
          border: `1px solid ${checked ? 'var(--brand-soft)' : 'var(--line)'}`,
          borderRadius: 'var(--radius)',
          backgroundColor: checked ? 'var(--accent-soft)' : 'var(--bg)',
          cursor: 'pointer',
        }}
      >
        <input
          type="checkbox"
          checked={checked}
          onChange={() => togglePermission(key)}
          style={{ flex: '0 0 auto', width: 16, height: 16, margin: '1px 0 0', accentColor: 'var(--accent)', cursor: 'pointer' }}
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
          <Button variant="primary" icon={Plus} onClick={openCreateForm}>
            Create custom role
          </Button>
        )}
      </div>

      {/* Role list */}
      {!isFormOpen && (
        <div className="n-card" style={{ marginBottom: 32 }}>
          {roles.map((role, i) => (
            <div key={role.id} style={listRow(i === roles.length - 1)}>
              <div style={{ flex: '1 1 240px', minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
                  <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>{role.display_name}</span>
                  <Badge>{role.is_system ? 'System' : 'Custom'}</Badge>
                </div>
                {role.description && (
                  <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{role.description}</div>
                )}
                <div style={{ marginTop: 2, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
                  {role.permissions.length} permission{role.permissions.length !== 1 ? 's' : ''}
                </div>
              </div>

              {!role.is_system && canEditRoles && (
                <div style={{ display: 'flex', gap: 8, marginLeft: 'auto' }}>
                  <Button size="sm" onClick={() => openEditForm(role)}>
                    Edit
                  </Button>
                  <Button size="sm" variant="danger" onClick={() => setDeleting(role)}>
                    Delete
                  </Button>
                </div>
              )}
            </div>
          ))}
          {roles.length === 0 && <PageState kind="empty" title="No roles defined yet." />}
        </div>
      )}

      {/* ---- Section 2: Create/Edit Form ---- */}
      {isFormOpen && (
        <div className="n-card" style={{ padding: 20, marginBottom: 32 }}>
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

          {/* Permission checkboxes grouped */}
          <div style={{ marginBottom: 20 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
              <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>Permissions</span>
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
