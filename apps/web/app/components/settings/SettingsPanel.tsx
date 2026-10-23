'use client';

import { useState, useEffect, useCallback, useId, useRef } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { SIDE_PANEL_EVENT, type SidePanelRequest } from '../../lib/sidePanel';
import { ChevronDown, ChevronRight, Plus } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import type { VenueDetail, Organization } from '../../types';
import ConnectorSpecsPanel from './ConnectorSpecsPanel';
import BillingTab from './BillingTab';
import ConnectionsMatrix from './ConnectionsMatrix';
import BuiltInConnections from './BuiltInConnections';
import AppMapPanel from './AppMapPanel';
import EmailTab from './EmailTab';
import DeploymentsPanel from './DeploymentsPanel';
import TestsPanel from './TestsPanel';
import RolesPanel from './RolesPanel';
import PeoplePanel from './PeoplePanel';
import SecretsPanel from './SecretsPanel';
import ComponentsPanel from './ComponentsPanel';
import PlaybooksPanel from './PlaybooksPanel';
import SupplierSpecsPanel from './SupplierSpecsPanel';
import InvoiceActivityPanel from './InvoiceActivityPanel';
import UnitCheckPanel from './UnitCheckPanel';
import AutopilotReportPanel from './AutopilotReportPanel';
import TemplatesPanel from './TemplatesPanel';
import AgentsPanel from './AgentsPanel';
import McpPanel from './McpPanel';
import AdminThreadsPanel from './AdminThreadsPanel';
import ApprovalPreferences from './ApprovalPreferences';
import MemoryTab from './MemoryTab';
import TabletPinCard from './TabletPinCard';
import SuggestionsAdminPanel from './SuggestionsAdminPanel';
import CompanySettingsTab from './CompanySettingsTab';
import TeamSection from '../team/TeamSection';
import AddressSearch from './AddressSearch';
import OrganisationsPanel from './OrganisationsPanel';
import PasswordLoginForm, { type AccountChoice } from '../display/PasswordLoginForm';
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
import PageState from '../ui/PageState';
import { ConfirmDialog } from '../ui/Dialog';
import SubNav, { type SubNavGroup } from '../ui/SubNav';

