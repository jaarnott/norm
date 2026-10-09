'use client';

import { Fragment, useState, useEffect, useCallback, useId, useRef } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { ChevronDown, ChevronRight, Info, Plus } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import type { AgentConfig, AgentBinding, VenueDetail, Organization, OrgMember } from '../../types';
import ConnectorSpecsPanel from './ConnectorSpecsPanel';
import ConsolidatorCoveragePanel from './ConsolidatorCoveragePanel';
import BillingTab from './BillingTab';
import ConnectionsMatrix from './ConnectionsMatrix';
import AppMapPanel from './AppMapPanel';
import EmailTab from './EmailTab';
import DeploymentsPanel from './DeploymentsPanel';
import TestsPanel from './TestsPanel';
import RolesPanel from './RolesPanel';
import SecretsPanel from './SecretsPanel';
import ComponentsPanel from './ComponentsPanel';
import PlaybooksPanel from './PlaybooksPanel';
import SupplierSpecsPanel from './SupplierSpecsPanel';
import TemplatesPanel from './TemplatesPanel';
import AgentsPanel from './AgentsPanel';
import McpPanel from './McpPanel';
import AdminThreadsPanel from './AdminThreadsPanel';
import ApprovalPreferences from './ApprovalPreferences';
import MemoryTab from './MemoryTab';
import AddressSearch from './AddressSearch';
import { getStoredUser } from '../../lib/api';
// can(user, scope): what the signed-in user may do — shared with the tabs.
import { can } from '../../lib/permissions';
import type { User } from '../../types';
import { useBreakpoint } from '../../hooks/useBreakpoint';
import PageHeader from '../ui/PageHeader';
import Tabs, { type TabEntry } from '../ui/Tabs';
import Button from '../ui/Button';
import IconButton from '../ui/IconButton';
import Badge from '../ui/Badge';
import Avatar from '../ui/Avatar';
import PageState from '../ui/PageState';
import Dialog, { ConfirmDialog } from '../ui/Dialog';
import SubNav, { type SubNavGroup } from '../ui/SubNav';

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
  needs_reconnect?: boolean;
  last_auth_error?: string | null;
  spec_driven?: boolean;
}

type TestStatus = 'idle' | 'testing' | 'success' | 'error';

// --- Shared presentation ---

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

/** Text for screen readers only (an icon column's header). */
const srOnly: CSSProperties = { position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)', whiteSpace: 'nowrap' };

/** AddressSearch takes a style, not a class: the .n-input look, inline. */
const addressInputStyle: CSSProperties = {
  width: '100%', minHeight: 34, padding: '6px 10px', boxSizing: 'border-box',
  border: '1px solid var(--field)', borderRadius: 'var(--radius)', backgroundColor: 'var(--bg)',
  color: 'var(--text)', fontFamily: 'inherit', fontSize: 'var(--fs-base)', lineHeight: 1.45,
};

/** An open row is selected, joined to its details band below (as on Orders). */
const expandedCell: CSSProperties = { backgroundColor: 'var(--selected)', borderBottom: 'none' };

// --- Venues Tab ---

interface VenueConnector {
  name: string;
  label: string;
  auth_type: string;
  configured: boolean;
  enabled: boolean;
  oauth_connected?: boolean;
  needs_reconnect?: boolean;
  last_auth_error?: string | null;
  spec_driven?: boolean;
  fields?: { key: string; label: string; secret: boolean }[];
  config?: Record<string, string>;
}

/**
 * One venue: a table row (or, on phones, a list row) that opens to its
 * settings, its connections and the delete button. `isLast` rounds the
 * bottom corners: the card can't clip them, because the address
 * suggestions have to hang out of it.
 *
 * `canManage` (org:venues) shows Edit and Delete; `canConnect`
 * (settings:connectors) the connection buttons. Without them the row is
 * read-only rather than offering buttons the API would refuse.
 */
