'use client';

import { useState, useEffect, useCallback } from 'react';
import { Plus, Sparkles, Trash2, X } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import type { ConnectorSpecSummary, ConnectorSpecFull } from '../../types';
import ConnectorSpecEditor from './ConnectorSpecEditor';
import Button from '../ui/Button';
import IconButton from '../ui/IconButton';
import Badge from '../ui/Badge';
import PageState from '../ui/PageState';
import { ConfirmDialog } from '../ui/Dialog';
import ConsolidatorCoveragePanel, { type Coverage } from './ConsolidatorCoveragePanel';

type ViewMode = 'list' | 'create' | 'edit';

/** How a connector runs, in words. */
export const MODE_LABEL: Record<string, string> = {
  template: 'HTTP API',
  mcp: 'MCP',
  internal: 'Built into Norm',
  agent: 'Agent (legacy)',
};

/** How a connector signs in, in words. */
export const AUTH_LABEL: Record<string, string> = {
  none: 'No sign-in',
  basic: 'Username and password',
  bearer: 'Bearer token',
  api_key_header: 'API key in a header',
  api_key: 'API key',
  oauth2: 'OAuth',
  custom: 'Custom',
};

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
  // The spec whose delete is waiting on the confirmation dialog.
  const [deleting, setDeleting] = useState<ConnectorSpecSummary | null>(null);

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

  // Tools and endpoints per connector — each row's counts, and the section
  // under the list (totals, leaks, drifted code). Fetched again after a save.
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [coverageError, setCoverageError] = useState<string | null>(null);
  const fetchCoverage = useCallback(async () => {
    try {
      const res = await apiFetch('/api/connector-specs/coverage');
      if (!res.ok) throw new Error(`Error ${res.status}`);
      setCoverage(await res.json());
      setCoverageError(null);
    } catch (e) {
      setCoverageError(e instanceof Error ? e.message : 'Could not load coverage');
    }
  }, []);
  useEffect(() => { if (viewMode === 'list') void fetchCoverage(); }, [viewMode, fetchCoverage]);

  // Throws on failure: the confirmation dialog shows why and stays open.
  const handleDelete = async (name: string) => {
    let res: Response;
    try {
      res = await apiFetch(`/api/connector-specs/${name}`, { method: 'DELETE' });
    } catch (err) {
      throw new Error(`Network error deleting "${name}": ${err instanceof Error ? err.message : String(err)}`);
    }
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(typeof body?.detail === 'string' ? body.detail : `Failed to delete "${name}" (${res.status})`);
    }
    await fetchSpecs();
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
        // What it generates are API endpoints: they join the endpoints list
        // (a connector not yet split keeps one list, so they go there).
        const newRows = generated.endpoints ?? generated.tools ?? [];
        setEditingSpec(Array.isArray(existing.endpoints)
          ? { ...existing, endpoints: [...existing.endpoints, ...newRows] }
          : { ...existing, tools: [...existing.tools, ...newRows] });
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
          api_documentation: null,
          example_requests: [],
          credential_fields: [],
          oauth_config: null,
          test_request: null,
          ...generated,
          // A new connector starts split: the generated calls are endpoints,
          // and tools are built on them afterwards.
          tools: [],
          endpoints: generated.endpoints ?? generated.tools ?? [],
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
            Connector specs
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

      {deleting && (
        <ConfirmDialog
          title={`Delete ${deleting.display_name || deleting.connector_name}?`}
          confirmLabel="Delete spec"
          busyLabel="Deleting…"
          danger
          onConfirm={() => handleDelete(deleting.connector_name)}
          onClose={() => setDeleting(null)}
        >
          <p style={{ margin: 0 }}>
            The {deleting.connector_name} connector spec and its endpoints and tools are removed from the shared config,
            so no environment can use them. This can’t be undone.
          </p>
          {(usedBy[deleting.connector_name] ?? []).length > 0 && (
            <p style={{ margin: '8px 0 0', color: 'var(--error)' }}>
              Used by {usedBy[deleting.connector_name].join(', ')} — {usedBy[deleting.connector_name].length === 1 ? 'that App stops' : 'those Apps stop'} working.
            </p>
          )}
        </ConfirmDialog>
      )}

      {/* AI Generate Modal */}
      {generateOpen && (
        <div className="n-card" style={{ padding: 16, marginBottom: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            <span style={{ fontWeight: 600, fontSize: 'var(--fs-md)', color: 'var(--text)' }}>Generate endpoints from API docs</span>
            <IconButton icon={X} label="Close" onClick={() => { setGenerateOpen(false); setGenerateTarget(''); setGenerateError(null); }} />
          </div>
          <p style={{ margin: '0 0 8px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
            Creates API endpoints — one call each. An LLM never sees an endpoint: wrap them in a consolidator to make a tool.
          </p>
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
          ? <PageState kind="empty" title="No connector specs yet." />
          : <PageState kind="loading" title="Loading connector specs…" />
      )}

      {specs.length > 0 && (
        <div className="n-card" style={{ padding: 0, overflow: 'hidden' }}>
          {specs.map((spec, i) => {
            const cov = coverage?.connectors.find(c => c.connector === spec.connector_name);
            const tools = cov ? cov.counts.consolidator + cov.counts['built-in'] : 0;
            const apps = usedBy[spec.connector_name] ?? [];
            const perPerson = spec.category === 'email' && spec.auth_type === 'oauth2';
            const facts = [
              MODE_LABEL[spec.execution_mode] ?? spec.execution_mode,
              spec.auth_type === 'none' ? 'no sign-in' : `${AUTH_LABEL[spec.auth_type] ?? spec.auth_type} sign-in`,
              cov ? `${tools} ${tools === 1 ? 'tool' : 'tools'}` : null,
              cov && cov.counts.endpoint ? `${cov.counts.endpoint} ${cov.counts.endpoint === 1 ? 'endpoint' : 'endpoints'}` : null,
            ].filter(Boolean).join(' · ');
            return (
              <div key={spec.connector_name} style={{ padding: '12px 16px', borderTop: i ? '1px solid var(--line)' : 'none' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'baseline', flexWrap: 'wrap', gap: '2px 8px' }}>
                      <span style={{ fontWeight: 600, fontSize: 'var(--fs-md)', color: 'var(--text)' }}>{spec.display_name}</span>
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{spec.connector_name}</span>
                      {!spec.enabled && <Badge tone="warn">Disabled</Badge>}
                      {perPerson && <Badge title="Each person connects their own account in Settings → Email">Per person</Badge>}
                      {spec.category === '_platform' && <Badge title="One Norm-wide key">Platform</Badge>}
                      {cov && cov.leaks.length > 0 && <Badge tone="error">{cov.leaks.length} {cov.leaks.length === 1 ? 'leak' : 'leaks'}</Badge>}
                    </div>
                    <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)', overflowWrap: 'anywhere' }}>
                      {facts}
                      {apps.length > 0 && <> · used by <span style={{ color: 'var(--text-soft)' }}>{apps.join(', ')}</span></>}
                    </div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
                    <Button size="sm" onClick={() => handleEdit(spec.connector_name)}>Edit</Button>
                    <IconButton icon={Trash2} iconSize={16} label={`Delete ${spec.display_name || spec.connector_name}`} onClick={() => setDeleting(spec)} />
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {loaded && specs.length > 0 && <ConsolidatorCoveragePanel data={coverage} error={coverageError} />}
    </>
  );
}
