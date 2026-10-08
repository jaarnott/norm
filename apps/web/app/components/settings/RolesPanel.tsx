'use client';

import { useState, useEffect, useCallback, type CSSProperties } from 'react';
import { ChevronDown, ChevronRight, Plus } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import type { OrgMember } from '../../types';
import Avatar from '../ui/Avatar';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import Icon from '../ui/Icon';
import PageState from '../ui/PageState';

interface Permission {
  key: string;
  label: string;
  description: string;
}

interface PermissionGroup {
  label: string;
  permissions: Permission[];
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

export default function RolesPanel({ orgId }: RolesPanelProps) {
  const [roles, setRoles] = useState<Role[]>([]);
  const [members, setMembers] = useState<OrgMember[]>([]);
  const [permissionGroups, setPermissionGroups] = useState<Record<string, PermissionGroup>>({});
  const [allPermissions, setAllPermissions] = useState<Permission[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingRole, setEditingRole] = useState<Role | null>(null);
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [assigningUser, setAssigningUser] = useState<string | null>(null);

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
        const data = await res.json();
        setAllPermissions(data.permissions || []);
        setPermissionGroups(data.groups || {});
      }
    } catch { /* ignore */ }
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
  };

  const openEditForm = (role: Role) => {
    setCreating(false);
    setEditingRole(role);
    setFormName(role.name);
    setFormDisplayName(role.display_name);
    setFormDescription(role.description || '');
    setFormPermissions([...role.permissions]);
    setExpandedGroups({});
  };

  const closeForm = () => {
    setEditingRole(null);
    setCreating(false);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const body = JSON.stringify({
        name: formName,
        display_name: formDisplayName,
        description: formDescription || null,
        permissions: formPermissions,
      });

      if (editingRole) {
        await apiFetch(`/api/organizations/${orgId}/roles/${editingRole.id}`, {
          method: 'PUT',
          body,
        });
      } else {
        await apiFetch(`/api/organizations/${orgId}/roles`, {
          method: 'POST',
          body,
        });
      }
      closeForm();
      await fetchRoles();
    } catch { /* ignore */ }
    setSaving(false);
  };

  const handleDelete = async (roleId: string) => {
    if (!confirm('Delete this role? Members will lose their assigned role.')) return;
    await apiFetch(`/api/organizations/${orgId}/roles/${roleId}`, { method: 'DELETE' });
    await fetchRoles();
  };

  const handleAssignRole = async (userId: string, roleId: string) => {
    setAssigningUser(userId);
    try {
      await apiFetch(`/api/organizations/${orgId}/members/${userId}/role`, {
        method: 'PUT',
        body: JSON.stringify({ role_id: roleId }),
      });
      await fetchMembers();
    } catch { /* ignore */ }
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

  // One permission as a tile: ticked tiles are tinted with a tan edge, so the
  // grid reads at a glance as well as by its checkboxes.
  const renderPermission = (perm: Permission, withDescription: boolean) => {
    const checked = formPermissions.includes(perm.key);
    return (
      <label
        key={perm.key}
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
          onChange={() => togglePermission(perm.key)}
          style={{ flex: '0 0 auto', width: 16, height: 16, margin: '1px 0 0', accentColor: 'var(--accent)', cursor: 'pointer' }}
        />
        <span style={{ minWidth: 0 }}>
          <span style={{ display: 'block', fontSize: 'var(--fs-base)', fontWeight: 500, color: 'var(--text)' }}>{perm.label}</span>
          {withDescription && perm.description && (
            <span style={{ display: 'block', marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{perm.description}</span>
          )}
        </span>
      </label>
    );
  };

  return (
    <div>
      {/* ---- Section 1: Roles List ---- */}
      <div style={sectionHead}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, minWidth: 0 }}>
          <h3 style={sectionTitle}>Organization roles</h3>
          {roles.length > 0 && <span style={metaText}>{roles.length} {roles.length === 1 ? 'role' : 'roles'}</span>}
        </div>
        {!isFormOpen && (
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

              {!role.is_system && (
                <div style={{ display: 'flex', gap: 8, marginLeft: 'auto' }}>
                  <Button size="sm" onClick={() => openEditForm(role)}>
                    Edit
                  </Button>
                  <Button size="sm" variant="danger" onClick={() => handleDelete(role.id)}>
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
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {Object.entries(permissionGroups).map(([groupKey, group]) => {
                const isOpen = !!expandedGroups[groupKey];
                // "2 of 3" so a collapsed group still shows what it grants.
                // Guarded: only when the group carries its permission list.
                const ticked = Array.isArray(group.permissions)
                  ? group.permissions.filter(p => formPermissions.includes(p.key)).length
                  : null;
                return (
                  <div key={groupKey} style={{ border: '1px solid var(--line)', borderRadius: 'var(--radius)', overflow: 'hidden', backgroundColor: 'var(--bg)' }}>
                    <button
                      type="button"
                      className="n-row"
                      aria-expanded={isOpen}
                      onClick={() => toggleGroup(groupKey)}
                      style={{ borderRadius: 0, padding: '10px 12px', fontWeight: 600 }}
                    >
                      <Icon icon={isOpen ? ChevronDown : ChevronRight} tone="muted" />
                      <span style={{ flex: 1, minWidth: 0 }}>{group.label}</span>
                      {ticked !== null && (
                        <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 500, color: 'var(--muted)', whiteSpace: 'nowrap' }}>
                          {ticked} of {group.permissions.length}
                        </span>
                      )}
                    </button>
                    {isOpen && (
                      <div style={{ ...permissionGrid, padding: 12, borderTop: '1px solid var(--line-soft)' }}>
                        {group.permissions.map(perm => renderPermission(perm, true))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            {allPermissions.length > 0 && Object.keys(permissionGroups).length === 0 && (
              <div style={permissionGrid}>
                {allPermissions.map(perm => renderPermission(perm, false))}
              </div>
            )}
          </div>

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
                <select
                  className="n-select"
                  aria-label={`Role for ${member.full_name}`}
                  value={roles.find(r => r.name === member.role)?.id || ''}
                  onChange={e => handleAssignRole(member.user_id, e.target.value)}
                  disabled={assigningUser === member.user_id}
                  style={{ marginLeft: 'auto', minWidth: 180 }}
                >
                  {roles.map(role => (
                    <option key={role.id} value={role.id}>{role.display_name}</option>
                  ))}
                </select>
              </div>
            ))}
            {members.length === 0 && <PageState kind="empty" title="No members found." />}
          </div>
        </>
      )}
    </div>
  );
}