function VenueCard({ venue, onDelete, onUpdate, compact, isLast, canManage, canConnect }: {
  venue: VenueDetail; onDelete: () => void; onUpdate: () => void; compact: boolean; isLast: boolean;
  canManage: boolean; canConnect: boolean;
}) {
  const fieldId = useId();
  const [expanded, setExpanded] = useState(false);
  const [connectors, setConnectors] = useState<VenueConnector[]>([]);
  const [connectorForms, setConnectorForms] = useState<Record<string, Record<string, string>>>({});
  const [savingConnector, setSavingConnector] = useState<string | null>(null);
  const [loadingConnectors, setLoadingConnectors] = useState(false);
  const [editingConnector, setEditingConnector] = useState<string | null>(null);
  const [editingVenue, setEditingVenue] = useState(false);
  const [venueForm, setVenueForm] = useState({ location: venue.location || '', timezone: venue.timezone || '', day_start_time: venue.day_start_time || '' });
  const [savingVenue, setSavingVenue] = useState(false);

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

  const connectorCount = venue.connector_count || 0;

  // Opens and closes the row: a real button, so it works from the keyboard
  // (its click reaches the row's handler).
  const chevron = (
    <IconButton
      icon={expanded ? ChevronDown : ChevronRight}
      iconSize={16}
      aria-expanded={expanded}
      label={`${expanded ? 'Hide' : 'Show'} details for ${venue.name}`}
    />
  );

  const details = (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Venue settings */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 8 }}>
          <span className="n-eyebrow">Venue settings</span>
          {canManage && !editingVenue && (
            <Button size="sm" onClick={() => { setVenueForm({ location: venue.location || '', timezone: venue.timezone || '', day_start_time: venue.day_start_time || '' }); setEditingVenue(true); }}>
              Edit
            </Button>
          )}
        </div>
        {editingVenue ? (
          <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div style={{ flex: '1.4 1 220px', minWidth: 0 }}>
              <label className="n-label">Address</label>
              <AddressSearch
                value={venueForm.location}
                onChange={loc => setVenueForm(f => ({ ...f, location: loc }))}
                onSelect={sel => setVenueForm(f => ({ ...f, location: sel.address, timezone: sel.timezone || f.timezone }))}
                placeholder="Search address"
                inputStyle={addressInputStyle}
              />
            </div>
            <div style={{ flex: '1 1 160px', minWidth: 0 }}>
              <label className="n-label" htmlFor={`${fieldId}-tz`}>Timezone</label>
              <input id={`${fieldId}-tz`} className="n-input" value={venueForm.timezone} onChange={e => setVenueForm(f => ({ ...f, timezone: e.target.value }))}
                placeholder="e.g. Pacific/Auckland" style={{ width: '100%' }} />
            </div>
            <div style={{ flex: '0 0 120px' }}>
              <label className="n-label" htmlFor={`${fieldId}-day`}>Day start</label>
              <input id={`${fieldId}-day`} className="n-input" type="time" value={venueForm.day_start_time} onChange={e => setVenueForm(f => ({ ...f, day_start_time: e.target.value }))}
                style={{ width: '100%' }} />
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <Button variant="primary" onClick={async () => {
                setSavingVenue(true);
                try {
                  await apiFetch(`/api/venues/${venue.id}`, {
                    method: 'PUT',
                    body: JSON.stringify({ location: venueForm.location || null, timezone: venueForm.timezone || null, day_start_time: venueForm.day_start_time || null }),
                  });
                  setEditingVenue(false);
                  onUpdate();
                } finally { setSavingVenue(false); }
              }} disabled={savingVenue}>{savingVenue ? 'Saving…' : 'Save'}</Button>
              <Button onClick={() => setEditingVenue(false)}>Cancel</Button>
            </div>
          </div>
        ) : (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 24px', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
            <span>Timezone: <strong style={{ fontWeight: 600, color: 'var(--text)' }}>{venue.timezone || '—'}</strong></span>
            <span>Day starts: <strong style={{ fontWeight: 600, color: 'var(--text)' }}>{venue.day_start_time || '—'}</strong></span>
          </div>
        )}
      </div>

      {/* Connections */}
      <div>
        <div className="n-eyebrow" style={{ marginBottom: 8 }}>Connections</div>
        {loadingConnectors ? (
          <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>Loading connectors…</div>
        ) : connectors.length === 0 ? (
          <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>No connectors available. Add connector specs first.</div>
        ) : (
          <div className="n-card" style={{ borderRadius: 'var(--radius)', overflow: 'hidden' }}>
            {connectors.map((c, i) => {
              const isEditing = editingConnector === c.name;
              const form = connectorForms[c.name] || {};
              const fields = c.fields || [];
              return (
                <div key={c.name} style={i > 0 ? { borderTop: '1px solid var(--line-soft)' } : undefined}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', padding: '8px 12px' }}>
                    <span style={{ flex: '1 1 140px', minWidth: 0, fontSize: 'var(--fs-base)', fontWeight: 500, color: 'var(--text)' }}>{c.label}</span>
                    {/* Without settings:connectors, the status alone. */}
                    {!canConnect ? (
                      c.auth_type === 'oauth2' ? (
                        c.oauth_connected && c.needs_reconnect ? <Badge tone="error">Reconnect needed</Badge>
                          : c.oauth_connected ? <Badge tone="ok">Connected</Badge>
                          : <Badge>Not connected</Badge>
                      ) : c.configured ? <Badge tone="ok">Configured</Badge> : <Badge>Not configured</Badge>
                    ) : c.auth_type === 'oauth2' ? (
                      c.oauth_connected && c.needs_reconnect ? (
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }} title={c.last_auth_error || 'This connection stopped working and needs to be reconnected.'}>
                          <Badge tone="error">Reconnect needed</Badge>
                          <Button variant="primary" size="sm" onClick={(e) => { e.stopPropagation(); handleOAuthConnect(c.name); }}>Reconnect</Button>
                        </div>
                      ) : c.oauth_connected ? (
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <Badge tone="ok">Connected</Badge>
                          <Button size="sm" onClick={(e) => { e.stopPropagation(); handleOAuthDisconnect(c.name); }}>Disconnect</Button>
                        </div>
                      ) : (
                        <Button variant="primary" size="sm" onClick={(e) => { e.stopPropagation(); handleOAuthConnect(c.name); }}>Connect</Button>
                      )
                    ) : (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        {c.configured && <Badge tone="ok">Configured</Badge>}
                        <Button size="sm" onClick={(e) => { e.stopPropagation(); setEditingConnector(isEditing ? null : c.name); }}>
                          {c.configured ? 'Edit' : 'Configure'}
                        </Button>
                      </div>
                    )}
                  </div>

                  {/* Credential form */}
                  {isEditing && fields.length > 0 && (
                    <div style={{
                      display: 'flex', flexDirection: 'column', gap: 8, padding: '10px 12px 12px',
                      borderTop: '1px solid var(--line-soft)', backgroundColor: 'var(--surface)',
                    }}>
                      {fields.map(f => (
                        <div key={f.key}>
                          <label className="n-label" htmlFor={`${fieldId}-${c.name}-${f.key}`}>{f.label}</label>
                          <input
                            id={`${fieldId}-${c.name}-${f.key}`}
                            className="n-input"
                            type={f.secret ? 'password' : 'text'}
                            value={form[f.key] || ''}
                            onChange={e => setConnectorForms(prev => ({
                              ...prev,
                              [c.name]: { ...(prev[c.name] || {}), [f.key]: e.target.value },
                            }))}
                            placeholder={f.secret && c.configured ? '••••••••' : f.label}
                            style={{ width: '100%' }}
                          />
                        </div>
                      ))}
                      <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
                        <Button variant="primary" size="sm" onClick={(e) => { e.stopPropagation(); handleSaveConnector(c.name); }}
                          disabled={savingConnector === c.name}>{savingConnector === c.name ? 'Saving…' : 'Save'}</Button>
                        <Button size="sm" onClick={(e) => { e.stopPropagation(); setEditingConnector(null); }}>Cancel</Button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Delete venue (asks first — see VenuesTab) */}
      {canManage && (
        <div style={{ paddingTop: 12, borderTop: '1px solid var(--line)' }}>
          <Button variant="danger" size="sm" onClick={(e) => { e.stopPropagation(); onDelete(); }}>Delete venue</Button>
        </div>
      )}
    </div>
  );

  // Phones: a list row — name, address, then timezone · day start · count.
  if (compact) {
    return (
      <div>
        <div onClick={handleToggle} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 4px 10px 12px', cursor: 'pointer' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 600, color: 'var(--text)' }}>{venue.name}</div>
            {venue.location && (
              <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{venue.location}</div>
            )}
            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
              {venue.timezone || 'No timezone'}{venue.day_start_time ? ` · Day starts ${venue.day_start_time}` : ''}
              {` · ${connectorCount} connection${connectorCount !== 1 ? 's' : ''}`}
            </div>
          </div>
          {chevron}
        </div>
        {expanded && <div style={{ padding: '4px 12px 16px' }}>{details}</div>}
      </div>
    );
  }

  // The last row rounds its bottom corners (hover fill, open details).
  const corner = (side: 'left' | 'right'): CSSProperties =>
    isLast && !expanded ? (side === 'left' ? { borderBottomLeftRadius: 11 } : { borderBottomRightRadius: 11 }) : {};
  const cell = expanded ? expandedCell : undefined;
  return (
    <>
      <tr onClick={handleToggle} style={{ cursor: 'pointer' }}>
        <td style={{ ...cell, ...corner('left') }}>
          <div style={{ fontWeight: 600, color: 'var(--text)' }}>{venue.name}</div>
          {venue.location && <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{venue.location}</div>}
        </td>
        <td style={{ ...cell, color: venue.timezone ? 'var(--text)' : 'var(--muted)' }}>{venue.timezone || 'No timezone'}</td>
        <td style={{ ...cell, color: venue.day_start_time ? 'var(--text)' : 'var(--muted)' }}>{venue.day_start_time || '—'}</td>
        <td className="num" style={cell}>{connectorCount}</td>
        <td style={{ ...cell, ...corner('right'), padding: '4px 8px', textAlign: 'right' }}>{chevron}</td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={5} style={{
            padding: '8px 12px 16px', backgroundColor: 'var(--surface-alt)',
            ...(isLast ? { borderBottomLeftRadius: 11, borderBottomRightRadius: 11 } : {}),
          }}>
            {details}
          </td>
        </tr>
      )}
    </>
  );
}

/** `canManage` = org:venues (add, edit, delete); `canConnect` = settings:connectors. */
function VenuesTab({ canManage, canConnect }: { canManage: boolean; canConnect: boolean }) {
  const [org, setOrg] = useState<Organization | null>(null);
  const [venues, setVenues] = useState<VenueDetail[]>([]);
  const [loading, setLoading] = useState(true);
  const [newName, setNewName] = useState('');
  const [newLocation, setNewLocation] = useState('');
  const [newTimezone, setNewTimezone] = useState('');
  const [adding, setAdding] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The venue waiting on "Delete venue?" — nothing is deleted until confirmed.
  const [deleting, setDeleting] = useState<VenueDetail | null>(null);
  const fieldId = useId();
  const { isMobile } = useBreakpoint();

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
    if (!newName.trim()) {
      setError('Please enter a venue name.');
      return;
    }
    if (!org) {
      setError('No organization found for your account — contact your administrator.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/organizations/${org.id}/venues`, {
        method: 'POST',
        body: JSON.stringify({ name: newName, location: newLocation || null, timezone: newTimezone || null }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        setError(detail?.detail || `Failed to create venue (${res.status})`);
        return;
      }
      setNewName('');
      setNewLocation('');
      setNewTimezone('');
      setAdding(false);
      loadData();
    } catch {
      setError('Network error — could not create venue.');
    } finally {
      setSaving(false);
    }
  };

  // Throws on failure, so the confirmation stays open and says why.
  const handleDelete = async (venueId: string) => {
    await mustSucceed(() => apiFetch(`/api/venues/${venueId}`, { method: 'DELETE' }), 'The venue wasn’t deleted');
    loadData();
  };

  if (loading) return <PageState kind="loading" title="Loading venues…" />;

  return (
    <div>
      <SectionHeader
        title="Venues"
        meta={venues.length > 0 ? `${venues.length} ${venues.length === 1 ? 'venue' : 'venues'}` : undefined}
        actions={canManage ? (
          // Secondary while the form is open, so its Add is the one primary.
          <Button variant={adding ? 'secondary' : 'primary'} icon={Plus} onClick={() => setAdding(!adding)}>Add venue</Button>
        ) : undefined}
      />

      {deleting && (
        <ConfirmDialog
          title={`Delete ${deleting.name}?`}
          confirmLabel="Delete venue"
          busyLabel="Deleting…"
          danger
          onConfirm={() => handleDelete(deleting.id)}
          onClose={() => setDeleting(null)}
        >
          <p style={{ margin: '0 0 8px' }}>
            Everyone loses access to this venue, and its connections are removed — saved sign-ins included.
          </p>
          <p style={{ margin: 0 }}>
            Its history stays: conversations, orders and reports are kept, no longer linked to a venue. This can’t be undone.
          </p>
        </ConfirmDialog>
      )}

      {canManage && adding && (
        <div className="n-card" style={{ padding: 16, marginBottom: 16 }}>
          <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div style={{ flex: '1 1 180px', minWidth: 0 }}>
              <label className="n-label" htmlFor={`${fieldId}-name`}>Name</label>
              <input id={`${fieldId}-name`} className="n-input" value={newName} onChange={e => setNewName(e.target.value)} placeholder="Venue name"
                style={{ width: '100%' }} />
            </div>
            <div style={{ flex: '1.4 1 220px', minWidth: 0 }}>
              <label className="n-label">Address</label>
              <AddressSearch
                value={newLocation}
                onChange={setNewLocation}
                onSelect={sel => { if (sel.timezone) setNewTimezone(sel.timezone); }}
                placeholder="Search address (optional)"
                inputStyle={addressInputStyle}
              />
            </div>
            <div style={{ flex: '1 1 180px', minWidth: 0 }}>
              <label className="n-label" htmlFor={`${fieldId}-tz`}>Timezone</label>
              <input id={`${fieldId}-tz`} className="n-input" value={newTimezone} onChange={e => setNewTimezone(e.target.value)} placeholder="Auto-set from address"
                style={{ width: '100%' }} />
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <Button variant="primary" onClick={handleAdd} disabled={saving}>{saving ? 'Adding…' : 'Add'}</Button>
              <Button onClick={() => { setAdding(false); setError(null); }}>Cancel</Button>
            </div>
          </div>
          {error && (
            <div role="alert" style={{
              marginTop: 12, padding: '8px 12px', fontSize: 'var(--fs-sm)',
              color: 'var(--error)', backgroundColor: 'var(--error-bg)', borderRadius: 'var(--radius)',
            }}>{error}</div>
          )}
        </div>
      )}

      {venues.length === 0 ? (
        org
          ? <PageState kind="empty" title="No venues yet" detail={canManage ? 'Click "Add venue" to create one.' : 'An owner or manager can add one.'} />
          : <PageState kind="empty" title="No organization linked" detail="Your account isn’t linked to an organization yet, so venues can’t be created. Contact your administrator." />
      ) : isMobile ? (
        <div className="n-card">
          {venues.map((v, i) => (
            <div key={v.id} style={i > 0 ? { borderTop: '1px solid var(--line)' } : undefined}>
              <VenueCard venue={v} onDelete={() => setDeleting(v)} onUpdate={loadData} compact isLast={i === venues.length - 1} canManage={canManage} canConnect={canConnect} />
            </div>
          ))}
        </div>
      ) : (
        // No overflow clip on this card: an open venue's address suggestions
        // hang below it. The corner cells round themselves instead.
        <div className="n-card">
          <table className="n-table">
            <thead>
              <tr>
                <th style={{ borderTopLeftRadius: 11 }}>Name</th>
                <th>Timezone</th>
                <th>Day starts</th>
                <th className="num">Connections</th>
                <th style={{ width: 48, borderTopRightRadius: 11 }}><span style={srOnly}>Details</span></th>
              </tr>
            </thead>
            <tbody>
              {venues.map((v, i) => (
                <VenueCard key={v.id} venue={v} onDelete={() => setDeleting(v)} onUpdate={loadData} compact={false} isLast={i === venues.length - 1} canManage={canManage} canConnect={canConnect} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// --- Users Tab ---

/** One period's usage. input_tokens is full-price input only — the cache is
 * counted separately; billable_tokens is what plan limits count. */
interface UsageFigures {
  input_tokens: number; output_tokens: number; llm_call_count: number;
  cache_read_tokens?: number; cache_write_tokens?: number;
  billable_tokens?: number; cost_usd?: number;
}
type UsageByUser = UsageFigures;
type DailyUsageEntry = UsageFigures;
interface UsageBreakdown {
  by_kind: Record<string, { calls: number; cost_usd: number; billable_tokens: number }>;
  top_threads: { thread_id: string; title: string; calls: number; cost_usd: number; billable_tokens: number; cache_ratio: number | null }[];
}

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

/**
 * `canManage` = org:members: inviting, changing someone's role or venues,
 * removing them. Without it the list is read-only — no buttons the API would
 * refuse.
 */
function UsersTab({ canManage }: { canManage: boolean }) {
  const [org, setOrg] = useState<Organization | null>(null);
  const [members, setMembers] = useState<OrgMember[]>([]);
  const [venues, setVenues] = useState<VenueDetail[]>([]);
  const [memberVenues, setMemberVenues] = useState<Record<string, string[]>>({});
  const [usage, setUsage] = useState<Record<string, UsageByUser>>({});
  const [usageTotals, setUsageTotals] = useState<{
    input: number; output: number; cacheRead: number; cacheWrite: number; billable: number; cost: number; calls: number;
  }>({ input: 0, output: 0, cacheRead: 0, cacheWrite: 0, billable: 0, cost: 0, calls: 0 });
  const [usageBreakdown, setUsageBreakdown] = useState<UsageBreakdown | null>(null);
  const [loading, setLoading] = useState(true);
  const [addEmail, setAddEmail] = useState('');
  const [addRole, setAddRole] = useState('');
  const [addVenueIds, setAddVenueIds] = useState<string[]>([]);
  const [addError, setAddError] = useState('');
  const [addSuccess, setAddSuccess] = useState('');
  const [showInviteModal, setShowInviteModal] = useState(false);
  // The person waiting on "Remove …?" — nobody is removed until confirmed.
  const [removing, setRemoving] = useState<OrgMember | null>(null);
  // Why the last role or venue change didn't stick, shown beside that control
  // until the next attempt.
  const [changeError, setChangeError] = useState<{ userId: string; field: 'role' | 'venues'; message: string } | null>(null);
  const [resendStatus, setResendStatus] = useState<Record<string, 'sending' | 'sent' | 'failed'>>({});
  const [viewingRoleId, setViewingRoleId] = useState<string | null>(null);
  const [availableRoles, setAvailableRoles] = useState<{ id: string; name: string; display_name: string; is_system: boolean; permissions: string[] }[]>([]);
  const [expandedMember, setExpandedMember] = useState<string | null>(null);
  const [memberDailyUsage, setMemberDailyUsage] = useState<Record<string, Record<string, DailyUsageEntry>>>({});
  const [showDailyUsage, setShowDailyUsage] = useState(false);
  const [dailyUsage, setDailyUsage] = useState<Record<string, DailyUsageEntry>>({});
  const inviteId = useId();
  const { isMobile } = useBreakpoint();

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
            cacheRead: usageData.total_cache_read_tokens || 0,
            cacheWrite: usageData.total_cache_write_tokens || 0,
            billable: usageData.total_billable_tokens || 0,
            cost: usageData.total_cost_usd || 0,
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

  // Throws on failure, so the confirmation stays open and says why.
  const handleRemove = async (userId: string) => {
    if (!org) throw new Error('No organization found for your account.');
    await mustSucceed(
      () => apiFetch(`/api/organizations/${org.id}/members/${userId}`, { method: 'DELETE' }),
      'They weren’t removed',
    );
    setExpandedMember(null);
    loadData();
  };

  // Ticks at once; if the API refuses, the box flips back and says why.
  const handleToggleVenue = async (userId: string, venueId: string, checked: boolean) => {
    setChangeError(null);
    const current = memberVenues[userId] || [];
    const updated = checked ? [...current, venueId] : current.filter(id => id !== venueId);
    setMemberVenues(prev => ({ ...prev, [userId]: updated }));
    try {
      await mustSucceed(
        () => apiFetch(`/api/users/${userId}/venues`, { method: 'PUT', body: JSON.stringify({ venue_ids: updated }) }),
        'Venue access wasn’t changed',
      );
    } catch (e) {
      // Undo just this venue, not any other box ticked meanwhile.
      setMemberVenues(prev => {
        const ids = prev[userId] || [];
        return { ...prev, [userId]: checked ? ids.filter(id => id !== venueId) : [...ids, venueId] };
      });
      setChangeError({ userId, field: 'venues', message: (e as Error).message });
    }
  };

  // The picker shows the new role at once; a refusal puts the old one back.
  const handleRoleChange = async (userId: string, roleId: string) => {
    if (!org) return;
    setChangeError(null);
    const before = members.find(m => m.user_id === userId);
    const role = availableRoles.find(r => r.id === roleId);
    if (!before || !role) return;
    const withRole = (m: OrgMember): OrgMember => ({ ...m, role_id: role.id, role: role.name, role_name: role.name, role_display_name: role.display_name });
    setMembers(prev => prev.map(m => (m.user_id === userId ? withRole(m) : m)));
    try {
      await mustSucceed(
        () => apiFetch(`/api/organizations/${org.id}/members/${userId}/role`, { method: 'PUT', body: JSON.stringify({ role_id: roleId }) }),
        'The role wasn’t changed',
      );
    } catch (e) {
      setMembers(prev => prev.map(m => (m.user_id === userId ? before : m)));
      setChangeError({ userId, field: 'role', message: (e as Error).message });
    }
  };

  /** Why a change just failed, in small error text under the control. */
  const changeFailure = (m: OrgMember, field: 'role' | 'venues') =>
    changeError?.userId === m.user_id && changeError.field === field ? (
      <div role="alert" style={{ marginTop: 6, fontSize: 'var(--fs-sm)', color: 'var(--error)', maxWidth: 320 }}>
        {changeError.message}
      </div>
    ) : null;

  if (loading) return <PageState kind="loading" title="Loading users…" />;

  const pendingCount = members.filter(m => m.is_active === false).length;
  const peopleMeta = members.length > 0
    ? `${members.length} ${members.length === 1 ? 'person' : 'people'}${pendingCount > 0 ? ` · ${pendingCount} invite${pendingCount === 1 ? '' : 's'} pending` : ''}`
    : undefined;

  /** "$1.23 · 45.6K" — the month's cost, then the billable tokens plan limits count. */
  const memberTokens = (m: OrgMember): string | null => {
    const u = usage[m.user_id];
    if (!u) return null;
    return `${fmtUsd(u.cost_usd)} · ${fmtTokens(u.billable_tokens || 0)}`;
  };
  const memberUsageTitle = (m: OrgMember): string | undefined => {
    const u = usage[m.user_id];
    return u ? usageTitle(u) : undefined;
  };

  const toggleMember = async (m: OrgMember, isExpanded: boolean) => {
    const next = isExpanded ? null : m.user_id;
    setExpandedMember(next);
    if (next && org && !memberDailyUsage[m.user_id]) {
      const res = await apiFetch(`/api/organizations/${org.id}/usage/daily?user_id=${m.user_id}`);
      if (res.ok) {
        const d = await res.json();
        setMemberDailyUsage(prev => ({ ...prev, [m.user_id]: d.days || {} }));
      }
    }
  };

  // Role picker and the permissions button. Clicks here don't open the row.
  const roleControl = (m: OrgMember) => (
    <div onClick={e => e.stopPropagation()}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        {!canManage ? (
          <span style={{ color: m.role_display_name || m.role ? 'var(--text)' : 'var(--muted)', whiteSpace: 'nowrap' }}>
            {m.role_display_name || m.role || 'Unassigned'}
          </span>
        ) : availableRoles.length > 0 ? (
          <select
            className="n-select"
            aria-label={`Role for ${m.full_name || m.email}`}
            value={m.role_id || ''}
            onChange={e => handleRoleChange(m.user_id, e.target.value)}
            style={{ fontSize: 'var(--fs-sm)' }}
          >
            {!m.role_id && <option value="">— Unassigned —</option>}
            {availableRoles.map(r => (
              <option key={r.id} value={r.id}>{r.display_name}</option>
            ))}
          </select>
        ) : (
          <Badge>{m.role_display_name || m.role}</Badge>
        )}
        <IconButton
          icon={Info}
          iconSize={16}
          label="View role permissions"
          onClick={() => setViewingRoleId(viewingRoleId === (m.role_id || '') ? null : (m.role_id || ''))}
        />
      </div>
      {changeFailure(m, 'role')}
    </div>
  );

  const statusControl = (m: OrgMember) => {
    if (m.is_active !== false) return <Badge tone="ok">Active</Badge>;
    const status = resendStatus[m.email];
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <Badge tone="warn">Invite pending</Badge>
        {status === 'sending' ? (
          <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>Sending…</span>
        ) : status === 'sent' ? (
          <span style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--ok)' }}>Sent!</span>
        ) : status === 'failed' ? (
          <span style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--error)' }}>Failed</span>
        ) : canManage ? (
          <Button variant="link" size="sm" onClick={e => { e.stopPropagation(); handleResendInvite(m.email); }}>Resend invite</Button>
        ) : null}
      </div>
    );
  };

  // The venues a member can open, from the access list loaded above.
  const venueAccess = (m: OrgMember) => {
    const ids = memberVenues[m.user_id];
    if (!ids) return <span style={{ color: 'var(--muted)' }}>—</span>;
    const named = venues.filter(v => ids.includes(v.id));
    if (named.length === 0) return <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>No venues</span>;
    if (venues.length > 1 && named.length === venues.length) return <Badge>All venues</Badge>;
    return (
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {named.map(v => <Badge key={v.id}>{v.name}</Badge>)}
      </div>
    );
  };

  const chevron = (m: OrgMember, isExpanded: boolean) => (
    <IconButton
      icon={isExpanded ? ChevronDown : ChevronRight}
      iconSize={16}
      aria-expanded={isExpanded}
      label={`${isExpanded ? 'Hide' : 'Show'} details for ${m.full_name || m.email}`}
    />
  );

  const memberDetails = (m: OrgMember) => {
    const userVenues = memberVenues[m.user_id] || [];
    const u = usage[m.user_id];
    const days = memberDailyUsage[m.user_id] || {};
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {/* Usage summary */}
        {u ? (
          <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
            <Stat label="Cost" value={fmtUsd(u.cost_usd)} />
            <div title={usageTitle(u)}><Stat label="Billable tokens" value={fmtTokens(u.billable_tokens || 0)} /></div>
            <Stat label="Calls" value={u.llm_call_count || 0} />
          </div>
        ) : (
          <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>No usage this month</div>
        )}

        {/* Daily usage chart */}
        {Object.keys(days).length > 0 && (
          <div>
            <div className="n-eyebrow" style={{ marginBottom: 6 }}>Daily usage</div>
            <DailyCostBars days={days} compact />
          </div>
        )}

        {/* Venues — ticked here by someone who may change them, listed otherwise */}
        <div>
          <div className="n-eyebrow" style={{ marginBottom: 6 }}>Venue access</div>
          {!canManage ? venueAccess(m) : (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 16px' }}>
              {venues.map(v => (
                <label key={v.id} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--text)', cursor: 'pointer' }}>
                  <input type="checkbox" checked={userVenues.includes(v.id)}
                    onChange={e => { e.stopPropagation(); handleToggleVenue(m.user_id, v.id, e.target.checked); }}
                    style={{ accentColor: 'var(--accent)' }} />
                  {v.name}
                </label>
              ))}
              {venues.length === 0 && <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>No venues created yet</span>}
            </div>
          )}
          {changeFailure(m, 'venues')}
        </div>

        {/* Remove (asks first) */}
        {canManage && (
          <div style={{ paddingTop: 12, borderTop: '1px solid var(--line)' }}>
            <Button variant="danger" size="sm" onClick={(e) => { e.stopPropagation(); setRemoving(m); }}>Remove from organization</Button>
          </div>
        )}
      </div>
    );
  };

  return (
    <div>
      <SectionHeader
        title="Users"
        meta={peopleMeta}
        actions={canManage ? (
          <Button variant="primary" icon={Plus} onClick={() => { setShowInviteModal(true); setAddError(''); setAddSuccess(''); setAddEmail(''); setAddVenueIds([]); }}>
            Invite user
          </Button>
        ) : undefined}
      />

      {removing && (
        <ConfirmDialog
          title={`Remove ${removing.full_name || removing.email}?`}
          confirmLabel="Remove"
          busyLabel="Removing…"
          danger
          onConfirm={() => handleRemove(removing.user_id)}
          onClose={() => setRemoving(null)}
        >
          <p style={{ margin: 0 }}>
            They lose access to {org?.name || 'this organization'} straight away. Their conversations are kept.
          </p>
        </ConfirmDialog>
      )}

      {/* Invite User Modal */}
      {showInviteModal && (
        <Dialog title="Invite user" width={420} onClose={() => setShowInviteModal(false)}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div>
              <label className="n-label" htmlFor={`${inviteId}-email`}>Email</label>
              <input id={`${inviteId}-email`} className="n-input" value={addEmail} onChange={e => { setAddEmail(e.target.value); setAddError(''); setAddSuccess(''); }}
                placeholder="user@example.com" style={{ width: '100%' }} />
            </div>

            <div>
              <label className="n-label" htmlFor={`${inviteId}-role`}>Role</label>
              <select id={`${inviteId}-role`} className="n-select" value={addRole} onChange={e => setAddRole(e.target.value)} style={{ width: '100%' }}>
                {availableRoles.map(r => (
                  <option key={r.id} value={r.id}>{r.display_name}</option>
                ))}
                {availableRoles.length === 0 && <option value="">No roles</option>}
              </select>
            </div>

            {venues.length > 0 && (
              <div>
                <div className="n-label">Venues</div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 16px' }}>
                  {venues.map(v => (
                    <label key={v.id} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--text)', cursor: 'pointer' }}>
                      <input type="checkbox" checked={addVenueIds.includes(v.id)}
                        onChange={e => {
                          setAddVenueIds(prev => e.target.checked ? [...prev, v.id] : prev.filter(id => id !== v.id));
                        }}
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
            <Button onClick={() => setShowInviteModal(false)}>Cancel</Button>
            <Button variant="primary" onClick={async () => { await handleInvite(); }}>Send invite</Button>
          </div>
        </Dialog>
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
          <Dialog title={`${role.display_name} permissions`} width={400} onClose={() => setViewingRoleId(null)} closeButton>
            <p style={{ margin: '-8px 0 16px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
              {perms.length} permissions granted
            </p>
            {Object.entries(groups).map(([cat, catPerms]) => (
              <div key={cat} style={{ marginBottom: 12 }}>
                <div className="n-eyebrow" style={{ marginBottom: 6 }}>{cat}</div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                  {catPerms.map(p => (
                    <Badge key={p}>{p.split(':')[1]}</Badge>
                  ))}
                </div>
              </div>
            ))}
          </Dialog>
        );
      })()}

      {/* User list */}
      {members.length === 0 ? (
        <PageState kind="empty" title="No members yet" />
      ) : isMobile ? (
        // Phones: one row per person — who, their role, then status and venues.
        <div className="n-card" style={{ overflow: 'hidden' }}>
          {members.map((m, i) => {
            const isExpanded = expandedMember === m.user_id;
            const tokens = memberTokens(m);
            return (
              <div key={m.id} style={i > 0 ? { borderTop: '1px solid var(--line)' } : undefined}>
                <div onClick={() => toggleMember(m, isExpanded)} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '12px 4px 12px 12px', cursor: 'pointer' }}>
                  <Avatar name={m.full_name || m.email} size={28} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{m.full_name || m.email}</div>
                    {m.full_name && (
                      <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{m.email}</div>
                    )}
                    <div style={{ marginTop: 8 }}>{roleControl(m)}</div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
                      {statusControl(m)}
                      {venueAccess(m)}
                      {tokens && <span title={memberUsageTitle(m)} style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{tokens} billable</span>}
                    </div>
                  </div>
                  {chevron(m, isExpanded)}
                </div>
                {isExpanded && <div style={{ padding: '0 12px 16px' }}>{memberDetails(m)}</div>}
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
                <th className="num" title="Cost and billable tokens this month">Usage</th>
                <th style={{ width: 48 }}><span style={srOnly}>Details</span></th>
              </tr>
            </thead>
            <tbody>
              {members.map(m => {
                const isExpanded = expandedMember === m.user_id;
                const cell = isExpanded ? expandedCell : undefined;
                const tokens = memberTokens(m);
                return (
                  <Fragment key={m.id}>
                    <tr onClick={() => toggleMember(m, isExpanded)} style={{ cursor: 'pointer' }}>
                      <td style={cell}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                          <Avatar name={m.full_name || m.email} size={28} />
                          <div style={{ minWidth: 0 }}>
                            <div style={{ fontWeight: 600, color: 'var(--text)' }}>{m.full_name || m.email}</div>
                            {m.full_name && <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{m.email}</div>}
                          </div>
                        </div>
                      </td>
                      <td style={cell}>{roleControl(m)}</td>
                      <td style={cell}>{venueAccess(m)}</td>
                      <td style={cell}>{statusControl(m)}</td>
                      <td className="num" title={memberUsageTitle(m)} style={{ ...cell, color: tokens ? 'var(--text-soft)' : 'var(--muted)', whiteSpace: 'nowrap' }}>{tokens ?? '—'}</td>
                      <td style={{ ...cell, padding: '4px 8px', textAlign: 'right' }}>{chevron(m, isExpanded)}</td>
                    </tr>
                    {isExpanded && (
                      <tr>
                        {/* Indented to line up with the name, past the avatar. */}
                        <td colSpan={6} style={{ padding: '8px 12px 16px 50px', backgroundColor: 'var(--surface-alt)' }}>
                          {memberDetails(m)}
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

      {/* Usage this month — the whole organisation */}
      {usageTotals.calls > 0 && (
        <div className="n-card" style={{ marginTop: 16 }}>
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
                if (next && org && Object.keys(dailyUsage).length === 0) {
                  const res = await apiFetch(`/api/organizations/${org.id}/usage/daily`);
                  if (res.ok) {
                    const d = await res.json();
                    setDailyUsage(d.days || {});
                  }
                }
                if (next && org && !usageBreakdown) {
                  const res = await apiFetch(`/api/organizations/${org.id}/usage/breakdown`);
                  if (res.ok) setUsageBreakdown(await res.json());
                }
              }}
            >
              Daily usage
            </Button>
          </div>

          {/* Daily usage chart */}
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

export type SettingsTab = 'app-map' | 'connections' | 'connectors' | 'agents' | 'components' | 'playbooks' | 'supplier-specs' | 'templates' | 'venues' | 'members' | 'billing' | 'email' | 'deployments' | 'tests' | 'roles' | 'secrets' | 'threads' | 'mcp' | 'preferences';

function hasSettingsPermission(user: User | null, ...perms: string[]): boolean {
  return perms.some(p => can(user, p));
}

export default function SettingsPanel({ initialTab }: { initialTab?: SettingsTab } = {}) {
  // Opens on a given tab when asked (the quota dialog's "Top up" → Billing).
  const [activeTab, setActiveTab] = useState<SettingsTab>(initialTab ?? 'preferences');
  const [orgId, setOrgId] = useState<string | null>(null);
  const storedUser = getStoredUser() as User | null;
  const isAdmin = storedUser?.role === 'admin';

  const showConnectors = isAdmin;
  // Hierarchy v2: Connections is for civilians — the per-venue status matrix
  // with "used by" App chips; the spec editor stays admin-only below.
  const showConnections = isAdmin || hasSettingsPermission(storedUser, 'settings:connectors');
  const showAgents = isAdmin;

  const showDeployments = isAdmin;
  const showTests = isAdmin;
  const showRoles = hasSettingsPermission(storedUser, 'org:roles', 'org:members');
  const showComponents = isAdmin;
  const showPlaybooks = isAdmin;
  const showSupplierSpecs = isAdmin;
  const [specEditing, setSpecEditing] = useState(false);
  const showSecrets = isAdmin;
  const showMcp = isAdmin;
  const showThreads = isAdmin;
  // Billing and Email are listed only for someone who can read them.
  const showBilling = can(storedUser, 'billing:read');
  const showEmail = can(storedUser, 'email:read');
  const { isMobile } = useBreakpoint();
  // The organisation's name, shown beside the page title.
  const [orgName, setOrgName] = useState<string | null>(null);
  const fieldId = useId();

  // Fetch org ID for billing tab
  useEffect(() => {
    apiFetch('/api/organizations').then(r => r.json()).then(d => {
      const orgs = d.organizations || [];
      if (orgs.length > 0) {
        setOrgId(orgs[0].id);
        setOrgName(orgs[0].name || null);
      }
    }).catch(() => {});
  }, []);

  // The tab strip scrolls sideways when it doesn't fit; a fade at its right
  // edge says there is more, until the strip is scrolled to its end.
  const tabsRef = useRef<HTMLDivElement>(null);
  const [tabsMore, setTabsMore] = useState(false);
  const measureTabs = useCallback(() => {
    const list = tabsRef.current?.querySelector<HTMLElement>('[role="tablist"]');
    if (list) setTabsMore(list.scrollLeft + list.clientWidth < list.scrollWidth - 1);
  }, []);
  // Phones only (wider screens have the side nav), so re-measure when the
  // strip appears.
  useEffect(() => {
    const frame = requestAnimationFrame(measureTabs);
    document.fonts?.ready.then(measureTabs).catch(() => {});
    window.addEventListener('resize', measureTabs);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('resize', measureTabs);
    };
  }, [measureTabs, isMobile]);

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
      case 'testing': return 'var(--muted)';
      case 'success': return 'var(--ok)';
      case 'error': return 'var(--error)';
      default: return 'var(--muted)';
    }
  };

  // The sections, in groups: a side nav on tablets and up, one tab strip on
  // phones. Every section keeps its settings-tab-<id> test id (saved E2E
  // tests click them) and its permission check; an empty group is left out.
  const groups: SubNavGroup[] = [];
  const addGroup = (label: string, entries: [show: boolean, id: SettingsTab, text: string][]) => {
    groups.push({ label, items: entries.filter(([show]) => show).map(([, id, text]) => ({ id, label: text, testId: `settings-tab-${id}` })) });
  };
  addGroup('You', [[true, 'preferences', 'Preferences']]);
  addGroup('Organisation', [
    [true, 'venues', 'Venues'],
    [true, 'members', 'Users'],
    [showRoles, 'roles', 'Roles'],
    [showBilling, 'billing', 'Billing'],
    [showEmail, 'email', 'Email'],
    [showConnections, 'connections', 'Connections'],
  ]);
  addGroup('AI team', [
    [showAgents, 'agents', 'Agents'],
    [showPlaybooks, 'playbooks', 'Playbooks'],
    [showPlaybooks, 'templates', 'Templates'],
    [showComponents, 'components', 'Components'],
    [showConnectors, 'connectors', 'Connector specs'],
    [showSupplierSpecs, 'supplier-specs', 'Supplier specs'],
    [isAdmin, 'app-map', 'App map'],
  ]);
  addGroup('Platform', [
    [showDeployments, 'deployments', 'Deployments'],
    [showTests, 'tests', 'Tests'],
    [showSecrets, 'secrets', 'Secrets'],
    [showMcp, 'mcp', 'MCP'],
    [showThreads, 'threads', 'Threads'],
  ]);
  const shownGroups = groups.filter(g => g.items.length > 0);
  const visibleTabs = shownGroups.flatMap(g => g.items.map(i => i.id as SettingsTab));
  // A section this user can't open — the quota dialog's "Top up" sends
  // everyone to Billing — shows Preferences (everyone has it) instead of a
  // page that would 403.
  const tab: SettingsTab = visibleTabs.includes(activeTab) ? activeTab : visibleTabs.includes('preferences') ? 'preferences' : visibleTabs[0];
  // Phones: the same groups in one strip, a thin rule between them.
  const tabItems: TabEntry[] = shownGroups.flatMap((g, i) => (i === 0 ? g.items : ['divider' as const, ...g.items]));

  // A 1px gap under the fade keeps the strip's rule unbroken.
  const tabsFade: CSSProperties = {
    position: 'absolute', top: 0, right: 0, bottom: 1, width: 48, pointerEvents: 'none',
    background: 'linear-gradient(to right, transparent, var(--canvas))',
  };
  const preStyle: CSSProperties = {
    margin: 0, padding: 8, maxHeight: 200, overflow: 'auto', borderRadius: 'var(--radius-sm)',
    backgroundColor: 'var(--code-bg)', color: 'var(--code-text)',
    fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', lineHeight: 1.4,
  };
  const threadsTab = tab === 'threads';
  // Tablets and up: the section list down the left, the section beside it.
  // Each column scrolls on its own, so the list stays put.
  // Threads fills its column edge to edge (its list keeps its own rule).
  const contentStyle: CSSProperties = threadsTab
    ? { flex: 1, minWidth: 0, minHeight: 0, overflow: 'hidden', borderLeft: isMobile ? undefined : '1px solid var(--line)' }
    : isMobile
      ? { flex: 1, minWidth: 0, minHeight: 0, overflowY: 'auto', paddingTop: 8, paddingBottom: 40 }
      : { flex: 1, minWidth: 0, minHeight: 0, overflowY: 'auto', padding: '4px 24px 40px 20px' };

  return (
    // Fills the area it is given and scrolls inside it — no viewport units, so
    // it sits under the phone top bar without running past the bottom.
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', backgroundColor: 'var(--canvas)' }}>
      <div className="n-page" style={{ flex: '0 0 auto' }}>
        <PageHeader
          title="Settings"
          status={orgName ? <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{orgName}</span> : undefined}
        >
          {isMobile && (
            <div ref={tabsRef} style={{ position: 'relative' }} onScrollCapture={measureTabs}>
              <Tabs label="Settings sections" items={tabItems} value={tab} onChange={id => setActiveTab(id as SettingsTab)} />
              {tabsMore && <div aria-hidden="true" style={tabsFade} />}
            </div>
          )}
        </PageHeader>
      </div>

      <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
        {!isMobile && (
          // Item text lines up with the page title: 14px here + the row's 10px.
          <div className="scroll-quiet" style={{ flex: '0 0 auto', width: 220, boxSizing: 'border-box', padding: '4px 6px 40px 14px' }}>
            <SubNav label="Settings sections" groups={groups} value={tab} onChange={id => setActiveTab(id as SettingsTab)} />
          </div>
        )}

        <div className={threadsTab ? undefined : 'n-page'} style={contentStyle}>
          {/* ============ THREADS TAB (admin) ============ */}
          {tab === 'threads' && <AdminThreadsPanel />}

          {/* ============ MCP TAB ============ */}
          {tab === 'mcp' && <McpPanel />}

          {/* ============ PREFERENCES TAB (all users) ============ */}
          {tab === 'preferences' && (
            <>
              {/* What Norm may do without asking — every write, receiving and
                  reconciling included. One home for one question. */}
              <ApprovalPreferences />
              <div style={{ height: 32 }} />
              <MemoryTab />
            </>
          )}

          {/* ============ VENUES TAB ============ */}
          {tab === 'venues' && <VenuesTab canManage={can(storedUser, 'org:venues')} canConnect={can(storedUser, 'settings:connectors')} />}

          {/* ============ MEMBERS TAB ============ */}
          {tab === 'members' && <UsersTab canManage={can(storedUser, 'org:members')} />}

          {/* ============ CONNECTORS TAB ============ */}
          {tab === 'app-map' && isAdmin && <AppMapPanel />}
          {tab === 'connections' && (
            <div style={{ width: '100%' }}>
              <SectionHeader title="Connections" />
              <ConnectionsMatrix />
            </div>
          )}
          {tab === 'connectors' && (
            <>
              <ConnectorSpecsPanel onViewModeChange={setSpecEditing} />

              {!specEditing && <ConsolidatorCoveragePanel />}

              {!specEditing && <section style={{ marginTop: 32, paddingTop: 24, borderTop: '1px solid var(--line)' }}>
              <SectionHeader
                title="Platform connectors"
                meta={connectors.length > 0 ? `${connectors.length} ${connectors.length === 1 ? 'connector' : 'connectors'}` : undefined}
              />
              <div className="n-card" style={{
                display: 'flex', alignItems: 'center', gap: '4px 12px', flexWrap: 'wrap',
                marginBottom: 16, padding: '10px 16px',
              }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-base)', color: 'var(--text)', cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={(() => { try { return localStorage.getItem('norm_show_tool_details') !== 'false'; } catch { return true; } })()}
                    onChange={e => {
                      localStorage.setItem('norm_show_tool_details', String(e.target.checked));
                      // Force re-render
                      setConnectors(c => [...c]);
                    }}
                    style={{ cursor: 'pointer', accentColor: 'var(--accent)' }}
                  />
                  Show tool call details in conversations
                </label>
                <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
                  Toggle request/response cards in the chat view
                </span>
              </div>

              {connectors.map(c => {
                const status = testStatus[c.name] || 'idle';
                return (
                  <div key={c.name} className="n-card" style={{
                    padding: 16,
                    marginBottom: 12,
                    opacity: c.configured && !c.enabled ? 0.6 : 1,
                    transition: 'opacity 0.2s',
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                        <span style={{ fontWeight: 600, fontSize: 'var(--fs-md)', color: 'var(--text)' }}>{c.label}</span>
                        {c.configured && <Badge tone="ok">Configured</Badge>}
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                        {c.configured && (
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
                            <button
                              type="button"
                              role="switch"
                              aria-checked={c.enabled}
                              aria-label={`${c.label} active`}
                              onClick={() => handleToggleEnabled(c.name)}
                              style={{
                                display: 'flex', alignItems: 'center', justifyContent: c.enabled ? 'flex-end' : 'flex-start',
                                width: 36, height: 20, boxSizing: 'border-box', padding: 2,
                                border: 'none', borderRadius: 999, cursor: 'pointer',
                                backgroundColor: c.enabled ? 'var(--primary)' : 'var(--field)',
                                transition: 'background-color 0.2s',
                              }}
                            >
                              <span style={{ width: 16, height: 16, borderRadius: '50%', backgroundColor: 'var(--bg)' }} />
                            </button>
                            {c.enabled ? 'Active' : 'Inactive'}
                          </div>
                        )}
                        <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{c.domain}</span>
                      </div>
                    </div>

                    {/* OAuth2 connectors: show Connect button instead of manual fields */}
                    {c.auth_type === 'oauth2' ? (
                      <>
                        {c.oauth_connected && c.needs_reconnect ? (
                          <div style={{
                            display: 'flex', alignItems: 'center', gap: 6, marginBottom: 12,
                            fontSize: 'var(--fs-sm)', color: 'var(--error)',
                          }} title={c.last_auth_error || undefined}>
                            <span style={{ width: 8, height: 8, borderRadius: '50%', backgroundColor: 'var(--error)', flex: '0 0 auto' }} />
                            Reconnect needed — this connection stopped working
                          </div>
                        ) : c.oauth_connected ? (
                          <div style={{
                            display: 'flex', alignItems: 'center', gap: 6, marginBottom: 12,
                            fontSize: 'var(--fs-sm)', color: 'var(--ok)',
                          }}>
                            <span style={{ width: 8, height: 8, borderRadius: '50%', backgroundColor: 'var(--ok)', flex: '0 0 auto' }} />
                            OAuth connected
                          </div>
                        ) : null}

                        {/* Still show non-secret credential fields (e.g. subdomain) */}
                        {c.fields.filter(f => !f.secret).map(f => (
                          <div key={f.key} style={{ marginBottom: 12 }}>
                            <label className="n-label" htmlFor={`${fieldId}-${c.name}-${f.key}`}>{f.label}</label>
                            <input
                              id={`${fieldId}-${c.name}-${f.key}`}
                              className="n-input"
                              type="text"
                              value={forms[c.name]?.[f.key] || ''}
                              onChange={e => updateField(c.name, f.key, e.target.value)}
                              placeholder={`Enter ${f.label.toLowerCase()}`}
                              style={{ width: '100%' }}
                            />
                          </div>
                        ))}
                      </>
                    ) : (
                      <>
                        {c.fields.map(f => (
                          <div key={f.key} style={{ marginBottom: 12 }}>
                            <label className="n-label" htmlFor={`${fieldId}-${c.name}-${f.key}`}>{f.label}</label>
                            {f.type === 'select' && f.options ? (
                              <select
                                id={`${fieldId}-${c.name}-${f.key}`}
                                className="n-select"
                                value={forms[c.name]?.[f.key] || f.default || ''}
                                onChange={e => updateField(c.name, f.key, e.target.value)}
                                style={{ width: '100%' }}
                              >
                                {f.options.map(opt => (
                                  <option key={opt.id} value={opt.id}>{opt.label}</option>
                                ))}
                              </select>
                            ) : (
                              <input
                                id={`${fieldId}-${c.name}-${f.key}`}
                                className="n-input"
                                type={f.secret ? 'password' : 'text'}
                                value={forms[c.name]?.[f.key] || ''}
                                onChange={e => updateField(c.name, f.key, e.target.value)}
                                placeholder={f.secret ? '••••••••' : `Enter ${f.label.toLowerCase()}`}
                                style={{ width: '100%' }}
                              />
                            )}
                          </div>
                        ))}
                      </>
                    )}

                    {status !== 'idle' && (
                      <div role="status" style={{
                        display: 'flex', alignItems: 'center', gap: 6, marginBottom: 12,
                        fontSize: 'var(--fs-sm)', color: statusColor(status),
                      }}>
                        <span style={{ width: 8, height: 8, borderRadius: '50%', backgroundColor: statusColor(status), flex: '0 0 auto' }} />
                        {status === 'testing' ? 'Testing connection…' : testMessage[c.name]}
                      </div>
                    )}

                    {testDetail[c.name] && (status === 'success' || status === 'error') && (
                      <details style={{ marginBottom: 12, fontSize: 'var(--fs-sm)' }}>
                        <summary style={{ cursor: 'pointer', color: 'var(--text-soft)', marginBottom: 6 }}>
                          Show request &amp; response
                        </summary>
                        {testDetail[c.name]?.rendered_request && (
                          <div style={{ marginBottom: 8 }}>
                            <div className="n-eyebrow" style={{ marginBottom: 4 }}>Request</div>
                            <pre style={preStyle}>
                              {JSON.stringify(testDetail[c.name]?.rendered_request, null, 2)}
                            </pre>
                          </div>
                        )}
                        {testDetail[c.name]?.response && (
                          <div>
                            <div className="n-eyebrow" style={{ marginBottom: 4 }}>Response</div>
                            <pre style={preStyle}>
                              {JSON.stringify(testDetail[c.name]?.response, null, 2)}
                            </pre>
                          </div>
                        )}
                      </details>
                    )}

                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                      {c.auth_type === 'oauth2' ? (
                        <>
                          {!c.oauth_connected ? (
                            <Button variant="primary" size="sm" onClick={() => handleOAuthConnect(c.name)}>
                              Connect with OAuth
                            </Button>
                          ) : (
                            <Button variant="danger" size="sm" onClick={() => handleOAuthDisconnect(c.name)}>
                              Disconnect
                            </Button>
                          )}
                          {/* Save non-secret fields if any exist */}
                          {c.fields.some(f => !f.secret) && (
                            <Button size="sm" onClick={() => handleSave(c.name)} disabled={saving[c.name]}>
                              {saving[c.name] ? 'Saving…' : 'Save'}
                            </Button>
                          )}
                        </>
                      ) : (
                        <>
                          <Button size="sm" onClick={() => handleTest(c.name)} disabled={status === 'testing'}>
                            Test
                          </Button>
                          <Button variant="primary" size="sm" onClick={() => handleSave(c.name)} disabled={saving[c.name]}>
                            {saving[c.name] ? 'Saving…' : 'Save'}
                          </Button>
                          {c.configured && (
                            <Button variant="danger" size="sm" onClick={() => handleDelete(c.name)}>
                              Remove
                            </Button>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                );
              })}
              </section>}
            </>
          )}

          {/* ============ AGENTS TAB ============ */}
          {tab === 'agents' && <AgentsPanel />}

          {/* ============ BILLING TAB ============ */}
          {tab === 'billing' && orgId && <BillingTab orgId={orgId} />}

          {/* ============ EMAIL TAB ============ */}
          {tab === 'email' && <EmailTab canManage={can(storedUser, 'email:manage')} canSendTest={isAdmin} />}

          {/* ============ DEPLOYMENTS TAB ============ */}
          {tab === 'deployments' && <DeploymentsPanel />}

          {/* ============ TESTS TAB ============ */}
          {tab === 'tests' && <TestsPanel />}

          {/* ============ ROLES TAB ============ */}
          {tab === 'roles' && orgId && <RolesPanel orgId={orgId} />}

          {/* ============ SECRETS TAB ============ */}
          {tab === 'components' && <ComponentsPanel />}
          {tab === 'playbooks' && <PlaybooksPanel />}
          {tab === 'supplier-specs' && <SupplierSpecsPanel />}
          {tab === 'templates' && <TemplatesPanel />}
          {tab === 'secrets' && <SecretsPanel />}
        </div>
      </div>
    </div>
  );
}
