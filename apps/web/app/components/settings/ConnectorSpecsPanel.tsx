'use client';

import { useState, useEffect, useCallback } from 'react';
import { Plus, Sparkles, X } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import type { ConnectorSpecSummary, ConnectorSpecFull } from '../../types';
import ConnectorSpecEditor from './ConnectorSpecEditor';
import Button from '../ui/Button';
import IconButton from '../ui/IconButton';
import Badge, { type BadgeTone } from '../ui/Badge';
import PageState from '../ui/PageState';

type ViewMode = 'list' | 'create' | 'edit';

export default function ConnectorSpecsPanel({ onViewModeChange }: { onViewModeChange?: (isEditing: boolean) => void } = {}) {
  const [specs, setSpecs] = useState<ConnectorSpecSummary[]>([]);
  // connection name -> marketplace Apps that declare it (the app lens)
  const [usedBy, setUsedBy] = useState<Record<string, string[]>>({});

  useEffect(() => {
    apiFetch('/api/marketplace')
      .then(r => (r.ok ? r.json() : { apps: [] }))
      .then((d: { apps?: { name: string; composition?: { connections?: string[] } }[] }) => {
        const map: Record<string, string[]> = {};
        for (const a of d.apps ?? []) {
          for (const c of a.composition?.connections ?? []) (map[c] ??= []).push(a.name);
        }
        setUsedBy(map);
      })
      .catch(() => {});
  }, []);
  const [viewMode, setViewMode] = useState<ViewMode>('list');
  const [editingSpec, setEditingSpec] = useState<ConnectorSpecFull | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  useEffect(() => {
    onViewModeChange?.(viewMode !== 'list');
  }, [viewMode, onViewModeChange]);

  // Error banner state
  const [errorBanner, setErrorBanner] = useState<string | null>(null);

  // AI Generate state
  const [generateOpen, setGenerateOpen] = useState(false);
  const [generateDocs, setGenerateDocs] = useState('');
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [generateTarget, setGenerateTarget] = useState<string>('');  // '' = new, connector_name = append

  const fetchSpecs = useCallback(async () => {
    try {
      const res = await apiFetch('/api/connector-specs');
      if (!res.ok) { setErrorBanner(`Failed to fetch specs (${res.status})`); return; }
      const data = await res.json();
      setSpecs(data.specs ?? data);
    } catch (err) {
      setErrorBanner(`Network error loading specs: ${err instanceof Error ? err.message : String(err)}`);
    }
  }, []);

  // Display only: until the first fetch settles, show "Loading", not "No specs".
  const [loaded, setLoaded] = useState(false);
  useEffect(() => { fetchSpecs().finally(() => setLoaded(true)); }, [fetchSpecs]);

  const handleDelete = async (name: string) => {
    if (!confirm(`Delete connection spec "${name}"?`)) return;
    setDeleting(name);
    try {
      const res = await apiFetch(`/api/connector-specs/${name}`, { method: 'DELETE' });
      if (!res.ok) { setErrorBanner(`Failed to delete "${name}" (${res.status})`); }
      await fetchSpecs();
    } catch (err) {
      setErrorBanner(`Network error deleting "${name}": ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setDeleting(null);
    }
  };

  const handleEdit = async (name: string) => {
    try {
      const res = await apiFetch(`/api/connector-specs/${name}`);
      if (!res.ok) { setErrorBanner(`Failed to load spec "${name}" (${res.status})`); return; }
      const data = await res.json();
      setEditingSpec(data);
      setViewMode('edit');
    } catch (err) {
      setErrorBanner(`Network error loading spec: ${err instanceof Error ? err.message : String(err)}`);
    }
  };


  const handleGenerate = async () => {
    setGenerating(true);
    setGenerateError(null);
    try {
      const res = await apiFetch('/api/connector-specs/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_docs: generateDocs }),
      });
      if (!res.ok) {
        const errBody = await res.text();
        setGenerateError(`Generation failed (${res.status}): ${errBody}`);
        return;
      }
      const data = await res.json();
      const generated = data.spec ?? data;

      if (generateTarget) {
        // Append to existing connector
        const existingRes = await apiFetch(`/api/connector-specs/${generateTarget}`);
        if (!existingRes.ok) {
          setGenerateError(`Failed to fetch existing spec "${generateTarget}"`);
          return;
        }
        const existing: ConnectorSpecFull = await existingRes.json();
        const newTools = generated.tools ?? [];
        setEditingSpec({
          ...existing,
          tools: [...existing.tools, ...newTools],
        });
        setViewMode('edit');
      } else {
        // Create new connector
        setEditingSpec({
          id: '',
          version: 1,
          enabled: true,
          created_at: '',
          updated_at: null,
          connector_name: '',
          display_name: '',
          category: null,
          execution_mode: 'template',
          auth_type: 'bearer',
          auth_config: {},
          base_url_template: null,
          tools: [],
          api_documentation: null,
          example_requests: [],
          credential_fields: [],
          oauth_config: null,
          test_request: null,
          ...generated,
        });
        setViewMode('create');
      }
      setGenerateOpen(false);
      setGenerateDocs('');
      setGenerateTarget('');
    } catch (err) {
      setGenerateError(`Network error: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setGenerating(false);
    }
  };

  const handleSave = async (spec: ConnectorSpecFull, isNew: boolean) => {
    const url = isNew ? '/api/connector-specs' : `/api/connector-specs/${spec.connector_name}`;
    const method = isNew ? 'POST' : 'PUT';
    try {
      const res = await apiFetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(spec),
      });
      if (res.ok) {
        if (isNew) {
          // After creating, switch to editing the new spec
          const saved = await apiFetch(`/api/connector-specs/${spec.connector_name}`);
          if (saved.ok) {
            const data = await saved.json();
            setEditingSpec(data);
            setViewMode('edit');
          } else {
            setViewMode('list');
            setEditingSpec(null);
          }
        } else {
          // After updating, stay on the same spec
          const refreshed = await apiFetch(`/api/connector-specs/${spec.connector_name}`);
          if (refreshed.ok) {
            const data = await refreshed.json();
            setEditingSpec(data);
          }
        }
        await fetchSpecs();
      } else {
        const errBody = await res.text();
        setErrorBanner(`Failed to save spec (${res.status}): ${errBody}`);
      }
    } catch (err) {
      setErrorBanner(`Network error saving spec: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleCancel = () => {
    setViewMode('list');
    setEditingSpec(null);
  };

  // --- Editor view ---
  if (viewMode === 'create' || viewMode === 'edit') {
    return (
      <ConnectorSpecEditor
        spec={editingSpec}
        isNew={viewMode === 'create'}
        onSave={handleSave}
        onCancel={handleCancel}
      />
    );
  }

  // --- List view ---
  return (
    <>
      {errorBanner && (
        <div style={{ marginBottom: 16 }}>
          <PageState
            kind="error"
            title={errorBanner}
            action={<IconButton icon={X} label="Dismiss" iconSize={16} onClick={() => setErrorBanner(null)} style={{ margin: '-6px -6px -6px 0' }} />}
          />
        </div>
      )}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, minWidth: 0 }}>
          <h3 style={{ margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>
            Connection specs
          </h3>
          {specs.length > 0 && (
            <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
              {specs.length} {specs.length === 1 ? 'spec' : 'specs'}
            </span>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Button icon={Sparkles} onClick={() => setGenerateOpen(true)}>
            AI generate
          </Button>
          <Button variant="primary" icon={Plus} onClick={() => { setEditingSpec(null); setViewMode('create'); }}>
            New spec
          </Button>
        </div>
      </div>

      {/* AI Generate Modal */}
      {generateOpen && (
        <div className="n-card" style={{ padding: 16, marginBottom: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            <span style={{ fontWeight: 600, fontSize: 'var(--fs-md)', color: 'var(--text)' }}>Generate from API docs</span>
            <IconButton icon={X} label="Close" onClick={() => { setGenerateOpen(false); setGenerateTarget(''); setGenerateError(null); }} />
          </div>
          <textarea
            className="n-input"
            aria-label="API documentation"
            value={generateDocs}
            onChange={e => setGenerateDocs(e.target.value)}
            rows={10}
            placeholder="Paste API documentation here..."
            style={{
              display: 'block',
              width: '100%',
              fontSize: 'var(--fs-sm)',
              fontFamily: 'var(--font-mono)',
              lineHeight: 1.5,
              marginBottom: 12,
            }}
          />
          <div style={{ marginBottom: 12 }}>
            <label className="n-label" htmlFor="connector-generate-target">
              Target
            </label>
            <select
              id="connector-generate-target"
              className="n-select"
              value={generateTarget}
              onChange={e => setGenerateTarget(e.target.value)}
              style={{ width: '100%' }}
            >
              <option value="">New connector</option>
              {specs.map(s => (
                <option key={s.connector_name} value={s.connector_name}>
                  Append to: {s.display_name}
                </option>
              ))}
            </select>
          </div>
          <Button
            variant="primary"
            onClick={handleGenerate}
            disabled={generating || !generateDocs.trim()}
          >
            {generating ? 'Generating...' : 'Generate'}
          </Button>
          {generateError && (
            <p role="alert" style={{ color: 'var(--error)', fontSize: 'var(--fs-sm)', marginTop: 8, marginBottom: 0 }}>
              {generateError}
            </p>
          )}
        </div>
      )}

      {/* A failed load shows the error above, never "no specs". */}
      {specs.length === 0 && !errorBanner && (
        loaded
          ? <PageState kind="empty" title="No connection specs defined yet." />
          : <PageState kind="loading" title="Loading connection specs…" />
      )}

      {specs.map(spec => (
        <div key={spec.connector_name} className="n-card" style={{ padding: 16, marginBottom: 12 }}>
          {/* Header */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 8 }}>
            <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 6, minWidth: 0 }}>
              <span style={{ fontWeight: 600, fontSize: 'var(--fs-md)', color: 'var(--text)', marginRight: 2 }}>{spec.display_name}</span>
              <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginRight: 2 }}>{spec.connector_name}</span>
              {(usedBy[spec.connector_name] ?? []).length > 0 && (
                <Badge title="marketplace Apps that use this connection">
                  Used by {usedBy[spec.connector_name].join(', ')}
                </Badge>
              )}
              {spec.category && <Badge>{spec.category}</Badge>}
              <Badge>{spec.execution_mode}</Badge>
              {!spec.enabled && <Badge tone="warn">Disabled</Badge>}
            </div>
            <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', whiteSpace: 'nowrap' }}>v{spec.version}</span>
          </div>

          {/* Info */}
          <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', marginBottom: 12 }}>
            Auth: {spec.auth_type}
          </div>

          {/* OAuth Config (if present) */}
          {!!(spec as unknown as Record<string, unknown>).oauth_config && (() => {
            const oauth = (spec as unknown as Record<string, unknown>).oauth_config as Record<string, string>;
            return (
              <div style={{
                fontSize: 'var(--fs-sm)',
                color: 'var(--text)',
                marginBottom: 12,
                padding: '10px 12px',
                backgroundColor: 'var(--surface)',
                border: '1px solid var(--line)',
                borderRadius: 'var(--radius)',
                overflowWrap: 'anywhere',
              }}>
                <div style={{ fontWeight: 600, marginBottom: 4, color: 'var(--text)' }}>OAuth config</div>
                {oauth.authorize_url && <div><span style={{ color: 'var(--muted)' }}>Authorize URL:</span> {oauth.authorize_url}</div>}
                {oauth.token_url && <div><span style={{ color: 'var(--muted)' }}>Token URL:</span> {oauth.token_url}</div>}
                {oauth.client_id && <div><span style={{ color: 'var(--muted)' }}>Client ID:</span> {oauth.client_id}</div>}
                {oauth.scopes && <div><span style={{ color: 'var(--muted)' }}>Scopes:</span> {oauth.scopes}</div>}
              </div>
            );
          })()}

          {/* Per-venue OAuth connections (only for oauth2 specs) */}
          {spec.auth_type === 'oauth2' && (
            <VenueOAuthSection connectorName={spec.connector_name} />
          )}

          {/* Actions */}
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <Button size="sm" onClick={() => handleEdit(spec.connector_name)}>
              Edit
            </Button>
            <Button
              variant="danger"
              size="sm"
              onClick={() => handleDelete(spec.connector_name)}
              disabled={deleting === spec.connector_name}
            >
              {deleting === spec.connector_name ? 'Deleting...' : 'Delete'}
            </Button>
          </div>

        </div>
      ))}
    </>
  );
}

// ─── Per-venue OAuth connection panel ────────────────────────────────────────

interface VenueOAuthRow {
  venue_id: string;
  venue_name: string;
  connected: boolean;
  expired: boolean;
  has_refresh_token: boolean;
  expires_at: string | null;
}

function VenueOAuthSection({ connectorName }: { connectorName: string }) {
  const [rows, setRows] = useState<VenueOAuthRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);  // venue_id currently in flight
  const [error, setError] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await apiFetch(`/api/oauth/venues/${connectorName}`);
      if (!res.ok) {
        setError(`Failed to load venue status (${res.status})`);
        return;
      }
      const data = await res.json();
      setRows(data.venues || []);
      setError(null);
    } catch (err) {
      setError(`Network error: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLoading(false);
    }
  }, [connectorName]);

  useEffect(() => { fetchStatus(); }, [fetchStatus]);

  // Listen for postMessage from the OAuth callback popup
  useEffect(() => {
    const handler = (event: MessageEvent) => {
      const msg = event.data;
      if (msg?.type === 'oauth-complete') {
        // Refresh status — the callback HTML already closed itself
        fetchStatus();
        setBusy(null);
      }
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, [fetchStatus]);

  const handleConnect = async () => {
    // OAuth flow: no venue_id passed — LoadedHub returns it after the user picks
    // their venue inside the LoadedHub authorize page. Backend matches it to a
    // Norm venue via x_loaded_company_id.
    setBusy('__connecting__');
    setError(null);
    try {
      const res = await apiFetch(`/api/oauth/authorize/${connectorName}`);
      if (!res.ok) {
        const text = await res.text();
        setError(`Failed to start OAuth: ${text.slice(0, 200)}`);
        setBusy(null);
        return;
      }
      const { authorize_url } = await res.json();
      const popup = window.open(
        authorize_url,
        'oauth-popup',
        'width=600,height=700,resizable=yes,scrollbars=yes'
      );
      if (!popup) {
        setError('Popup was blocked. Please allow popups for this site.');
        setBusy(null);
        return;
      }
      const pollClosed = setInterval(() => {
        if (popup.closed) {
          clearInterval(pollClosed);
          fetchStatus();
          setBusy(null);
        }
      }, 1000);
    } catch (err) {
      setError(`Error: ${err instanceof Error ? err.message : String(err)}`);
      setBusy(null);
    }
  };

  const handleDisconnect = async (venueId: string, venueName: string) => {
    if (!confirm(`Disconnect ${connectorName} for ${venueName}?`)) return;
    setBusy(venueId);
    try {
      const res = await apiFetch(
        `/api/oauth/disconnect/${connectorName}?venue_id=${encodeURIComponent(venueId)}`,
        { method: 'POST' }
      );
      if (!res.ok) setError(`Failed to disconnect (${res.status})`);
      await fetchStatus();
    } finally {
      setBusy(null);
    }
  };

  if (loading) {
    return (
      <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', marginBottom: 12 }}>
        Loading venue connections...
      </div>
    );
  }

  return (
    <div style={{
      fontSize: 'var(--fs-sm)',
      color: 'var(--text-soft)',
      marginBottom: 12,
      padding: '10px 12px',
      backgroundColor: 'var(--surface)',
      border: '1px solid var(--line)',
      borderRadius: 'var(--radius)',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
        <div style={{ fontWeight: 600, color: 'var(--text)' }}>
          Per-venue OAuth connections
        </div>
        <Button
          size="sm"
          onClick={handleConnect}
          disabled={busy === '__connecting__'}
          title="Open the OAuth flow. The venue is selected inside LoadedHub during authorization."
        >
          {busy === '__connecting__' ? 'Opening OAuth...' : 'Connect a venue'}
        </Button>
      </div>
      {error && (
        <div role="alert" style={{ color: 'var(--error)', marginBottom: 6 }}>{error}</div>
      )}
      <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginBottom: 8 }}>
        Click <strong>Connect a venue</strong> to start the OAuth flow.
        The venue is selected inside LoadedHub; tokens are stored against the matching
        Norm venue using the x_loaded_company_id mapping.
      </div>
      {rows.length === 0 ? (
        <div style={{ color: 'var(--muted)' }}>No venues found.</div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <tbody>
            {rows.map(r => {
              const status: { label: string; tone: BadgeTone } = !r.connected
                ? { label: 'Not connected', tone: 'neutral' }
                : r.expired && !r.has_refresh_token
                ? { label: 'Expired', tone: 'error' }
                : r.expired && r.has_refresh_token
                ? { label: 'Connected (auto-refresh)', tone: 'warn' }
                : { label: 'Connected', tone: 'ok' };
              return (
                <tr key={r.venue_id} style={{ borderTop: '1px solid var(--line)' }}>
                  <td style={{ padding: '6px 4px', fontWeight: 500, color: 'var(--text)' }}>{r.venue_name}</td>
                  <td style={{ padding: '6px 4px' }}>
                    <Badge tone={status.tone}>{status.label}</Badge>
                  </td>
                  <td style={{ padding: '6px 4px', textAlign: 'right' }}>
                    {r.connected && (
                      <Button
                        variant="danger"
                        size="sm"
                        onClick={() => handleDisconnect(r.venue_id, r.venue_name)}
                        disabled={busy === r.venue_id}
                      >
                        Disconnect
                      </Button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
