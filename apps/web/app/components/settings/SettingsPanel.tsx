'use client';

import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../../lib/api';
import type { AgentConfig, AgentBinding, VenueDetail, Organization, OrgMember } from '../../types';
import ConnectorSpecsPanel from './ConnectorSpecsPanel';
import BillingTab from './BillingTab';
import EmailTab from './EmailTab';
import DeploymentsPanel from './DeploymentsPanel';
import TestsPanel from './TestsPanel';
import RolesPanel from './RolesPanel';
import SecretsPanel from './SecretsPanel';
import ComponentsPanel from './ComponentsPanel';
import { getStoredUser } from '../../lib/api';
import type { User } from '../../types';

interface ConnectorField {
  key: string;
  label: string;
  secret: boolean;
  type?: string;
  options?: { id: string; label: string }[];
  default?: string;
}

interface ConnectorMeta {
  name: string;
  label: string;
  domain: string;
  fields: ConnectorField[];
  auth_type?: string;
  configured: boolean;
  enabled: boolean;
  config: Record<string, string>;
  oauth_connected?: boolean;
  spec_driven?: boolean;
}

type TestStatus = 'idle' | 'testing' | 'success' | 'error';
// --- Venues Tab ---

interface VenueConnector {
  name: string;
  label: string;
  auth_type: string;
  configured: boolean;
  enabled: boolean;
  oauth_connected?: boolean;
  spec_driven?: boolean;
  fields?: { key: string; label: string; secret: boolean }[];
  config?: Record<string, string>;
}