interface ConnectorField {
  key: string;
  label: string;
  secret: boolean;
  type?: string;
  options?: { id: string; label: string }[];
  default?: string;
  /** What the setting is for, under its label. */
  description?: string;
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

/** Admins only, and only on this browser: show each tool call's request and
 *  response in a conversation (ThreadDetail reads the same flag). */
function ToolDetailsToggle() {
  const [on, setOn] = useState<boolean>(() => {
    try { return localStorage.getItem('norm_show_tool_details') !== 'false'; } catch { return true; }
  });
  return (
    <div className="n-card" style={{ display: 'flex', alignItems: 'center', gap: '4px 12px', flexWrap: 'wrap', padding: '10px 16px' }}>
      <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-base)', color: 'var(--text)', cursor: 'pointer' }}>
        <input
          type="checkbox"
          checked={on}
          onChange={e => {
            setOn(e.target.checked);
            try { localStorage.setItem('norm_show_tool_details', String(e.target.checked)); } catch { /* private mode */ }
          }}
          style={{ cursor: 'pointer', accentColor: 'var(--accent)' }}
        />
        Show tool call details in conversations
      </label>
      <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
        Each tool call’s request and response under Norm’s answers — on this browser, for platform admins.
      </span>
    </div>
  );
}

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
  /** A password login reaching several accounts (Loaded Legacy: the
   *  person's Loaded companies): the one this venue works on, and what to
   *  call one. */
  account?: string | null;
  account_choice?: AccountChoice | null;
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
  const [connectorError, setConnectorError] = useState<string | null>(null);
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

  // A refused save says why (a password login is checked before it's kept).
  const handleSaveConnector = async (connectorName: string) => {
    setSavingConnector(connectorName);
    setConnectorError(null);
    try {
      const res = await apiFetch(`/api/connectors/${connectorName}`, {
        method: 'PUT',
        body: JSON.stringify({ config: connectorForms[connectorName] || {}, venue_id: venue.id }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        setConnectorError(typeof d.detail === 'string' ? d.detail : `Couldn't save (${res.status})`);
        return;
      }
      setEditingConnector(null);
      await loadConnectors();
    } catch (e) {
      setConnectorError(e instanceof Error ? e.message : String(e));
    } finally {
      setSavingConnector(null);
    }
  };

  const toggleEditing = (connectorName: string) => {
    setConnectorError(null);
    setEditingConnector(editingConnector === connectorName ? null : connectorName);
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
              // A username-and-password login Norm checks before keeping
              // (Loaded Legacy): its own status, Connect / Replace login, and
              // the same form as Settings → Connections (PasswordLoginForm).
              const isLogin = c.auth_type === 'password_login';
              const loginStatus = !c.configured || !c.enabled ? null
                : c.needs_reconnect ? <Badge tone="warn">Needs attention</Badge>
                : <Badge tone="ok">Connected</Badge>;
              return (
                <div key={c.name} style={i > 0 ? { borderTop: '1px solid var(--line-soft)' } : undefined}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', padding: '8px 12px' }}>
                    <span style={{ flex: '1 1 140px', minWidth: 0, fontSize: 'var(--fs-base)', fontWeight: 500, color: 'var(--text)' }}>
                      {c.label}
                      {isLogin && c.account && (
                        <span style={{ display: 'block', fontSize: 'var(--fs-xs)', fontWeight: 400, color: 'var(--muted)' }}>
                          Works on {c.account}{c.account_choice ? ` (${c.account_choice.label})` : ''}
                        </span>
                      )}
                      {isLogin && c.needs_reconnect && c.last_auth_error && (
                        <span style={{ display: 'block', fontSize: 'var(--fs-xs)', fontWeight: 400, color: 'var(--error)' }}>{c.last_auth_error}</span>
                      )}
                    </span>
                    {/* Without settings:connectors, the status alone. */}
                    {!canConnect ? (
                      isLogin ? (loginStatus ?? <Badge>Not connected</Badge>)
                      : c.auth_type === 'oauth2' ? (
                        c.oauth_connected && c.needs_reconnect ? <Badge tone="error">Reconnect needed</Badge>
                          : c.oauth_connected ? <Badge tone="ok">Connected</Badge>
                          : <Badge>Not connected</Badge>
                      ) : c.configured ? <Badge tone="ok">Configured</Badge> : <Badge>Not configured</Badge>
                    ) : isLogin ? (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        {loginStatus}
                        <Button
                          variant={isEditing ? 'quiet' : c.configured ? 'secondary' : 'primary'}
                          size="sm"
                          onClick={(e) => { e.stopPropagation(); toggleEditing(c.name); }}
                        >
                          {isEditing ? 'Cancel' : c.configured ? 'Replace login' : 'Connect'}
                        </Button>
                      </div>
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
                        <Button size="sm" onClick={(e) => { e.stopPropagation(); toggleEditing(c.name); }}>
                          {c.configured ? 'Edit' : 'Configure'}
                        </Button>
                      </div>
                    )}
                  </div>

                  {isLogin && isEditing && (
                    <div style={{ padding: '10px 12px 12px', borderTop: '1px solid var(--line-soft)', backgroundColor: 'var(--surface)' }}>
                      <PasswordLoginForm
                        connectorName={c.name}
                        venueId={venue.id}
                        fields={fields}
                        initial={c.config}
                        accountChoice={c.account_choice}
                        account={c.account}
                        onSaved={() => { setEditingConnector(null); loadConnectors(); }}
                        onCancel={() => setEditingConnector(null)}
                      />
                    </div>
                  )}

                  {/* Credential form */}
                  {!isLogin && isEditing && fields.length > 0 && (
                    <form
                      onSubmit={(e) => { e.preventDefault(); handleSaveConnector(c.name); }}
                      style={{
                        display: 'flex', flexDirection: 'column', gap: 8, padding: '10px 12px 12px',
                        borderTop: '1px solid var(--line-soft)', backgroundColor: 'var(--surface)',
                      }}
                    >
                      {fields.map(f => (
                        <div key={f.key}>
                          <label className="n-label" htmlFor={`${fieldId}-${c.name}-${f.key}`}>{f.label}</label>
                          <input
                            id={`${fieldId}-${c.name}-${f.key}`}
                            className="n-input"
                            type={f.secret ? 'password' : 'text'}
                            autoComplete={f.secret ? 'new-password' : 'off'}
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
                      {connectorError && (
                        <div role="alert" style={{ fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>{connectorError}</div>
                      )}
                      <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
                        <Button variant="primary" size="sm" type="submit" onClick={(e) => e.stopPropagation()}
                          disabled={savingConnector === c.name}>
                          {savingConnector === c.name ? 'Saving…' : 'Save'}
                        </Button>
                        <Button size="sm" onClick={(e) => { e.stopPropagation(); setEditingConnector(null); }}>Cancel</Button>
                      </div>
                    </form>
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

// --- Billing: the Ask Norm cap for venue staff ---

/**
 * "Ask Norm for venue staff: $__ per person per month." A chat whose role
 * reaches no AI agent counts against this cap; 0 switches staff chat off and
 * an empty field goes back to Norm's default. Saved with
 * PUT /api/organizations/{id} {staff_chat_cap_cents} (billing:manage or
 * org:manage); anyone who can read billing sees the figure.
 */
function StaffChatCapCard({ orgId, canEdit }: { orgId: string; canEdit: boolean }) {
  const fieldId = useId();
  const [capCents, setCapCents] = useState<number | null>(null);
  const [defaultCents, setDefaultCents] = useState<number | null>(null);
  const [draft, setDraft] = useState('');
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const dollars = (cents: number) => (cents / 100).toFixed(2);

  useEffect(() => {
    let cancelled = false;
    apiFetch(`/api/organizations/${orgId}`).then(r => (r.ok ? r.json() : null)).then(d => {
      if (cancelled || !d) return;
      const cap = typeof d.staff_chat_cap_cents === 'number' ? d.staff_chat_cap_cents : null;
      const def = typeof d.staff_chat_cap_default_cents === 'number' ? d.staff_chat_cap_default_cents : null;
      setCapCents(cap);
      setDefaultCents(def);
      setDraft(cap === null ? '' : dollars(cap));
      setLoaded(true);
    }).catch(() => setLoaded(true));
    return () => { cancelled = true; };
  }, [orgId]);

  const effective = capCents ?? defaultCents;
  const parsed = draft.trim() === '' ? null : Math.round(Number(draft) * 100);
  const valid = parsed === null || (Number.isFinite(parsed) && parsed >= 0);
  const dirty = parsed !== capCents;

  const save = async () => {
    if (!valid) return;
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const res = await apiFetch(`/api/organizations/${orgId}`, { method: 'PUT', body: JSON.stringify({ staff_chat_cap_cents: parsed }) });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        setError(typeof body?.detail === 'string' ? body.detail : `The cap wasn’t saved (error ${res.status}).`);
        return;
      }
      const d = await res.json().catch(() => null);
      const cap = d && typeof d.staff_chat_cap_cents === 'number' ? d.staff_chat_cap_cents : parsed;
      setCapCents(cap);
      setDraft(cap === null ? '' : dollars(cap));
      setSaved(true);
    } catch {
      setError('The cap wasn’t saved — check your connection and try again.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <section style={{ marginBottom: 28 }} data-testid="staff-chat-cap">
      <SectionHeader title="Ask Norm for venue staff" />
      <div className="n-card" style={{ padding: 16 }}>
        <p style={{ margin: '0 0 12px', fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>
          Venue staff whose role reaches no AI agent can still ask Norm, on a lighter model, up to this much usage each per month. It counts toward the organisation’s allowance. Setting it to 0 switches Ask Norm off for them; leaving it empty uses Norm’s default
          {defaultCents !== null ? ` of $${dollars(defaultCents)}` : ''}.
        </p>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <label className="n-label" htmlFor={fieldId} style={{ margin: 0 }}>$</label>
          <input
            id={fieldId}
            className="n-input"
            type="number"
            min={0}
            step="0.01"
            inputMode="decimal"
            value={draft}
            disabled={!loaded || !canEdit || saving}
            onChange={e => { setDraft(e.target.value); setSaved(false); setError(null); }}
            placeholder={defaultCents !== null ? dollars(defaultCents) : ''}
            aria-label="Ask Norm cap per person per month, in dollars"
            style={{ width: 110, textAlign: 'right' }}
            data-testid="staff-chat-cap-input"
          />
          <span style={{ fontSize: 'var(--fs-base)', color: 'var(--text)' }}>per person per month</span>
          {canEdit && (
            <Button variant="primary" size="sm" onClick={save} disabled={!loaded || saving || !valid || !dirty} data-testid="staff-chat-cap-save">
              {saving ? 'Saving…' : 'Save'}
            </Button>
          )}
          {saved && <span role="status" style={{ fontSize: 'var(--fs-sm)', color: 'var(--ok)' }}>Saved</span>}
        </div>
        {!valid && <p role="alert" style={{ margin: '8px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>Enter 0 or more.</p>}
        {error && <p role="alert" style={{ margin: '8px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>{error}</p>}
        {loaded && effective !== null && (
          <p style={{ margin: '8px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
            {effective === 0
              ? 'Ask Norm is switched off for venue staff.'
              : `Each venue staff member can use up to $${dollars(effective)} a month${capCents === null ? ' (Norm’s default)' : ''}.`}
          </p>
        )}
      </div>
    </section>
  );
}

export type SettingsTab = 'organisations' | 'app-map' | 'connections' | 'connectors' | 'agents' | 'components' | 'playbooks' | 'supplier-specs' | 'invoice-activity' | 'unit-check' | 'autopilot-readiness' | 'templates' | 'venues' | 'members' | 'company' | 'billing' | 'email' | 'deployments' | 'tests' | 'roles' | 'secrets' | 'threads' | 'mcp' | 'preferences' | 'suggestion-rules' | 'team';

function hasSettingsPermission(user: User | null, ...perms: string[]): boolean {
  return perms.some(p => can(user, p));
}

export default function SettingsPanel({ initialTab, initialView }: { initialTab?: SettingsTab; initialView?: string } = {}) {
  // Opens on a given tab when asked (the quota dialog's "Top up" → Billing).
  const [activeTab, setActiveTab] = useState<SettingsTab>(initialTab ?? 'preferences');
  // The section list folds away while a view needs the width (an invoice copy
  // beside its card) and comes back when the last such view closes.
  const foldRequests = useRef(0);
  const [navFolded, setNavFolded] = useState(false);
  useEffect(() => {
    const onRequest = (e: Event) => {
      const { collapse } = (e as CustomEvent<SidePanelRequest>).detail || { collapse: false };
      foldRequests.current = Math.max(0, foldRequests.current + (collapse ? 1 : -1));
      setNavFolded(foldRequests.current > 0);
    };
    window.addEventListener(SIDE_PANEL_EVENT, onRequest);
    return () => window.removeEventListener(SIDE_PANEL_EVENT, onRequest);
  }, []);
  const [orgId, setOrgId] = useState<string | null>(null);
  const storedUser = getStoredUser() as User | null;
  const isAdmin = storedUser?.role === 'admin';
  // Venue staff (a role with no AI agent): Settings is "just me" — the
  // Preferences tab only (docs/plans/staff-access.md, section 2).
  const isStaffUser = !!storedUser?.access?.staff;

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
  // The platform key waiting on its Remove confirmation.
  const [removingPlatform, setRemovingPlatform] = useState<ConnectorMeta | null>(null);

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

  useEffect(() => { fetchConnectors(); }, [fetchConnectors]);

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
      const detail = data.rendered_request || data.response
        ? { rendered_request: data.rendered_request, response: data.response }
        : null;
      if (res.ok && data.success) {
        setTestStatus(prev => ({ ...prev, [name]: 'success' }));
        setTestMessage(prev => ({ ...prev, [name]: data.message || 'Connected' }));
      } else {
        setTestStatus(prev => ({ ...prev, [name]: 'error' }));
        setTestMessage(prev => ({
          ...prev,
          [name]: data.error || (typeof data.detail === 'string' ? data.detail : '') || `Test failed (${res.status})`,
        }));
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
        // Saving keeps the switch where it is; a first save turns it on.
        body: JSON.stringify({
          config: forms[name] || {},
          enabled: connectors.find(c => c.name === name)?.configured
            ? connectors.find(c => c.name === name)?.enabled ?? true
            : true,
        }),
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
    // The company's money, dates and Functions switch (id kept: Functions' links open it).
    [!!orgId && !isStaffUser, 'company', 'General'],
    [!isStaffUser, 'venues', 'Venues'],
    // People: office users and venue staff (the tab id is kept for saved
    // tests). It lists everyone's email, phone and PIN status, so it is a
    // manager's page: org:members, which the API also asks for.
    [can(storedUser, 'org:members'), 'members', 'People'],
    // Hire and retire team members, switch their apps.
    [!!orgId && !isStaffUser, 'team', 'AI team'],
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
    [isAdmin, 'app-map', 'App map'],
    // How Home's suggestions are made — tuned site-wide (platform admins).
    [isAdmin, 'suggestion-rules', 'Suggestions'],
  ]);
  // Everything about how Norm reads and receives invoices, in one place
  // (owner, 19 Oct 2026). Ids kept: links and saved E2E tests use them.
  addGroup('Invoice Dojo', [
    [showSupplierSpecs, 'invoice-activity', 'Activity'],
    [showSupplierSpecs, 'unit-check', 'Daily unit check'],
    [showSupplierSpecs, 'supplier-specs', 'Supplier specs'],
    [showSupplierSpecs, 'autopilot-readiness', 'Autopilot readiness'],
  ]);
  addGroup('Platform', [
    // Every organisation, its venues and people — platform admins only.
    [isAdmin, 'organisations', 'Organisations'],
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
        {!isMobile && !navFolded && (
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
          {/* Venue staff: their own tablet PIN and what Norm has learned, nothing
              of the organisation's approval settings (plan section 2). */}
          {tab === 'preferences' && isStaffUser && (
            <>
              <TabletPinCard />
              <MemoryTab />
            </>
          )}
          {tab === 'preferences' && !isStaffUser && (
            <>
              {/* What Norm may do without asking — every write, receiving and
                  reconciling included. One home for one question. The
                  organisation's receiving rules sit under the receive card. */}
              <ApprovalPreferences />
              <div style={{ height: 32 }} />
              {/* The person's own kitchen-tablet PIN, when they have a staff
                  record in this organisation (plan sections 2 and 5). */}
              <TabletPinCard />
              {/* Knowledge and suggestion notes, one list. */}
              <MemoryTab />
              {isAdmin && (
                <div style={{ marginTop: 32 }}>
                  <SectionHeader title="Admin" />
                  <ToolDetailsToggle />
                </div>
              )}
            </>
          )}

          {/* ============ SUGGESTIONS TAB (platform admins) ============ */}
          {tab === 'suggestion-rules' && isAdmin && <SuggestionsAdminPanel />}

          {/* ============ VENUES TAB ============ */}
          {tab === 'venues' && <VenuesTab canManage={can(storedUser, 'org:venues')} canConnect={can(storedUser, 'settings:connectors')} />}

          {/* ============ PEOPLE TAB (id 'members') ============ */}
          {tab === 'members' && <PeoplePanel orgId={orgId} orgName={orgName} />}

          {/* ============ COMPANY TAB (currency, time zone, tax, Functions mode) ============ */}
          {tab === 'company' && orgId && <CompanySettingsTab orgId={orgId} />}
          {tab === 'team' && orgId && <TeamSection user={storedUser} />}

          {/* ============ CONNECTORS TAB ============ */}
          {tab === 'app-map' && isAdmin && <AppMapPanel />}
          {tab === 'connections' && (
            <div style={{ width: '100%' }}>
              <SectionHeader title="Connections" />
              <BuiltInConnections />
              <ConnectionsMatrix />
            </div>
          )}
          {tab === 'connectors' && (
            <>
              {/* The list renders its own tools-and-endpoints section under the specs. */}
              <ConnectorSpecsPanel onViewModeChange={setSpecEditing} />

              {!specEditing && <section style={{ marginTop: 32, paddingTop: 24, borderTop: '1px solid var(--line)' }}>
              <SectionHeader title="Platform key and models" />
              <p style={{ margin: '-4px 0 12px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
                The Anthropic key Norm uses everywhere, and which Claude models do the work.
              </p>
              {removingPlatform && (
                <ConfirmDialog
                  title={`Remove the saved ${removingPlatform.label} settings?`}
                  confirmLabel="Remove"
                  busyLabel="Removing…"
                  danger
                  onConfirm={async () => {
                    const res = await apiFetch(`/api/connectors/${removingPlatform.name}`, { method: 'DELETE' });
                    if (!res.ok) throw new Error(`Couldn't remove them (${res.status})`);
                    await fetchConnectors();
                    setTestStatus(prev => ({ ...prev, [removingPlatform.name]: 'idle' }));
                  }}
                  onClose={() => setRemovingPlatform(null)}
                >
                  <p style={{ margin: 0 }}>
                    The key and both model choices saved here are deleted, in this environment. Norm falls back to the
                    server’s own key and default models.
                  </p>
                </ConfirmDialog>
              )}
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
                      </div>
                    </div>

                    {c.configured && (
                      <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', margin: '-4px 0 12px' }}>
                        {c.enabled
                          ? 'Active: Norm uses the key and models saved here.'
                          : 'Inactive: Norm ignores what is saved here and uses the server’s own key and default models.'}
                      </div>
                    )}

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
                              (() => {
                                const value = forms[c.name]?.[f.key] || f.default || '';
                                // A saved model that is no longer offered: say so, rather
                                // than let the select show the first option instead.
                                const retired = !!value && !f.options.some(o => o.id === value);
                                return (
                                  <select
                                    id={`${fieldId}-${c.name}-${f.key}`}
                                    className="n-select"
                                    value={value}
                                    onChange={e => updateField(c.name, f.key, e.target.value)}
                                    style={{ width: '100%' }}
                                  >
                                    {retired && <option value={value}>{value} — no longer offered; Norm uses the default</option>}
                                    {f.options.map(opt => (
                                      <option key={opt.id} value={opt.id}>{opt.label}</option>
                                    ))}
                                  </select>
                                );
                              })()
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
                            {f.description && (
                              <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', marginTop: 4 }}>{f.description}</div>
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
                            <Button variant="danger" size="sm" onClick={() => setRemovingPlatform(c)}>
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
          {tab === 'billing' && orgId && (
            <>
              <BillingTab orgId={orgId} />
              <StaffChatCapCard orgId={orgId} canEdit={can(storedUser, 'billing:manage') || can(storedUser, 'org:manage')} />
            </>
          )}

          {/* ============ EMAIL TAB ============ */}
          {tab === 'email' && <EmailTab canManage={can(storedUser, 'email:manage')} canSendTest={isAdmin} />}

          {/* ============ DEPLOYMENTS TAB ============ */}
          {tab === 'organisations' && isAdmin && <OrganisationsPanel />}
          {tab === 'deployments' && <DeploymentsPanel />}

          {/* ============ TESTS TAB ============ */}
          {tab === 'tests' && <TestsPanel />}

          {/* ============ ROLES TAB ============ */}
          {tab === 'roles' && orgId && <RolesPanel orgId={orgId} />}

          {/* ============ SECRETS TAB ============ */}
          {tab === 'components' && <ComponentsPanel />}
          {tab === 'playbooks' && <PlaybooksPanel />}
          {tab === 'supplier-specs' && <SupplierSpecsPanel />}
          {tab === 'invoice-activity' && <InvoiceActivityPanel initialView={initialView} />}
          {tab === 'unit-check' && <UnitCheckPanel />}
          {tab === 'autopilot-readiness' && <AutopilotReportPanel />}
          {tab === 'templates' && <TemplatesPanel />}
          {tab === 'secrets' && <SecretsPanel />}
        </div>
      </div>
    </div>
  );
}
