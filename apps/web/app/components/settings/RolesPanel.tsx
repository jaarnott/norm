'use client';

import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../../lib/api';
import type { OrgMember } from '../../types';

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
    return <div style={{ fontSize: '0.85rem', color: '#999', padding: '2rem 0' }}>Loading roles...</div>;
  }

  const isFormOpen = creating || editingRole !== null;

  const inputStyle: React.CSSProperties = {
    width: '100%',
    padding: '0.5rem 0.65rem',
    fontSize: '0.82rem',
    border: '1px solid #e2ddd7',
    borderRadius: 6,
    backgroundColor: '#fff',
    fontFamily: 'inherit',
    color: '#1a1a1a',
    outline: 'none',
    boxSizing: 'border-box',
  };

  const labelStyle: React.CSSProperties = {
    fontSize: '0.75rem',
    fontWeight: 600,
    color: '#555',
    marginBottom: 4,
    display: 'block',
  };

  return (
    <div>
      {/* ---- Section 1: Roles List ---- */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1rem' }}>
        <h3 style={{ margin: 0, fontSize: '0.85rem', fontWeight: 600, color: '#666', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Organization Roles
        </h3>
        {!isFormOpen && (
          <button
            onClick={openCreateForm}
            style={{
              padding: '0.4rem 0.85rem',
              fontSize: '0.78rem',
              fontWeight: 600,
              border: 'none',
              borderRadius: 6,
              backgroundColor: '#1a1a1a',
              color: '#fff',
              cursor: 'pointer',
              fontFamily: 'inherit',
            }}
          >
            Create Custom Role
          </button>
        )}
      </div>

      {/* Role cards */}
      {!isFormOpen && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginBottom: '2rem' }}>
          {roles.map(role => (
            <div
              key={role.id}
              style={{
                padding: '0.75rem 1rem',
                border: '1px solid #e2ddd7',
                borderRadius: 8,
                backgroundColor: '#fff',
                display: 'flex',
                alignItems: 'center',
                gap: '0.75rem',
              }}
            >
              <div style={{ flex: 1 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  <span style={{ fontWeight: 600, fontSize: '0.88rem', color: '#1a1a1a' }}>{role.display_name}</span>
                  {role.is_system && (
                    <span style={{
                      fontSize: '0.6rem',
                      fontWeight: 600,
                      padding: '1px 6px',
                      borderRadius: 8,
                      backgroundColor: '#f0ebe5',
                      color: '#8a7356',
                    }}>
                      System
                    </span>
                  )}
                </div>
                {role.description && (
                  <div style={{ fontSize: '0.75rem', color: '#999', marginTop: 2 }}>{role.description}</div>
                )}
                <div style={{ fontSize: '0.7rem', color: '#bbb', marginTop: 2 }}>
                  {role.permissions.length} permission{role.permissions.length !== 1 ? 's' : ''}
                </div>
              </div>

              {!role.is_system && (
                <div style={{ display: 'flex', gap: '0.4rem' }}>
                  <button
                    onClick={() => openEditForm(role)}
                    style={{
                      padding: '0.3rem 0.6rem',
                      fontSize: '0.72rem',
                      border: '1px solid #e2ddd7',
                      borderRadius: 4,
                      backgroundColor: '#fff',
                      color: '#555',
                      cursor: 'pointer',
                      fontFamily: 'inherit',
                    }}
                  >
                    Edit
                  </button>
                  <button
                    onClick={() => handleDelete(role.id)}
                    style={{
                      padding: '0.3rem 0.6rem',
                      fontSize: '0.72rem',
                      border: '1px solid #f5c6cb',
                      borderRadius: 4,
                      backgroundColor: '#fff',
                      color: '#dc3545',
                      cursor: 'pointer',
                      fontFamily: 'inherit',
                    }}
                  >
                    Delete
                  </button>
                </div>
              )}
            </div>
          ))}
          {roles.length === 0 && (
            <div style={{ fontSize: '0.82rem', color: '#999' }}>No roles defined yet.</div>
          )}
        </div>
      )}

      {/* ---- Section 2: Create/Edit Form ---- */}
      {isFormOpen && (
        <div style={{
          padding: '1.25rem',
          border: '1px solid #e2ddd7',
          borderRadius: 8,
          backgroundColor: '#fff',
          marginBottom: '2rem',
        }}>
          <h4 style={{ margin: '0 0 1rem', fontSize: '0.9rem', fontWeight: 600, color: '#1a1a1a' }}>
            {editingRole ? `Edit Role: ${editingRole.display_name}` : 'Create Custom Role'}
          </h4>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginBottom: '1rem' }}>
            <div>
              <label style={labelStyle}>Name (slug)</label>
              <input
                value={formName}
                onChange={e => setFormName(e.target.value)}
                placeholder="e.g. venue-manager"
                style={inputStyle}
                disabled={!!editingRole}
              />
            </div>
            <div>
              <label style={labelStyle}>Display Name</label>
              <input
                value={formDisplayName}
                onChange={e => setFormDisplayName(e.target.value)}
                placeholder="e.g. Venue Manager"
                style={inputStyle}
              />
            </div>
            <div>
              <label style={labelStyle}>Description</label>
              <input
                value={formDescription}
                onChange={e => setFormDescription(e.target.value)}
                placeholder="Optional description"
                style={inputStyle}
              />
            </div>
          </div>

          {/* Permission checkboxes grouped */}
          <div style={{ marginBottom: '1rem' }}>
            <label style={{ ...labelStyle, marginBottom: 8 }}>Permissions</label>
            {Object.entries(permissionGroups).map(([groupKey, group]) => (
              <div key={groupKey} style={{ marginBottom: '0.5rem', border: '1px solid #f0ebe5', borderRadius: 6, overflow: 'hidden' }}>
                <button
                  onClick={() => toggleGroup(groupKey)}
                  style={{
                    width: '100%',
                    padding: '0.5rem 0.65rem',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    border: 'none',
                    backgroundColor: '#faf8f5',
                    cursor: 'pointer',
                    fontFamily: 'inherit',
                    fontSize: '0.8rem',
                    fontWeight: 600,
                    color: '#555',
                  }}
                >
                  <span>{group.label}</span>
                  <span style={{
                    fontSize: '0.6rem',
                    color: '#bbb',
                    transform: expandedGroups[groupKey] ? 'rotate(90deg)' : 'rotate(0deg)',
                    transition: 'transform 0.15s',
                  }}>&#9654;</span>
                </button>
                {expandedGroups[groupKey] && (
                  <div style={{ padding: '0.5rem 0.65rem', display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
                    {group.permissions.map(perm => (
                      <label
                        key={perm.key}
                        style={{
                          display: 'flex',
                          alignItems: 'flex-start',
                          gap: '0.5rem',
                          fontSize: '0.78rem',
                          color: '#333',
                          cursor: 'pointer',
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={formPermissions.includes(perm.key)}
                          onChange={() => togglePermission(perm.key)}
                          style={{ marginTop: 2 }}
                        />
                        <div>
                          <div style={{ fontWeight: 500 }}>{perm.label}</div>
                          {perm.description && (
                            <div style={{ fontSize: '0.7rem', color: '#999' }}>{perm.description}</div>
                          )}
                        </div>
                      </label>
                    ))}
                  </div>
                )}
              </div>
            ))}
            {allPermissions.length > 0 && Object.keys(permissionGroups).length === 0 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
                {allPermissions.map(perm => (
                  <label key={perm.key} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.78rem', color: '#333', cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      checked={formPermissions.includes(perm.key)}
                      onChange={() => togglePermission(perm.key)}
                    />
                    {perm.label}
                  </label>
                ))}
              </div>
            )}
          </div>

          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button
              onClick={handleSave}
              disabled={saving || !formName || !formDisplayName}
              style={{
                padding: '0.45rem 1rem',
                fontSize: '0.8rem',
                fontWeight: 600,
                border: 'none',
                borderRadius: 6,
                backgroundColor: saving ? '#999' : '#1a1a1a',
                color: '#fff',
                cursor: saving ? 'default' : 'pointer',
                fontFamily: 'inherit',
              }}
            >
              {saving ? 'Saving...' : editingRole ? 'Update Role' : 'Create Role'}
            </button>
            <button
              onClick={closeForm}
              style={{
                padding: '0.45rem 1rem',
                fontSize: '0.8rem',
                border: '1px solid #e2ddd7',
                borderRadius: 6,
                backgroundColor: '#fff',
                color: '#555',
                cursor: 'pointer',
                fontFamily: 'inherit',
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* ---- Section 3: Members & Role Assignment ---- */}
      {!isFormOpen && (
        <>
          <h3 style={{ margin: '0 0 1rem', fontSize: '0.85rem', fontWeight: 600, color: '#666', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Member Role Assignments
          </h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            {members.map(member => (
              <div
                key={member.id}
                style={{
                  padding: '0.65rem 1rem',
                  border: '1px solid #e2ddd7',
                  borderRadius: 8,
                  backgroundColor: '#fff',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.75rem',
                }}
              >
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 500, fontSize: '0.85rem', color: '#1a1a1a' }}>{member.full_name}</div>
                  <div style={{ fontSize: '0.72rem', color: '#999' }}>{member.email}</div>
                </div>
                <select
                  value={roles.find(r => r.name === member.role)?.id || ''}
                  onChange={e => handleAssignRole(member.user_id, e.target.value)}
                  disabled={assigningUser === member.user_id}
                  style={{
                    padding: '0.35rem 0.5rem',
                    fontSize: '0.78rem',
                    border: '1px solid #e2ddd7',
                    borderRadius: 6,
                    backgroundColor: '#fff',
                    fontFamily: 'inherit',
                    color: '#333',
                    cursor: 'pointer',
                    outline: 'none',
                  }}
                >
                  {roles.map(role => (
                    <option key={role.id} value={role.id}>{role.display_name}</option>
                  ))}
                </select>
              </div>
            ))}
            {members.length === 0 && (
              <div style={{ fontSize: '0.82rem', color: '#999' }}>No members found.</div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