function VenueCard({ venue, onDelete }: { venue: VenueDetail; onDelete: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [connectors, setConnectors] = useState<VenueConnector[]>([]);
  const [connectorForms, setConnectorForms] = useState<Record<string, Record<string, string>>>({});
  const [savingConnector, setSavingConnector] = useState<string | null>(null);
  const [loadingConnectors, setLoadingConnectors] = useState(false);
  const [editingConnector, setEditingConnector] = useState<string | null>(null);

  const loadConnectors = useCallback(async () => {
    setLoadingConnectors(true);
    try {
      const res = await apiFetch(`/api/connectors?venue_id=${venue.id}`);
      if (res.ok) {
        const data = await res.json();
        const filtered = (data.connectors || []).filter((c: VenueConnector) => c.spec_driven && c.auth_type !== 'none');
        setConnectors(filtered);
        const forms: Record<string, Record<string, string>> = {};
        for (const c of filtered) {
          forms[c.name] = { ...(c.config || {}) };
        }
        setConnectorForms(forms);
      }
    } catch { /* ignore */ }
    setLoadingConnectors(false);
  }, [venue.id]);

  const handleToggle = async () => {
    const next = !expanded;
    setExpanded(next);
    if (next && connectors.length === 0) {
      await loadConnectors();
    }
  };

  const handleSaveConnector = async (connectorName: string) => {
    setSavingConnector(connectorName);
    try {
      await apiFetch(`/api/connectors/${connectorName}`, {
        method: 'PUT',
        body: JSON.stringify({ config: connectorForms[connectorName] || {}, venue_id: venue.id }),
      });
      setEditingConnector(null);
      await loadConnectors();
    } catch { /* ignore */ }
    setSavingConnector(null);
  };

  const handleOAuthConnect = async (connectorName: string) => {
    try {
      const res = await apiFetch(`/api/oauth/authorize/${connectorName}?venue_id=${venue.id}`);
      if (!res.ok) return;
      const data = await res.json();
      const popup = window.open(data.authorize_url, `oauth_${connectorName}_${venue.id}`, 'width=600,height=700');
      if (popup) {
        const timer = setInterval(() => {
          if (popup.closed) {
            clearInterval(timer);
            loadConnectors();
          }
        }, 500);
      }
    } catch { /* ignore */ }
  };

  const handleOAuthDisconnect = async (connectorName: string) => {
    await apiFetch(`/api/oauth/disconnect/${connectorName}?venue_id=${venue.id}`, { method: 'POST' });
    loadConnectors();
  };

  return (
    <div>
      <div
        onClick={handleToggle}
        style={{
          padding: '0.75rem 1rem', border: '1px solid #e5e7eb',
          borderRadius: expanded ? '8px 8px 0 0' : 8,
          backgroundColor: '#fff', display: 'flex', alignItems: 'center', gap: '0.75rem',
          cursor: 'pointer',
        }}
      >
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, color: '#111', fontSize: '0.9rem' }}>{venue.name}</div>
          {venue.location && <div style={{ fontSize: '0.75rem', color: '#999' }}>{venue.location}</div>}
          {venue.timezone && <div style={{ fontSize: '0.7rem', color: '#aaa' }}>{venue.timezone}</div>}
        </div>
        <div style={{ fontSize: '0.72rem', color: '#999' }}>
          {venue.connector_count || 0} connector{(venue.connector_count || 0) !== 1 ? 's' : ''}
        </div>
        <span style={{
          fontSize: '0.6rem', color: '#bbb',
          transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
          transition: 'transform 0.15s',
        }}>&#9654;</span>
      </div>

      {expanded && (
        <div style={{
          padding: '0.75rem 1rem', border: '1px solid #e5e7eb', borderTop: 'none',
          borderRadius: '0 0 8px 8px', backgroundColor: '#fafafa',
        }}>
          {loadingConnectors ? (
            <div style={{ fontSize: '0.75rem', color: '#999' }}>Loading connectors...</div>
          ) : connectors.length === 0 ? (
            <div style={{ fontSize: '0.75rem', color: '#999' }}>No connectors available. Add connector specs first.</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
              {connectors.map(c => {
                const isEditing = editingConnector === c.name;
                const form = connectorForms[c.name] || {};
                const fields = c.fields || [];
                return (
                  <div key={c.name} style={{
                    backgroundColor: '#fff', border: '1px solid #f3f4f6', borderRadius: 6,
                    overflow: 'hidden',
                  }}>
                    <div style={{
                      display: 'flex', alignItems: 'center', gap: '0.5rem',
                      padding: '0.5rem 0.6rem',
                    }}>
                      <span style={{ fontSize: '0.82rem', fontWeight: 500, color: '#333', flex: 1 }}>{c.label}</span>
                      {c.auth_type === 'oauth2' ? (
                        c.oauth_connected ? (
                          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                            <span style={{
                              fontSize: '0.65rem', fontWeight: 600, padding: '1px 6px', borderRadius: 8,
                              backgroundColor: '#d1fae5', color: '#065f46',
                            }}>Connected</span>
                            <button onClick={(e) => { e.stopPropagation(); handleOAuthDisconnect(c.name); }} style={{
                              padding: '2px 8px', fontSize: '0.68rem', border: '1px solid #ddd',
                              borderRadius: 4, backgroundColor: '#fff', color: '#666',
                              cursor: 'pointer', fontFamily: 'inherit',
                            }}>Disconnect</button>
                          </div>
                        ) : (
                          <button onClick={(e) => { e.stopPropagation(); handleOAuthConnect(c.name); }} style={{
                            padding: '3px 10px', fontSize: '0.72rem', fontWeight: 600,
                            border: 'none', borderRadius: 6, backgroundColor: '#111', color: '#fff',
                            cursor: 'pointer', fontFamily: 'inherit',
                          }}>Connect</button>
                        )
                      ) : (
                        <button onClick={(e) => { e.stopPropagation(); setEditingConnector(isEditing ? null : c.name); }} style={{
                          padding: '2px 8px', fontSize: '0.68rem', border: '1px solid #ddd',
                          borderRadius: 4, backgroundColor: '#fff',
                          color: c.configured ? '#065f46' : '#666',
                          cursor: 'pointer', fontFamily: 'inherit',
                        }}>{c.configured ? 'Edit' : 'Configure'}</button>
                      )}
                    </div>

                    {/* Credential form */}
                    {isEditing && fields.length > 0 && (
                      <div style={{
                        padding: '0.5rem 0.6rem', borderTop: '1px solid #f3f4f6',
                        display: 'flex', flexDirection: 'column', gap: '0.4rem',
                      }}>
                        {fields.map(f => (
                          <div key={f.key}>
                            <label style={{ fontSize: '0.65rem', color: '#666', fontWeight: 600 }}>{f.label}</label>
                            <input
                              type={f.secret ? 'password' : 'text'}
                              value={form[f.key] || ''}
                              onChange={e => setConnectorForms(prev => ({
                                ...prev,
                                [c.name]: { ...(prev[c.name] || {}), [f.key]: e.target.value },
                              }))}
                              placeholder={f.secret && c.configured ? '••••••••' : f.label}
                              style={{
                                width: '100%', padding: '4px 8px', border: '1px solid #ddd',
                                borderRadius: 4, fontSize: '0.78rem', fontFamily: 'inherit',
                              }}
                            />
                          </div>
                        ))}
                        <div style={{ display: 'flex', gap: '0.3rem', marginTop: '0.25rem' }}>
                          <button onClick={(e) => { e.stopPropagation(); handleSaveConnector(c.name); }}
                            disabled={savingConnector === c.name}
                            style={{
                              padding: '4px 12px', fontSize: '0.72rem', fontWeight: 600,
                              backgroundColor: '#111', color: '#fff', border: 'none', borderRadius: 4,
                              cursor: 'pointer', fontFamily: 'inherit',
                            }}>{savingConnector === c.name ? 'Saving...' : 'Save'}</button>
                          <button onClick={(e) => { e.stopPropagation(); setEditingConnector(null); }} style={{
                            padding: '4px 12px', fontSize: '0.72rem',
                            backgroundColor: '#fff', color: '#666', border: '1px solid #ddd', borderRadius: 4,
                            cursor: 'pointer', fontFamily: 'inherit',
                          }}>Cancel</button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {/* Delete venue */}
          <div style={{ borderTop: '1px solid #e5e7eb', marginTop: '0.75rem', paddingTop: '0.5rem' }}>
            <button onClick={(e) => { e.stopPropagation(); onDelete(); }} style={{
              padding: '4px 12px', fontSize: '0.72rem', fontWeight: 500,
              border: '1px solid #fecaca', borderRadius: 6, backgroundColor: '#fff', color: '#dc2626',
              cursor: 'pointer', fontFamily: 'inherit',
            }}>Delete venue</button>
          </div>
        </div>
      )}
    </div>
  );
}

function VenuesTab() {
  const [org, setOrg] = useState<Organization | null>(null);
  const [venues, setVenues] = useState<VenueDetail[]>([]);
  const [loading, setLoading] = useState(true);
  const [newName, setNewName] = useState('');
  const [newLocation, setNewLocation] = useState('');
  const [newTimezone, setNewTimezone] = useState('');
  const [adding, setAdding] = useState(false);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const orgRes = await apiFetch('/api/organizations');
      const orgData = await orgRes.json();
      const orgs = orgData.organizations || [];
      if (orgs.length > 0) {
        const detailRes = await apiFetch(`/api/organizations/${orgs[0].id}`);
        const detail = await detailRes.json();
        setOrg(detail);
        setVenues(detail.venues || []);
      }
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  const handleAdd = async () => {
    if (!newName.trim() || !org) return;
    await apiFetch(`/api/organizations/${org.id}/venues`, {
      method: 'POST',
      body: JSON.stringify({ name: newName, location: newLocation || null, timezone: newTimezone || null }),
    });
    setNewName('');
    setNewLocation('');
    setNewTimezone('');
    setAdding(false);
    loadData();
  };

  const handleDelete = async (venueId: string) => {
    await apiFetch(`/api/venues/${venueId}`, { method: 'DELETE' });
    loadData();
  };

  if (loading) return <div style={{ color: '#999' }}>Loading...</div>;

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1rem' }}>
        <h3 style={{ margin: 0, fontSize: '0.85rem', fontWeight: 600, color: '#666', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Venues {org && <span style={{ fontWeight: 400, textTransform: 'none' }}>— {org.name}</span>}
        </h3>
        <button onClick={() => setAdding(!adding)} style={{
          padding: '4px 12px', fontSize: '0.75rem', fontWeight: 600,
          border: '1px solid #ddd', borderRadius: 6, backgroundColor: '#fff',
          cursor: 'pointer', fontFamily: 'inherit', color: '#333',
        }}>+ Add Venue</button>
      </div>

      {adding && (
        <div style={{
          padding: '0.75rem', border: '1px solid #dbeafe', borderRadius: 8,
          backgroundColor: '#f8fafc', marginBottom: '1rem',
          display: 'flex', gap: '0.5rem', alignItems: 'flex-end',
        }}>
          <div style={{ flex: 1 }}>
            <label style={{ fontSize: '0.68rem', color: '#666', fontWeight: 600 }}>Name</label>
            <input value={newName} onChange={e => setNewName(e.target.value)} placeholder="Venue name"
              style={{ width: '100%', padding: '4px 8px', border: '1px solid #ddd', borderRadius: 4, fontSize: '0.82rem', fontFamily: 'inherit' }} />
          </div>
          <div style={{ flex: 1 }}>
            <label style={{ fontSize: '0.68rem', color: '#666', fontWeight: 600 }}>Location</label>
            <input value={newLocation} onChange={e => setNewLocation(e.target.value)} placeholder="Address (optional)"
              style={{ width: '100%', padding: '4px 8px', border: '1px solid #ddd', borderRadius: 4, fontSize: '0.82rem', fontFamily: 'inherit' }} />
          </div>
          <div style={{ flex: 1 }}>
            <label style={{ fontSize: '0.68rem', color: '#666', fontWeight: 600 }}>Timezone</label>
            <input value={newTimezone} onChange={e => setNewTimezone(e.target.value)} placeholder="e.g. Pacific/Auckland"
              style={{ width: '100%', padding: '4px 8px', border: '1px solid #ddd', borderRadius: 4, fontSize: '0.82rem', fontFamily: 'inherit' }} />
          </div>
          <button onClick={handleAdd} style={{
            padding: '5px 14px', fontSize: '0.75rem', fontWeight: 600,
            backgroundColor: '#111', color: '#fff', border: 'none', borderRadius: 6,
            cursor: 'pointer', fontFamily: 'inherit',
          }}>Add</button>
          <button onClick={() => setAdding(false)} style={{
            padding: '5px 12px', fontSize: '0.75rem',
            backgroundColor: 'transparent', color: '#666', border: '1px solid #ddd', borderRadius: 6,
            cursor: 'pointer', fontFamily: 'inherit',
          }}>Cancel</button>
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        {venues.map(v => (
          <VenueCard key={v.id} venue={v} onDelete={() => handleDelete(v.id)} />
        ))}
        {venues.length === 0 && (
          <div style={{ padding: '2rem', textAlign: 'center', color: '#999', fontSize: '0.82rem' }}>
            No venues yet. Click "Add Venue" to create one.
          </div>
        )}
      </div>
    </div>
  );
}

// --- Users Tab ---

interface UsageByUser { input_tokens: number; output_tokens: number; llm_call_count: number }
interface DailyUsageEntry { input_tokens: number; output_tokens: number; llm_call_count: number }

function UsersTab() {
  const [org, setOrg] = useState<Organization | null>(null);
  const [members, setMembers] = useState<OrgMember[]>([]);
  const [venues, setVenues] = useState<VenueDetail[]>([]);
  const [memberVenues, setMemberVenues] = useState<Record<string, string[]>>({});
  const [usage, setUsage] = useState<Record<string, UsageByUser>>({});
  const [usageTotals, setUsageTotals] = useState<{ input: number; output: number; calls: number }>({ input: 0, output: 0, calls: 0 });
  const [loading, setLoading] = useState(true);
  const [addEmail, setAddEmail] = useState('');
  const [addRole, setAddRole] = useState('');
  const [addVenueIds, setAddVenueIds] = useState<string[]>([]);
  const [addError, setAddError] = useState('');
  const [addSuccess, setAddSuccess] = useState('');
  const [showInviteModal, setShowInviteModal] = useState(false);
  const [resendStatus, setResendStatus] = useState<Record<string, 'sending' | 'sent' | 'failed'>>({});
  const [viewingRoleId, setViewingRoleId] = useState<string | null>(null);
  const [availableRoles, setAvailableRoles] = useState<{ id: string; name: string; display_name: string; is_system: boolean; permissions: string[] }[]>([]);
  const [expandedMember, setExpandedMember] = useState<string | null>(null);
  const [memberDailyUsage, setMemberDailyUsage] = useState<Record<string, Record<string, DailyUsageEntry>>>({});
  const [showDailyUsage, setShowDailyUsage] = useState(false);
  const [dailyUsage, setDailyUsage] = useState<Record<string, DailyUsageEntry>>({});

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const orgRes = await apiFetch('/api/organizations');
      const orgData = await orgRes.json();
      const orgs = orgData.organizations || [];
      if (orgs.length > 0) {
        const detailRes = await apiFetch(`/api/organizations/${orgs[0].id}`);
        const detail = await detailRes.json();
        setOrg(detail);
        setMembers(detail.members || []);
        setVenues(detail.venues || []);
        // Load venue access for each member
        const venueMap: Record<string, string[]> = {};
        for (const m of detail.members || []) {
          const vRes = await apiFetch(`/api/users/${m.user_id}/venues`);
          if (vRes.ok) {
            const vData = await vRes.json();
            venueMap[m.user_id] = (vData.venues || []).map((v: VenueDetail) => v.id);
          }
        }
        setMemberVenues(venueMap);

        // Load available roles for role assignment dropdown
        const rolesRes = await apiFetch(`/api/organizations/${orgs[0].id}/roles`);
        if (rolesRes.ok) {
          const rolesData = await rolesRes.json();
          setAvailableRoles(rolesData.roles || []);
        }

        // Load token usage for current month
        const usageRes = await apiFetch(`/api/organizations/${orgs[0].id}/usage`);
        if (usageRes.ok) {
          const usageData = await usageRes.json();
          setUsage(usageData.by_user || {});
          setUsageTotals({
            input: usageData.total_input_tokens || 0,
            output: usageData.total_output_tokens || 0,
            calls: usageData.total_llm_calls || 0,
          });
        }
      }
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  const handleInvite = async () => {
    if (!addEmail.trim() || !org) return;
    setAddError('');
    setAddSuccess('');
    try {
      const roleId = addRole || (availableRoles.length > 0 ? availableRoles[0].id : '');
      const res = await apiFetch('/api/auth/invite', {
        method: 'POST',
        body: JSON.stringify({
          email: addEmail,
          org_id: org.id,
          role_id: roleId,
          venue_ids: addVenueIds,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setAddError(data.detail || 'Failed to send invite');
        return;
      }
      setAddSuccess(`Invite sent to ${addEmail}`);
      setAddEmail('');
      setAddVenueIds([]);
      loadData();
    } catch { setAddError('Network error'); }
  };

  const handleResendInvite = async (email: string) => {
    if (!org) return;
    setResendStatus(prev => ({ ...prev, [email]: 'sending' }));
    try {
      const roleId = availableRoles.length > 0 ? availableRoles[0].id : '';
      const res = await apiFetch('/api/auth/invite', {
        method: 'POST',
        body: JSON.stringify({ email, org_id: org.id, role_id: roleId, venue_ids: [] }),
      });
      setResendStatus(prev => ({ ...prev, [email]: res.ok ? 'sent' : 'failed' }));
      // Clear status after 3 seconds
      setTimeout(() => setResendStatus(prev => { const n = { ...prev }; delete n[email]; return n; }), 3000);
    } catch {
      setResendStatus(prev => ({ ...prev, [email]: 'failed' }));
    }
  };

  const handleRemove = async (userId: string) => {
    if (!org) return;
    await apiFetch(`/api/organizations/${org.id}/members/${userId}`, { method: 'DELETE' });
    loadData();
  };

  const handleToggleVenue = async (userId: string, venueId: string, checked: boolean) => {
    const current = memberVenues[userId] || [];
    const updated = checked ? [...current, venueId] : current.filter(id => id !== venueId);
    await apiFetch(`/api/users/${userId}/venues`, {
      method: 'PUT',
      body: JSON.stringify({ venue_ids: updated }),
    });
    setMemberVenues(prev => ({ ...prev, [userId]: updated }));
  };

  const handleRoleChange = async (userId: string, roleId: string) => {
    if (!org) return;
    await apiFetch(`/api/organizations/${org.id}/members/${userId}/role`, {
      method: 'PUT',
      body: JSON.stringify({ role_id: roleId }),
    });
    loadData();
  };

  if (loading) return <div style={{ color: '#999' }}>Loading...</div>;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
        <h3 style={{ margin: 0, fontSize: '0.85rem', fontWeight: 600, color: '#666', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Users {org && <span style={{ fontWeight: 400, textTransform: 'none' }}>— {org.name}</span>}
        </h3>
        <button onClick={() => { setShowInviteModal(true); setAddError(''); setAddSuccess(''); setAddEmail(''); setAddVenueIds([]); }}
          style={{
            padding: '6px 16px', fontSize: '0.75rem', fontWeight: 600,
            backgroundColor: '#c4a882', color: '#fff', border: 'none', borderRadius: 6,
            cursor: 'pointer', fontFamily: 'inherit',
          }}>+ Invite User</button>
      </div>

      {/* Usage summary */}
      {usageTotals.calls > 0 && (
        <div style={{ marginBottom: '1rem' }}>
          <div style={{
            display: 'flex', gap: '2rem',
            padding: '0.75rem 1rem', border: '1px solid #e5e7eb', borderRadius: showDailyUsage ? '8px 8px 0 0' : 8,
            backgroundColor: '#fafafa',
          }}>
            <div
              onClick={async () => {
                const next = !showDailyUsage;
                setShowDailyUsage(next);
                if (next && org && Object.keys(dailyUsage).length === 0) {
                  const res = await apiFetch(`/api/organizations/${org.id}/usage/daily`);
                  if (res.ok) {
                    const d = await res.json();
                    setDailyUsage(d.days || {});
                  }
                }
              }}
              style={{ cursor: 'pointer' }}
            >
              <div style={{ fontSize: '0.65rem', fontWeight: 600, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.03em', display: 'flex', alignItems: 'center', gap: 4 }}>
                This Month
                <span style={{ fontSize: '0.55rem', transform: showDailyUsage ? 'rotate(90deg)' : 'rotate(0deg)', transition: 'transform 0.15s' }}>&#9654;</span>
              </div>
              <div style={{ fontSize: '1.1rem', fontWeight: 700, color: '#111' }}>
                {((usageTotals.input + usageTotals.output) / 1000).toFixed(1)}K
                <span style={{ fontSize: '0.72rem', fontWeight: 400, color: '#999', marginLeft: 4 }}>tokens</span>
              </div>
            </div>
            <div>
              <div style={{ fontSize: '0.65rem', fontWeight: 600, color: '#9ca3af', textTransform: 'uppercase' }}>Input</div>
              <div style={{ fontSize: '0.85rem', fontWeight: 600, color: '#111' }}>{(usageTotals.input / 1000).toFixed(1)}K</div>
            </div>
            <div>
              <div style={{ fontSize: '0.65rem', fontWeight: 600, color: '#9ca3af', textTransform: 'uppercase' }}>Output</div>
              <div style={{ fontSize: '0.85rem', fontWeight: 600, color: '#111' }}>{(usageTotals.output / 1000).toFixed(1)}K</div>
            </div>
            <div>
              <div style={{ fontSize: '0.65rem', fontWeight: 600, color: '#9ca3af', textTransform: 'uppercase' }}>LLM Calls</div>
              <div style={{ fontSize: '0.85rem', fontWeight: 600, color: '#111' }}>{usageTotals.calls}</div>
            </div>
          </div>

          {/* Daily usage chart */}
          {showDailyUsage && (
            <div style={{
              padding: '0.75rem 1rem', border: '1px solid #e5e7eb', borderTop: 'none',
              borderRadius: '0 0 8px 8px', backgroundColor: '#fff',
            }}>
              <div style={{ fontSize: '0.65rem', fontWeight: 600, color: '#9ca3af', textTransform: 'uppercase', marginBottom: '0.5rem' }}>
                Daily Usage
              </div>
              {Object.keys(dailyUsage).length === 0 ? (
                <div style={{ fontSize: '0.75rem', color: '#999' }}>No daily data yet</div>
              ) : (() => {
                const entries = Object.entries(dailyUsage).sort(([a], [b]) => a.localeCompare(b));
                const maxTokens = Math.max(...entries.map(([, d]) => (d.input_tokens || 0) + (d.output_tokens || 0)), 1);
                return (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                    {entries.map(([date, d]) => {
                      const total = (d.input_tokens || 0) + (d.output_tokens || 0);
                      const inputPct = (d.input_tokens || 0) / maxTokens * 100;
                      const outputPct = (d.output_tokens || 0) / maxTokens * 100;
                      const dayLabel = new Date(date + 'T00:00:00').toLocaleDateString('en-NZ', { weekday: 'short', day: 'numeric', month: 'short' });
                      return (
                        <div key={date} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                          <span style={{ fontSize: '0.68rem', color: '#999', width: 80, textAlign: 'right', flexShrink: 0 }}>{dayLabel}</span>
                          <div style={{ flex: 1, height: 16, backgroundColor: '#f3f4f6', borderRadius: 3, overflow: 'hidden', display: 'flex' }}>
                            <div style={{ width: `${inputPct}%`, backgroundColor: '#93c5fd', height: '100%' }} title={`Input: ${(d.input_tokens || 0).toLocaleString()}`} />
                            <div style={{ width: `${outputPct}%`, backgroundColor: '#6366f1', height: '100%' }} title={`Output: ${(d.output_tokens || 0).toLocaleString()}`} />
                          </div>
                          <span style={{ fontSize: '0.65rem', color: '#999', width: 50, flexShrink: 0 }}>
                            {total >= 1000 ? `${(total / 1000).toFixed(1)}K` : total}
                          </span>
                        </div>
                      );
                    })}
                    <div style={{ display: 'flex', gap: '1rem', marginTop: '0.25rem', fontSize: '0.6rem', color: '#bbb' }}>
                      <span><span style={{ display: 'inline-block', width: 8, height: 8, backgroundColor: '#93c5fd', borderRadius: 2, marginRight: 3 }} />Input</span>
                      <span><span style={{ display: 'inline-block', width: 8, height: 8, backgroundColor: '#6366f1', borderRadius: 2, marginRight: 3 }} />Output</span>
                    </div>
                  </div>
                );
              })()}
            </div>
          )}
        </div>
      )}

      {/* Invite User Modal */}
      {showInviteModal && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
          backgroundColor: 'rgba(0,0,0,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center',
          zIndex: 1000,
        }} onClick={() => setShowInviteModal(false)}>
          <div style={{
            backgroundColor: '#fff', borderRadius: 12, padding: '1.5rem', width: 420, maxWidth: '90vw',
            boxShadow: '0 8px 32px rgba(0,0,0,0.15)',
          }} onClick={e => e.stopPropagation()}>
            <h3 style={{ margin: '0 0 1rem', fontSize: '0.95rem', fontWeight: 600, color: '#1a1a1a' }}>Invite User</h3>

            <div style={{ marginBottom: '0.75rem' }}>
              <label style={{ fontSize: '0.72rem', color: '#666', fontWeight: 600, display: 'block', marginBottom: 4 }}>Email</label>
              <input value={addEmail} onChange={e => { setAddEmail(e.target.value); setAddError(''); setAddSuccess(''); }}
                placeholder="user@example.com"
                style={{ width: '100%', padding: '0.65rem', border: '1px solid #e2ddd7', borderRadius: 8, fontSize: '0.82rem', fontFamily: 'inherit', boxSizing: 'border-box' }} />
            </div>

            <div style={{ marginBottom: '0.75rem' }}>
              <label style={{ fontSize: '0.72rem', color: '#666', fontWeight: 600, display: 'block', marginBottom: 4 }}>Role</label>
              <select value={addRole} onChange={e => setAddRole(e.target.value)}
                style={{ width: '100%', padding: '0.65rem', border: '1px solid #e2ddd7', borderRadius: 8, fontSize: '0.82rem', fontFamily: 'inherit', boxSizing: 'border-box' }}>
                {availableRoles.map(r => (
                  <option key={r.id} value={r.id}>{r.display_name}</option>
                ))}
                {availableRoles.length === 0 && <option value="">No roles</option>}
              </select>
            </div>

            {venues.length > 0 && (
              <div style={{ marginBottom: '0.75rem' }}>
                <label style={{ fontSize: '0.72rem', color: '#666', fontWeight: 600, display: 'block', marginBottom: 4 }}>Venues</label>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
                  {venues.map(v => (
                    <label key={v.id} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: '0.78rem', color: '#555', cursor: 'pointer' }}>
                      <input type="checkbox" checked={addVenueIds.includes(v.id)}
                        onChange={e => {
                          setAddVenueIds(prev => e.target.checked ? [...prev, v.id] : prev.filter(id => id !== v.id));
                        }}
                        style={{ accentColor: '#c4a882' }} />
                      {v.name}
                    </label>
                  ))}
                </div>
              </div>
            )}

            {addError && <div style={{ color: '#dc2626', fontSize: '0.78rem', marginBottom: '0.5rem' }}>{addError}</div>}
            {addSuccess && <div style={{ color: '#28a745', fontSize: '0.78rem', marginBottom: '0.5rem' }}>{addSuccess}</div>}

            <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end', marginTop: '1rem' }}>
              <button onClick={() => setShowInviteModal(false)} style={{
                padding: '8px 16px', fontSize: '0.78rem', fontWeight: 500,
                backgroundColor: 'transparent', color: '#666', border: '1px solid #e2ddd7', borderRadius: 8,
                cursor: 'pointer', fontFamily: 'inherit',
              }}>Cancel</button>
              <button onClick={async () => { await handleInvite(); }} style={{
                padding: '8px 20px', fontSize: '0.78rem', fontWeight: 600,
                backgroundColor: '#c4a882', color: '#fff', border: 'none', borderRadius: 8,
                cursor: 'pointer', fontFamily: 'inherit',
              }}>Send Invite</button>
            </div>
          </div>
        </div>
      )}

      {/* Role Permissions Modal */}
      {viewingRoleId && (() => {
        const role = availableRoles.find(r => r.id === viewingRoleId);
        if (!role) return null;
        const perms = (role as { permissions?: string[] }).permissions || [];
        // Group permissions by category
        const groups: Record<string, string[]> = {};
        for (const p of perms) {
          const [cat] = p.split(':');
          const label = cat.charAt(0).toUpperCase() + cat.slice(1);
          if (!groups[label]) groups[label] = [];
          groups[label].push(p);
        }
        return (
          <div style={{
            position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
            backgroundColor: 'rgba(0,0,0,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center',
            zIndex: 1000,
          }} onClick={() => setViewingRoleId(null)}>
            <div style={{
              backgroundColor: '#fff', borderRadius: 12, padding: '1.5rem', width: 400, maxWidth: '90vw',
              maxHeight: '80vh', overflow: 'auto', boxShadow: '0 8px 32px rgba(0,0,0,0.15)',
            }} onClick={e => e.stopPropagation()}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                <h3 style={{ margin: 0, fontSize: '0.95rem', fontWeight: 600, color: '#1a1a1a' }}>
                  {role.display_name} Permissions
                </h3>
                <button onClick={() => setViewingRoleId(null)} style={{
                  border: 'none', background: 'none', cursor: 'pointer', fontSize: '1.1rem', color: '#999',
                }}>×</button>
              </div>
              <p style={{ fontSize: '0.78rem', color: '#666', margin: '0 0 1rem' }}>
                {perms.length} permissions granted
              </p>
              {Object.entries(groups).map(([cat, catPerms]) => (
                <div key={cat} style={{ marginBottom: '0.75rem' }}>
                  <div style={{ fontSize: '0.7rem', fontWeight: 600, color: '#999', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 4 }}>
                    {cat}
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {catPerms.map(p => (
                      <span key={p} style={{
                        fontSize: '0.68rem', padding: '2px 8px', borderRadius: 4,
                        backgroundColor: '#f0ebe5', color: '#8a7356',
                      }}>{p.split(':')[1]}</span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        );
      })()}

      {/* User list */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
        {members.map(m => {
          const isExpanded = expandedMember === m.user_id;
          const userVenues = memberVenues[m.user_id] || [];
          return (
            <div key={m.id}>
              <div
                onClick={async () => {
                  const next = isExpanded ? null : m.user_id;
                  setExpandedMember(next);
                  if (next && org && !memberDailyUsage[m.user_id]) {
                    const res = await apiFetch(`/api/organizations/${org.id}/usage/daily?user_id=${m.user_id}`);
                    if (res.ok) {
                      const d = await res.json();
                      setMemberDailyUsage(prev => ({ ...prev, [m.user_id]: d.days || {} }));
                    }
                  }
                }}
                style={{
                  padding: '0.6rem 1rem', border: '1px solid #e5e7eb',
                  borderRadius: isExpanded ? '8px 8px 0 0' : 8,
                  backgroundColor: '#fff', display: 'flex', alignItems: 'center', gap: '0.75rem',
                  cursor: 'pointer',
                }}
              >
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 600, color: '#111', fontSize: '0.85rem', display: 'flex', alignItems: 'center', gap: 6 }}>
                    {m.full_name || m.email}
                    {m.is_active === false && (
                      <span style={{ fontSize: '0.6rem', fontWeight: 600, padding: '1px 6px', borderRadius: 10, backgroundColor: '#fff3cd', color: '#856404' }}>Pending</span>
                    )}
                  </div>
                  <div style={{ fontSize: '0.72rem', color: '#999' }}>{m.email}</div>
                </div>
                {m.is_active === false && (() => {
                  const status = resendStatus[m.email];
                  if (status === 'sending') return <span style={{ fontSize: '0.68rem', color: '#999' }}>Sending...</span>;
                  if (status === 'sent') return <span style={{ fontSize: '0.68rem', color: '#28a745', fontWeight: 600 }}>Sent!</span>;
                  if (status === 'failed') return <span style={{ fontSize: '0.68rem', color: '#dc3545', fontWeight: 600 }}>Failed</span>;
                  return (
                    <button onClick={e => { e.stopPropagation(); handleResendInvite(m.email); }} style={{
                      padding: '3px 10px', fontSize: '0.68rem', fontWeight: 500,
                      backgroundColor: '#fff', color: '#c4a882', border: '1px solid #c4a882', borderRadius: 6,
                      cursor: 'pointer', fontFamily: 'inherit',
                    }}>Resend Invite</button>
                  );
                })()}
                {(() => {
                  const u = usage[m.user_id];
                  if (!u) return null;
                  const total = (u.input_tokens || 0) + (u.output_tokens || 0);
                  return (
                    <span style={{ fontSize: '0.68rem', color: '#999' }}>
                      {total >= 1000000 ? `${(total / 1000000).toFixed(1)}M` : total >= 1000 ? `${(total / 1000).toFixed(1)}K` : total} tokens
                    </span>
                  );
                })()}
                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }} onClick={e => e.stopPropagation()}>
                  {availableRoles.length > 0 ? (
                    <select
                      value={m.role_id || ''}
                      onChange={e => handleRoleChange(m.user_id, e.target.value)}
                      style={{
                        fontSize: '0.7rem', fontWeight: 600, padding: '2px 6px', borderRadius: 6,
                        border: '1px solid #e5e7eb', backgroundColor: '#f9fafb', cursor: 'pointer',
                        fontFamily: 'inherit', color: '#374151',
                      }}
                    >
                      {!m.role_id && <option value="">— Unassigned —</option>}
                      {availableRoles.map(r => (
                        <option key={r.id} value={r.id}>{r.display_name}</option>
                      ))}
                    </select>
                  ) : (
                    <span style={{
                      fontSize: '0.65rem', fontWeight: 600, padding: '2px 8px', borderRadius: 10,
                      backgroundColor: m.role_name === 'owner' ? '#dbeafe' : m.role_name === 'manager' ? '#fef3c7' : '#f3f4f6',
                      color: m.role_name === 'owner' ? '#1e40af' : m.role_name === 'manager' ? '#92400e' : '#6b7280',
                    }}>{m.role_display_name || m.role}</span>
                  )}
                  <button
                    onClick={() => setViewingRoleId(viewingRoleId === (m.role_id || '') ? null : (m.role_id || ''))}
                    title="View role permissions"
                    style={{
                      border: 'none', background: 'none', cursor: 'pointer',
                      fontSize: '0.7rem', color: '#999', padding: '2px 4px', borderRadius: 4,
                    }}
                  >ℹ</button>
                </div>
                <span style={{
                  fontSize: '0.6rem', color: '#bbb',
                  transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)',
                  transition: 'transform 0.15s',
                }}>&#9654;</span>
              </div>
              {isExpanded && (
                <div style={{
                  padding: '0.75rem 1rem', border: '1px solid #e5e7eb', borderTop: 'none',
                  borderRadius: '0 0 8px 8px', backgroundColor: '#fafafa',
                }}>
                  {/* Usage summary */}
                  {(() => {
                    const u = usage[m.user_id];
                    if (!u) return <div style={{ fontSize: '0.75rem', color: '#999', marginBottom: '0.5rem' }}>No usage this month</div>;
                    return (
                      <div style={{ display: 'flex', gap: '1.5rem', marginBottom: '0.75rem', fontSize: '0.75rem' }}>
                        <div>
                          <div style={{ fontSize: '0.65rem', fontWeight: 600, color: '#9ca3af', textTransform: 'uppercase' }}>Input</div>
                          <div style={{ fontWeight: 600, color: '#111' }}>{((u.input_tokens || 0) / 1000).toFixed(1)}K</div>
                        </div>
                        <div>
                          <div style={{ fontSize: '0.65rem', fontWeight: 600, color: '#9ca3af', textTransform: 'uppercase' }}>Output</div>
                          <div style={{ fontWeight: 600, color: '#111' }}>{((u.output_tokens || 0) / 1000).toFixed(1)}K</div>
                        </div>
                        <div>
                          <div style={{ fontSize: '0.65rem', fontWeight: 600, color: '#9ca3af', textTransform: 'uppercase' }}>Calls</div>
                          <div style={{ fontWeight: 600, color: '#111' }}>{u.llm_call_count || 0}</div>
                        </div>
                      </div>
                    );
                  })()}

                  {/* Daily usage chart */}
                  {(() => {
                    const days = memberDailyUsage[m.user_id] || {};
                    const entries = Object.entries(days).sort(([a], [b]) => a.localeCompare(b));
                    if (entries.length === 0) return null;
                    const maxTokens = Math.max(...entries.map(([, d]) => (d.input_tokens || 0) + (d.output_tokens || 0)), 1);
                    return (
                      <div style={{ marginBottom: '0.75rem' }}>
                        <div style={{ fontSize: '0.65rem', fontWeight: 600, color: '#9ca3af', textTransform: 'uppercase', marginBottom: '0.4rem' }}>Daily Usage</div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                          {entries.map(([date, d]) => {
                            const total = (d.input_tokens || 0) + (d.output_tokens || 0);
                            const inputPct = (d.input_tokens || 0) / maxTokens * 100;
                            const outputPct = (d.output_tokens || 0) / maxTokens * 100;
                            const dayLabel = new Date(date + 'T00:00:00').toLocaleDateString('en-NZ', { weekday: 'short', day: 'numeric' });
                            return (
                              <div key={date} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                                <span style={{ fontSize: '0.65rem', color: '#999', width: 50, textAlign: 'right', flexShrink: 0 }}>{dayLabel}</span>
                                <div style={{ flex: 1, height: 12, backgroundColor: '#f3f4f6', borderRadius: 2, overflow: 'hidden', display: 'flex' }}>
                                  <div style={{ width: `${inputPct}%`, backgroundColor: '#93c5fd', height: '100%' }} />
                                  <div style={{ width: `${outputPct}%`, backgroundColor: '#6366f1', height: '100%' }} />
                                </div>
                                <span style={{ fontSize: '0.6rem', color: '#bbb', width: 40, flexShrink: 0 }}>
                                  {total >= 1000 ? `${(total / 1000).toFixed(1)}K` : total}
                                </span>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    );
                  })()}

                  {/* Venues */}
                  <div style={{ fontSize: '0.65rem', fontWeight: 600, color: '#9ca3af', textTransform: 'uppercase', marginBottom: '0.25rem' }}>Venue Access</div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '0.75rem' }}>
                    {venues.map(v => (
                      <label key={v.id} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: '0.78rem', color: '#555', cursor: 'pointer' }}>
                        <input type="checkbox" checked={userVenues.includes(v.id)}
                          onChange={e => { e.stopPropagation(); handleToggleVenue(m.user_id, v.id, e.target.checked); }}
                          style={{ accentColor: '#2563eb' }} />
                        {v.name}
                      </label>
                    ))}
                    {venues.length === 0 && <span style={{ color: '#999', fontSize: '0.75rem' }}>No venues created yet</span>}
                  </div>

                  {/* Delete */}
                  <div style={{ borderTop: '1px solid #e5e7eb', paddingTop: '0.5rem' }}>
                    <button onClick={(e) => { e.stopPropagation(); handleRemove(m.user_id); }} style={{
                      padding: '4px 12px', fontSize: '0.72rem', fontWeight: 500,
                      border: '1px solid #fecaca', borderRadius: 6, backgroundColor: '#fff', color: '#dc2626',
                      cursor: 'pointer', fontFamily: 'inherit',
                    }}>Remove from organization</button>
                  </div>
                </div>
              )}
            </div>
          );
        })}
        {members.length === 0 && (
          <div style={{ padding: '2rem', textAlign: 'center', color: '#999', fontSize: '0.82rem' }}>
            No members yet.
          </div>
        )}
      </div>
    </div>
  );
}

type SettingsTab = 'connectors' | 'agents' | 'specs' | 'components' | 'venues' | 'members' | 'billing' | 'email' | 'deployments' | 'tests' | 'roles' | 'secrets';

function hasSettingsPermission(user: User | null, ...perms: string[]): boolean {
  if (!user) return false;
  if (user.role === 'admin') return true;
  return perms.some(p => user.permissions?.includes(p));
}

export default function SettingsPanel() {
  const [activeTab, setActiveTab] = useState<SettingsTab>('venues');
  const [orgId, setOrgId] = useState<string | null>(null);
  const storedUser = getStoredUser() as User | null;
  const isAdmin = storedUser?.role === 'admin';

  const showConnectors = isAdmin;
  const showAgents = isAdmin;
  const showSpecs = isAdmin;
  const showDeployments = isAdmin;
  const showTests = isAdmin;
  const showRoles = hasSettingsPermission(storedUser, 'org:roles', 'org:members');
  const showComponents = isAdmin;
  const showSecrets = isAdmin;

  // Fetch org ID for billing tab
  useEffect(() => {
    apiFetch('/api/organizations').then(r => r.json()).then(d => {
      const orgs = d.organizations || [];
      if (orgs.length > 0) setOrgId(orgs[0].id);
    }).catch(() => {});
  }, []);

  // --- Connector state ---
  const [connectors, setConnectors] = useState<ConnectorMeta[]>([]);
  const [forms, setForms] = useState<Record<string, Record<string, string>>>({});
  const [testStatus, setTestStatus] = useState<Record<string, TestStatus>>({});
  const [testMessage, setTestMessage] = useState<Record<string, string>>({});
  const [testDetail, setTestDetail] = useState<Record<string, { rendered_request?: Record<string, unknown>; response?: Record<string, unknown> } | null>>({});
  const [saving, setSaving] = useState<Record<string, boolean>>({});

  // --- Agent state ---
  const [agents, setAgents] = useState<AgentConfig[]>([]);
  const [agentForms, setAgentForms] = useState<Record<string, { system_prompt: string; description: string }>>({});
  const [agentSaving, setAgentSaving] = useState<Record<string, boolean>>({});

  const fetchConnectors = useCallback(async () => {
    try {
      // Only fetch platform/global connectors (venue_id=NULL)
      const res = await apiFetch('/api/connectors');
      if (!res.ok) return;
      const data = await res.json();
      // Filter to only show non-spec-driven (platform) connectors
      const platformOnly = (data.connectors || []).filter((c: ConnectorMeta) => !c.spec_driven);
      setConnectors(platformOnly);
      const initialForms: Record<string, Record<string, string>> = {};
      for (const c of platformOnly) {
        const form: Record<string, string> = { ...c.config };
        // Populate defaults for select fields when not already saved
        for (const f of c.fields) {
          if (f.type === 'select' && f.default && !form[f.key]) {
            form[f.key] = f.default;
          }
        }
        initialForms[c.name] = form;
      }
      setForms(initialForms);
    } catch (e) { console.error(e); }
  }, []);

  const fetchAgents = useCallback(async () => {
    try {
      const res = await apiFetch('/api/agents');
      if (!res.ok) return;
      const data = await res.json();
      setAgents(data.agents);
      const initialForms: Record<string, { system_prompt: string; description: string }> = {};
      for (const a of data.agents) {
        initialForms[a.slug] = {
          system_prompt: a.system_prompt || '',
          description: a.description || '',
        };
      }
      setAgentForms(initialForms);
    } catch (e) { console.error(e); }
  }, []);

  useEffect(() => { fetchConnectors(); }, [fetchConnectors]);
  useEffect(() => { if (activeTab === 'agents') fetchAgents(); }, [activeTab, fetchAgents]);

  // --- Connector handlers ---
  const updateField = (connector: string, key: string, value: string) => {
    setForms(prev => ({
      ...prev,
      [connector]: { ...prev[connector], [key]: value },
    }));
  };

  const handleTest = async (name: string) => {
    setTestStatus(prev => ({ ...prev, [name]: 'testing' }));
    setTestMessage(prev => ({ ...prev, [name]: '' }));
    setTestDetail(prev => ({ ...prev, [name]: null }));
    try {
      const res = await apiFetch(`/api/connectors/${name}/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config: forms[name] || {} }),
      });
      const data = await res.json();
      const detail = { rendered_request: data.rendered_request, response: data.response };
      if (data.success) {
        setTestStatus(prev => ({ ...prev, [name]: 'success' }));
        setTestMessage(prev => ({ ...prev, [name]: data.message || 'Connected' }));
      } else {
        setTestStatus(prev => ({ ...prev, [name]: 'error' }));
        setTestMessage(prev => ({ ...prev, [name]: data.error || 'Test failed' }));
      }
      setTestDetail(prev => ({ ...prev, [name]: detail }));
    } catch (e) {
      console.error(e);
      setTestStatus(prev => ({ ...prev, [name]: 'error' }));
      setTestMessage(prev => ({ ...prev, [name]: 'Network error' }));
    }
  };

  const handleSave = async (name: string) => {
    setSaving(prev => ({ ...prev, [name]: true }));
    try {
      const res = await apiFetch(`/api/connectors/${name}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config: forms[name] || {}, enabled: true }),
      });
      if (res.ok) {
        await fetchConnectors();
      }
    } catch (e) { console.error(e); } finally {
      setSaving(prev => ({ ...prev, [name]: false }));
    }
  };

  const handleToggleEnabled = async (name: string) => {
    try {
      const res = await apiFetch(`/api/connectors/${name}/toggle`, { method: 'PATCH' });
      if (res.ok) {
        await fetchConnectors();
      }
    } catch (e) { console.error(e); }
  };

  const handleDelete = async (name: string) => {
    try {
      await apiFetch(`/api/connectors/${name}`, { method: 'DELETE' });
      await fetchConnectors();
      setTestStatus(prev => ({ ...prev, [name]: 'idle' }));
      setTestMessage(prev => ({ ...prev, [name]: '' }));
    } catch (e) { console.error(e); }
  };

  const handleOAuthConnect = async (name: string) => {
    try {
      const res = await apiFetch(`/api/oauth/authorize/${name}`);
      if (!res.ok) {
        let errMsg: string;
        try {
          const errJson = await res.json();
          errMsg = errJson.detail || JSON.stringify(errJson);
        } catch (e) { console.error(e); errMsg = await res.text(); }
        setTestStatus(prev => ({ ...prev, [name]: 'error' }));
        setTestMessage(prev => ({ ...prev, [name]: `OAuth error: ${errMsg}` }));
        return;
      }
      const data = await res.json();
      // Open the authorize URL in a popup
      const popup = window.open(data.authorize_url, `oauth_${name}`, 'width=600,height=700');
      // Listen for the callback message from the popup
      const handler = (event: MessageEvent) => {
        if (event.data?.type === 'oauth-complete') {
          window.removeEventListener('message', handler);
          fetchConnectors();
          if (event.data.success) {
            setTestStatus(prev => ({ ...prev, [name]: 'success' }));
            setTestMessage(prev => ({ ...prev, [name]: 'OAuth connected successfully' }));
          } else {
            setTestStatus(prev => ({ ...prev, [name]: 'error' }));
            setTestMessage(prev => ({ ...prev, [name]: 'OAuth connection failed' }));
          }
        }
      };
      window.addEventListener('message', handler);
      // Clean up listener after 5 minutes if popup closes without completing
      setTimeout(() => {
        window.removeEventListener('message', handler);
        if (popup && popup.closed) fetchConnectors();
      }, 300000);
    } catch (e) {
      console.error(e);
      setTestStatus(prev => ({ ...prev, [name]: 'error' }));
      setTestMessage(prev => ({ ...prev, [name]: 'Failed to start OAuth flow' }));
    }
  };

  const handleOAuthDisconnect = async (name: string) => {
    try {
      await apiFetch(`/api/oauth/disconnect/${name}`, { method: 'POST' });
      await fetchConnectors();
      setTestStatus(prev => ({ ...prev, [name]: 'idle' }));
      setTestMessage(prev => ({ ...prev, [name]: '' }));
    } catch (e) { console.error(e); }
  };

  // --- Agent handlers ---
  const updateAgentField = (slug: string, key: 'system_prompt' | 'description', value: string) => {
    setAgentForms(prev => ({
      ...prev,
      [slug]: { ...prev[slug], [key]: value },
    }));
  };

  const handleAgentSave = async (slug: string) => {
    setAgentSaving(prev => ({ ...prev, [slug]: true }));
    try {
      const form = agentForms[slug];
      const res = await apiFetch(`/api/agents/${slug}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          system_prompt: form?.system_prompt || null,
          description: form?.description || null,
        }),
      });
      if (res.ok) {
        await fetchAgents();
      }
    } catch (e) { console.error(e); } finally {
      setAgentSaving(prev => ({ ...prev, [slug]: false }));
    }
  };

  const handleAgentReset = async (slug: string) => {
    try {
      const res = await apiFetch(`/api/agents/${slug}/reset-prompt`, { method: 'POST' });
      if (res.ok) {
        await fetchAgents();
      }
    } catch (e) { console.error(e); }
  };

  const handleToggleCapability = async (slug: string, binding: AgentBinding, capIndex: number) => {
    const updated = binding.capabilities.map((c, i) =>
      i === capIndex ? { ...c, enabled: !c.enabled } : c
    );
    try {
      await apiFetch(`/api/agents/${slug}/bindings/${binding.connector_name}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ capabilities: updated, enabled: binding.enabled }),
      });
      await fetchAgents();
    } catch (e) { console.error(e); }
  };

  const handleDeleteBinding = async (slug: string, connectorName: string) => {
    try {
      await apiFetch(`/api/agents/${slug}/bindings/${connectorName}`, { method: 'DELETE' });
      await fetchAgents();
    } catch (e) { console.error(e); }
  };

  const handleAddConnector = async (slug: string, connectorName: string) => {
    try {
      await apiFetch(`/api/agents/${slug}/bindings/${connectorName}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ capabilities: [], enabled: true }),
      });
      await fetchAgents();
    } catch (e) { console.error(e); }
  };

  const statusColor = (s: TestStatus) => {
    switch (s) {
      case 'testing': return '#c4a882';
      case 'success': return '#38a169';
      case 'error': return '#e53e3e';
      default: return '#999';
    }
  };

  const tabStyle = (tab: SettingsTab): React.CSSProperties => ({
    padding: '6px 16px',
    fontSize: '0.82rem',
    fontWeight: 500,
    border: 'none',
    borderBottom: activeTab === tab ? '2px solid #c4a882' : '2px solid transparent',
    backgroundColor: 'transparent',
    color: activeTab === tab ? '#c4a882' : '#666',
    cursor: 'pointer',
    fontFamily: 'inherit',
  });

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      <div style={{
        padding: '1.25rem 1.5rem',
        borderBottom: '1px solid #eee',
        display: 'flex',
        alignItems: 'center',
        gap: 8,
      }}>
        <span style={{ fontSize: '1.1rem' }}>&#9881;</span>
        <h2 style={{ margin: 0, fontSize: '1rem', fontWeight: 600 }}>Settings</h2>
      </div>

      {/* Tab bar */}
      <div style={{ display: 'flex', gap: 4, padding: '0 1.5rem', borderBottom: '1px solid #eee', overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
        <button data-testid="settings-tab-venues" onClick={() => setActiveTab('venues')} style={tabStyle('venues')}>Venues</button>
        <button data-testid="settings-tab-members" onClick={() => setActiveTab('members')} style={tabStyle('members')}>Users</button>
        <button data-testid="settings-tab-billing" onClick={() => setActiveTab('billing')} style={tabStyle('billing')}>Billing</button>
        <button onClick={() => setActiveTab('email')} style={tabStyle('email')}>Email</button>
        {showConnectors && <button data-testid="settings-tab-connectors" onClick={() => setActiveTab('connectors')} style={tabStyle('connectors')}>Connectors</button>}
        {showAgents && <button data-testid="settings-tab-agents" onClick={() => setActiveTab('agents')} style={tabStyle('agents')}>Agents</button>}
        {showSpecs && <button data-testid="settings-tab-specs" onClick={() => setActiveTab('specs')} style={tabStyle('specs')}>Connector Specs</button>}
        {showComponents && <button data-testid="settings-tab-components" onClick={() => setActiveTab('components')} style={tabStyle('components')}>Components</button>}
        {showDeployments && <button data-testid="settings-tab-deployments" onClick={() => setActiveTab('deployments')} style={tabStyle('deployments')}>Deployments</button>}
        {showTests && <button data-testid="settings-tab-tests" onClick={() => setActiveTab('tests')} style={tabStyle('tests')}>Tests</button>}
        {showRoles && <button data-testid="settings-tab-roles" onClick={() => setActiveTab('roles')} style={tabStyle('roles')}>Roles</button>}
        {showSecrets && <button data-testid="settings-tab-secrets" onClick={() => setActiveTab('secrets')} style={tabStyle('secrets')}>Secrets</button>}
      </div>

      <div style={{ flex: 1, overflow: 'auto', padding: '1.5rem' }}>
        {/* ============ VENUES TAB ============ */}
        {activeTab === 'venues' && <VenuesTab />}

        {/* ============ MEMBERS TAB ============ */}
        {activeTab === 'members' && <UsersTab />}

        {/* ============ CONNECTORS TAB ============ */}
        {activeTab === 'connectors' && (
          <>
            <h3 style={{ margin: '0 0 1rem', fontSize: '0.85rem', fontWeight: 600, color: '#666', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Platform Connectors
            </h3>
            <div style={{
              display: 'flex', alignItems: 'center', gap: '0.5rem',
              marginBottom: '1rem', padding: '0.6rem 0.75rem',
              border: '1px solid #edf2f7', borderRadius: 8, backgroundColor: '#fafafa',
            }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.82rem', color: '#555', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={(() => { try { return localStorage.getItem('norm_show_tool_details') !== 'false'; } catch { return true; } })()}
                  onChange={e => {
                    localStorage.setItem('norm_show_tool_details', String(e.target.checked));
                    // Force re-render
                    setConnectors(c => [...c]);
                  }}
                  style={{ cursor: 'pointer' }}
                />
                Show tool call details in conversations
              </label>
              <span style={{ fontSize: '0.72rem', color: '#999' }}>
                Toggle request/response cards in the chat view
              </span>
            </div>

            {connectors.map(c => {
              const status = testStatus[c.name] || 'idle';
              return (
                <div key={c.name} style={{
                  border: '1px solid #e2e8f0',
                  borderRadius: 10,
                  padding: '1.25rem',
                  marginBottom: '1rem',
                  backgroundColor: '#fff',
                  opacity: c.configured && !c.enabled ? 0.6 : 1,
                  transition: 'opacity 0.2s',
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontWeight: 600, fontSize: '0.95rem' }}>{c.label}</span>
                      {c.configured && (
                        <span style={{
                          fontSize: '0.7rem',
                          backgroundColor: '#e6fffa',
                          color: '#234e52',
                          padding: '2px 8px',
                          borderRadius: 10,
                          fontWeight: 500,
                        }}>
                          Configured
                        </span>
                      )}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      {c.configured && (
                        <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: '0.78rem', color: '#555' }}>
                          <div
                            onClick={() => handleToggleEnabled(c.name)}
                            style={{
                              width: 34,
                              height: 18,
                              borderRadius: 9,
                              backgroundColor: c.enabled ? '#38a169' : '#cbd5e0',
                              position: 'relative',
                              cursor: 'pointer',
                              transition: 'background-color 0.2s',
                            }}
                          >
                            <div style={{
                              width: 14,
                              height: 14,
                              borderRadius: '50%',
                              backgroundColor: '#fff',
                              position: 'absolute',
                              top: 2,
                              left: c.enabled ? 18 : 2,
                              transition: 'left 0.2s',
                              boxShadow: '0 1px 2px rgba(0,0,0,0.2)',
                            }} />
                          </div>
                          {c.enabled ? 'Active' : 'Inactive'}
                        </label>
                      )}
                      <span style={{ fontSize: '0.75rem', color: '#999' }}>{c.domain}</span>
                    </div>
                  </div>

                  {/* OAuth2 connectors: show Connect button instead of manual fields */}
                  {c.auth_type === 'oauth2' ? (
                    <>
                      {c.oauth_connected && (
                        <div style={{
                          fontSize: '0.78rem',
                          color: '#38a169',
                          marginBottom: '0.75rem',
                          display: 'flex',
                          alignItems: 'center',
                          gap: 6,
                        }}>
                          <span style={{
                            width: 8,
                            height: 8,
                            borderRadius: '50%',
                            backgroundColor: '#38a169',
                            display: 'inline-block',
                          }} />
                          OAuth connected
                        </div>
                      )}

                      {/* Still show non-secret credential fields (e.g. subdomain) */}
                      {c.fields.filter(f => !f.secret).map(f => (
                        <div key={f.key} style={{ marginBottom: '0.75rem' }}>
                          <label style={{ display: 'block', fontSize: '0.78rem', fontWeight: 500, color: '#555', marginBottom: 4 }}>
                            {f.label}
                          </label>
                          <input
                            type="text"
                            value={forms[c.name]?.[f.key] || ''}
                            onChange={e => updateField(c.name, f.key, e.target.value)}
                            placeholder={`Enter ${f.label.toLowerCase()}`}
                            style={{
                              width: '100%',
                              padding: '8px 10px',
                              border: '1px solid #ddd',
                              borderRadius: 6,
                              fontSize: '0.85rem',
                              fontFamily: 'inherit',
                              boxSizing: 'border-box',
                              outline: 'none',
                            }}
                          />
                        </div>
                      ))}
                    </>
                  ) : (
                    <>
                      {c.fields.map(f => (
                        <div key={f.key} style={{ marginBottom: '0.75rem' }}>
                          <label style={{ display: 'block', fontSize: '0.78rem', fontWeight: 500, color: '#555', marginBottom: 4 }}>
                            {f.label}
                          </label>
                          {f.type === 'select' && f.options ? (
                            <select
                              value={forms[c.name]?.[f.key] || f.default || ''}
                              onChange={e => updateField(c.name, f.key, e.target.value)}
                              style={{
                                width: '100%',
                                padding: '8px 10px',
                                border: '1px solid #ddd',
                                borderRadius: 6,
                                fontSize: '0.85rem',
                                fontFamily: 'inherit',
                                boxSizing: 'border-box',
                                outline: 'none',
                                backgroundColor: '#fff',
                                cursor: 'pointer',
                              }}
                            >
                              {f.options.map(opt => (
                                <option key={opt.id} value={opt.id}>{opt.label}</option>
                              ))}
                            </select>
                          ) : (
                            <input
                              type={f.secret ? 'password' : 'text'}
                              value={forms[c.name]?.[f.key] || ''}
                              onChange={e => updateField(c.name, f.key, e.target.value)}
                              placeholder={f.secret ? '••••••••' : `Enter ${f.label.toLowerCase()}`}
                              style={{
                                width: '100%',
                                padding: '8px 10px',
                                border: '1px solid #ddd',
                                borderRadius: 6,
                                fontSize: '0.85rem',
                                fontFamily: 'inherit',
                                boxSizing: 'border-box',
                                outline: 'none',
                              }}
                            />
                          )}
                        </div>
                      ))}
                    </>
                  )}

                  {status !== 'idle' && (
                    <div style={{
                      fontSize: '0.78rem',
                      color: statusColor(status),
                      marginBottom: '0.75rem',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 6,
                    }}>
                      <span style={{
                        width: 8,
                        height: 8,
                        borderRadius: '50%',
                        backgroundColor: statusColor(status),
                        display: 'inline-block',
                      }} />
                      {status === 'testing' ? 'Testing connection...' : testMessage[c.name]}
                    </div>
                  )}

                  {testDetail[c.name] && (status === 'success' || status === 'error') && (
                    <details style={{ marginBottom: '0.75rem', fontSize: '0.78rem' }}>
                      <summary style={{ cursor: 'pointer', color: '#666', marginBottom: '0.4rem' }}>
                        Show request &amp; response
                      </summary>
                      {testDetail[c.name]?.rendered_request && (
                        <div style={{ marginBottom: '0.4rem' }}>
                          <div style={{ fontSize: '0.72rem', fontWeight: 600, color: '#888', textTransform: 'uppercase', letterSpacing: '0.03em', marginBottom: 3 }}>Request</div>
                          <pre style={{
                            padding: '0.5rem', backgroundColor: '#1a202c', color: '#e2e8f0',
                            borderRadius: 6, fontSize: '0.75rem', overflow: 'auto', lineHeight: 1.4, margin: 0, maxHeight: 200,
                          }}>
                            {JSON.stringify(testDetail[c.name]?.rendered_request, null, 2)}
                          </pre>
                        </div>
                      )}
                      {testDetail[c.name]?.response && (
                        <div>
                          <div style={{ fontSize: '0.72rem', fontWeight: 600, color: '#888', textTransform: 'uppercase', letterSpacing: '0.03em', marginBottom: 3 }}>Response</div>
                          <pre style={{
                            padding: '0.5rem', backgroundColor: '#1a202c', color: '#e2e8f0',
                            borderRadius: 6, fontSize: '0.75rem', overflow: 'auto', lineHeight: 1.4, margin: 0, maxHeight: 200,
                          }}>
                            {JSON.stringify(testDetail[c.name]?.response, null, 2)}
                          </pre>
                        </div>
                      )}
                    </details>
                  )}

                  <div style={{ display: 'flex', gap: 8 }}>
                    {c.auth_type === 'oauth2' ? (
                      <>
                        {!c.oauth_connected ? (
                          <button
                            onClick={() => handleOAuthConnect(c.name)}
                            style={{
                              padding: '6px 14px',
                              fontSize: '0.8rem',
                              fontWeight: 500,
                              border: 'none',
                              borderRadius: 6,
                              backgroundColor: '#c4a882',
                              color: '#fff',
                              cursor: 'pointer',
                              fontFamily: 'inherit',
                            }}
                          >
                            Connect with OAuth
                          </button>
                        ) : (
                          <button
                            onClick={() => handleOAuthDisconnect(c.name)}
                            style={{
                              padding: '6px 14px',
                              fontSize: '0.8rem',
                              fontWeight: 500,
                              border: '1px solid #e53e3e',
                              borderRadius: 6,
                              backgroundColor: '#fff',
                              color: '#e53e3e',
                              cursor: 'pointer',
                              fontFamily: 'inherit',
                            }}
                          >
                            Disconnect
                          </button>
                        )}
                        {/* Save non-secret fields if any exist */}
                        {c.fields.some(f => !f.secret) && (
                          <button
                            onClick={() => handleSave(c.name)}
                            disabled={saving[c.name]}
                            style={{
                              padding: '6px 14px',
                              fontSize: '0.8rem',
                              fontWeight: 500,
                              border: '1px solid #ddd',
                              borderRadius: 6,
                              backgroundColor: '#fff',
                              cursor: saving[c.name] ? 'not-allowed' : 'pointer',
                              fontFamily: 'inherit',
                            }}
                          >
                            {saving[c.name] ? 'Saving...' : 'Save'}
                          </button>
                        )}
                      </>
                    ) : (
                      <>
                        <button
                          onClick={() => handleTest(c.name)}
                          disabled={status === 'testing'}
                          style={{
                            padding: '6px 14px',
                            fontSize: '0.8rem',
                            fontWeight: 500,
                            border: '1px solid #ddd',
                            borderRadius: 6,
                            backgroundColor: '#fff',
                            cursor: status === 'testing' ? 'not-allowed' : 'pointer',
                            fontFamily: 'inherit',
                          }}
                        >
                          Test
                        </button>
                        <button
                          onClick={() => handleSave(c.name)}
                          disabled={saving[c.name]}
                          style={{
                            padding: '6px 14px',
                            fontSize: '0.8rem',
                            fontWeight: 500,
                            border: 'none',
                            borderRadius: 6,
                            backgroundColor: '#c4a882',
                            color: '#fff',
                            cursor: saving[c.name] ? 'not-allowed' : 'pointer',
                            fontFamily: 'inherit',
                          }}
                        >
                          {saving[c.name] ? 'Saving...' : 'Save'}
                        </button>
                        {c.configured && (
                          <button
                            onClick={() => handleDelete(c.name)}
                            style={{
                              padding: '6px 14px',
                              fontSize: '0.8rem',
                              fontWeight: 500,
                              border: '1px solid #e53e3e',
                              borderRadius: 6,
                              backgroundColor: '#fff',
                              color: '#e53e3e',
                              cursor: 'pointer',
                              fontFamily: 'inherit',
                            }}
                          >
                            Remove
                          </button>
                        )}
                      </>
                    )}
                  </div>
                </div>
              );
            })}
          </>
        )}

        {/* ============ CONNECTOR SPECS TAB ============ */}
        {activeTab === 'specs' && <ConnectorSpecsPanel />}

        {/* ============ AGENTS TAB ============ */}
        {activeTab === 'agents' && (
          <>
            <h3 style={{ margin: '0 0 1rem', fontSize: '0.85rem', fontWeight: 600, color: '#666', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Agent Configuration
            </h3>

            {agents.map(agent => (
              <div key={agent.slug} style={{
                border: '1px solid #e2e8f0',
                borderRadius: 10,
                padding: '1.25rem',
                marginBottom: '1rem',
                backgroundColor: '#fff',
              }}>
                {/* Header */}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontWeight: 600, fontSize: '0.95rem' }}>{agent.display_name}</span>
                    <span style={{ fontSize: '0.75rem', color: '#999' }}>{agent.slug}</span>
                    {!agent.has_prompt && (
                      <span style={{
                        fontSize: '0.7rem',
                        backgroundColor: '#fee2e2',
                        color: '#991b1b',
                        padding: '2px 8px',
                        borderRadius: 10,
                        fontWeight: 500,
                      }}>
                        No prompt
                      </span>
                    )}
                  </div>
                </div>

                {/* Description */}
                <div style={{ marginBottom: '0.75rem' }}>
                  <label style={{ display: 'block', fontSize: '0.78rem', fontWeight: 500, color: '#555', marginBottom: 4 }}>
                    Description
                  </label>
                  <input
                    type="text"
                    value={agentForms[agent.slug]?.description || ''}
                    onChange={e => updateAgentField(agent.slug, 'description', e.target.value)}
                    placeholder="What this agent does..."
                    style={{
                      width: '100%',
                      padding: '8px 10px',
                      border: '1px solid #ddd',
                      borderRadius: 6,
                      fontSize: '0.85rem',
                      fontFamily: 'inherit',
                      boxSizing: 'border-box',
                      outline: 'none',
                    }}
                  />
                </div>

                {/* System Prompt */}
                <div style={{ marginBottom: '0.75rem' }}>
                  <label style={{ display: 'block', fontSize: '0.78rem', fontWeight: 500, color: '#555', marginBottom: 4 }}>
                    System Prompt
                  </label>
                  <textarea
                    value={agentForms[agent.slug]?.system_prompt || ''}
                    onChange={e => updateAgentField(agent.slug, 'system_prompt', e.target.value)}
                    rows={15}
                    style={{
                      width: '100%',
                      padding: '8px 10px',
                      border: '1px solid #ddd',
                      borderRadius: 6,
                      fontSize: '0.82rem',
                      fontFamily: 'monospace',
                      boxSizing: 'border-box',
                      outline: 'none',
                      resize: 'vertical',
                      lineHeight: 1.5,
                    }}
                  />
                </div>

                {/* Connector Bindings */}
                {agent.bindings.length > 0 && (
                  <div style={{ marginBottom: '0.75rem' }}>
                    <label style={{ display: 'block', fontSize: '0.78rem', fontWeight: 500, color: '#555', marginBottom: 8 }}>
                      Connector Bindings
                    </label>
                    {agent.bindings.map(binding => (
                      <div key={binding.connector_name} style={{
                        border: '1px solid #edf2f7',
                        borderRadius: 8,
                        padding: '0.75rem',
                        marginBottom: '0.5rem',
                        backgroundColor: '#fafafa',
                      }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                          <span style={{ fontWeight: 500, fontSize: '0.85rem' }}>{binding.connector_label}</span>
                          <button
                            onClick={() => handleDeleteBinding(agent.slug, binding.connector_name)}
                            style={{
                              padding: '2px 8px',
                              fontSize: '0.72rem',
                              border: '1px solid #e53e3e',
                              borderRadius: 4,
                              backgroundColor: '#fff',
                              color: '#e53e3e',
                              cursor: 'pointer',
                              fontFamily: 'inherit',
                            }}
                          >
                            Remove
                          </button>
                        </div>
                        {binding.capabilities.map((cap, idx) => (
                          <label key={`${binding.connector_name}__${cap.action}__${idx}`} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.8rem', color: '#444', cursor: 'pointer', marginBottom: 2 }}>
                            <input
                              type="checkbox"
                              checked={cap.enabled}
                              onChange={() => handleToggleCapability(agent.slug, binding, idx)}
                            />
                            {cap.label}
                          </label>
                        ))}
                      </div>
                    ))}
                  </div>
                )}

                {/* Add Connector */}
                {agent.available_connectors && agent.available_connectors.length > 0 && (
                  <div style={{ marginBottom: '0.75rem' }}>
                    <label style={{ display: 'block', fontSize: '0.78rem', fontWeight: 500, color: '#555', marginBottom: 4 }}>
                      Add Connector
                    </label>
                    <select
                      defaultValue=""
                      onChange={e => {
                        if (e.target.value) {
                          handleAddConnector(agent.slug, e.target.value);
                          e.target.value = '';
                        }
                      }}
                      style={{
                        padding: '6px 10px',
                        fontSize: '0.82rem',
                        border: '1px solid #ddd',
                        borderRadius: 6,
                        fontFamily: 'inherit',
                        backgroundColor: '#fff',
                        cursor: 'pointer',
                        outline: 'none',
                      }}
                    >
                      <option value="" disabled>Select a connector...</option>
                      {agent.available_connectors.map(ac => (
                        <option key={ac.connector_name} value={ac.connector_name}>
                          {ac.display_name}
                        </option>
                      ))}
                    </select>
                  </div>
                )}

                {/* Actions */}
                <div style={{ display: 'flex', gap: 8 }}>
                  <button
                    onClick={() => handleAgentSave(agent.slug)}
                    disabled={agentSaving[agent.slug]}
                    style={{
                      padding: '6px 14px',
                      fontSize: '0.8rem',
                      fontWeight: 500,
                      border: 'none',
                      borderRadius: 6,
                      backgroundColor: '#c4a882',
                      color: '#fff',
                      cursor: agentSaving[agent.slug] ? 'not-allowed' : 'pointer',
                      fontFamily: 'inherit',
                    }}
                  >
                    {agentSaving[agent.slug] ? 'Saving...' : 'Save'}
                  </button>
                  {agent.has_prompt && (
                    <button
                      onClick={() => handleAgentReset(agent.slug)}
                      style={{
                        padding: '6px 14px',
                        fontSize: '0.8rem',
                        fontWeight: 500,
                        border: '1px solid #ddd',
                        borderRadius: 6,
                        backgroundColor: '#fff',
                        cursor: 'pointer',
                        fontFamily: 'inherit',
                      }}
                    >
                      Clear Prompt
                    </button>
                  )}
                </div>
              </div>
            ))}
          </>
        )}

        {/* ============ BILLING TAB ============ */}
        {activeTab === 'billing' && orgId && <BillingTab orgId={orgId} />}

        {/* ============ EMAIL TAB ============ */}
        {activeTab === 'email' && <EmailTab />}

        {/* ============ DEPLOYMENTS TAB ============ */}
        {activeTab === 'deployments' && <DeploymentsPanel />}

        {/* ============ TESTS TAB ============ */}
        {activeTab === 'tests' && <TestsPanel />}

        {/* ============ ROLES TAB ============ */}
        {activeTab === 'roles' && orgId && <RolesPanel orgId={orgId} />}

        {/* ============ SECRETS TAB ============ */}
        {activeTab === 'components' && <ComponentsPanel />}
        {activeTab === 'secrets' && <SecretsPanel />}
      </div>
    </div>
  );
}
