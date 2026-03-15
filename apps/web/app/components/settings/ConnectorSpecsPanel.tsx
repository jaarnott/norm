'use client';

import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../../lib/api';
import type { ConnectorSpecSummary, ConnectorSpecFull } from '../../types';
import ConnectorSpecEditor from './ConnectorSpecEditor';

type ViewMode = 'list' | 'create' | 'edit';

export default function ConnectorSpecsPanel() {
  const [specs, setSpecs] = useState<ConnectorSpecSummary[]>([]);
  const [viewMode, setViewMode] = useState<ViewMode>('list');
  const [editingSpec, setEditingSpec] = useState<ConnectorSpecFull | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  // Dry-run state
  const [dryRunOpen, setDryRunOpen] = useState<string | null>(null);
  const [dryRunAction, setDryRunAction] = useState('');
  const [dryRunFields, setDryRunFields] = useState('{}');
  const [dryRunResult, setDryRunResult] = useState<Record<string, unknown> | null>(null);
  const [dryRunLoading, setDryRunLoading] = useState(false);

  // AI Generate state
  const [generateOpen, setGenerateOpen] = useState(false);
  const [generateDocs, setGenerateDocs] = useState('');
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [generateTarget, setGenerateTarget] = useState<string>('');  // '' = new, connector_name = append

  const fetchSpecs = useCallback(async () => {
    try {
      const res = await apiFetch('/api/connector-specs');
      if (!res.ok) return;
      const data = await res.json();
      setSpecs(data.specs ?? data);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => { fetchSpecs(); }, [fetchSpecs]);

  const handleDelete = async (name: string) => {
    if (!confirm(`Delete connector spec "${name}"?`)) return;
    setDeleting(name);
    try {
      await apiFetch(`/api/connector-specs/${name}`, { method: 'DELETE' });
      await fetchSpecs();
    } catch {
      // ignore
    } finally {
      setDeleting(null);
    }
  };

  const handleEdit = async (name: string) => {
    try {
      const res = await apiFetch(`/api/connector-specs/${name}`);
      if (!res.ok) return;
      const data = await res.json();
      setEditingSpec(data);
      setViewMode('edit');
    } catch {
      // ignore
    }
  };

  const handleDryRun = async (name: string) => {
    setDryRunLoading(true);
    setDryRunResult(null);
    try {
      let fields: Record<string, unknown> = {};
      try { fields = JSON.parse(dryRunFields); } catch { /* ignore */ }
      const res = await apiFetch(`/api/connector-specs/${name}/dry-run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          extracted_fields: fields,
          operation_action: dryRunAction || null,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setDryRunResult(data.rendered_request || data);
      }
    } catch {
      // ignore
    } finally {
      setDryRunLoading(false);
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
        const newOps = generated.operations ?? [];
        setEditingSpec({
          ...existing,
          operations: [...existing.operations, ...newOps],
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
          operations: [],
          api_documentation: null,
          example_requests: [],
          credential_fields: [],
          oauth_config: null,
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
        setViewMode('list');
        setEditingSpec(null);
        await fetchSpecs();
      }
    } catch {
      // ignore
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
  const badgeStyle = (bg: string, color: string): React.CSSProperties => ({
    fontSize: '0.7rem',
    backgroundColor: bg,
    color,
    padding: '2px 8px',
    borderRadius: 10,
    fontWeight: 500,
    marginLeft: 6,
  });

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
        <h3 style={{ margin: 0, fontSize: '0.85rem', fontWeight: 600, color: '#666', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Connector Specs
        </h3>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            onClick={() => setGenerateOpen(true)}
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
            AI Generate
          </button>
          <button
            onClick={() => { setEditingSpec(null); setViewMode('create'); }}
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
            New Spec
          </button>
        </div>
      </div>

      {/* AI Generate Modal */}
      {generateOpen && (
        <div style={{
          border: '1px solid #e2e8f0',
          borderRadius: 10,
          padding: '1.25rem',
          marginBottom: '1rem',
          backgroundColor: '#fefcf9',
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
            <span style={{ fontWeight: 600, fontSize: '0.9rem' }}>Generate from API Docs</span>
            <button onClick={() => { setGenerateOpen(false); setGenerateTarget(''); setGenerateError(null); }} style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: '1rem', color: '#999' }}>
              &#10005;
            </button>
          </div>
          <textarea
            value={generateDocs}
            onChange={e => setGenerateDocs(e.target.value)}
            rows={10}
            placeholder="Paste API documentation here..."
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
              marginBottom: '0.75rem',
            }}
          />
          <div style={{ marginBottom: '0.75rem' }}>
            <label style={{ display: 'block', fontSize: '0.78rem', fontWeight: 500, color: '#555', marginBottom: 4 }}>
              Target
            </label>
            <select
              value={generateTarget}
              onChange={e => setGenerateTarget(e.target.value)}
              style={{
                width: '100%',
                padding: '6px 8px',
                border: '1px solid #ddd',
                borderRadius: 6,
                fontSize: '0.82rem',
                fontFamily: 'inherit',
                boxSizing: 'border-box',
                outline: 'none',
                backgroundColor: '#fff',
              }}
            >
              <option value="">New connector</option>
              {specs.map(s => (
                <option key={s.connector_name} value={s.connector_name}>
                  Append to: {s.display_name}
                </option>
              ))}
            </select>
          </div>
          <button
            onClick={handleGenerate}
            disabled={generating || !generateDocs.trim()}
            style={{
              padding: '6px 14px',
              fontSize: '0.8rem',
              fontWeight: 500,
              border: 'none',
              borderRadius: 6,
              backgroundColor: '#c4a882',
              color: '#fff',
              cursor: generating ? 'not-allowed' : 'pointer',
              fontFamily: 'inherit',
            }}
          >
            {generating ? 'Generating...' : 'Generate'}
          </button>
          {generateError && (
            <p style={{ color: '#e53e3e', fontSize: '0.82rem', marginTop: '0.5rem', marginBottom: 0 }}>
              {generateError}
            </p>
          )}
        </div>
      )}

      {specs.length === 0 && (
        <p style={{ color: '#999', fontSize: '0.85rem' }}>No connector specs defined yet.</p>
      )}

      {specs.map(spec => (
        <div key={spec.connector_name} style={{
          border: '1px solid #e2e8f0',
          borderRadius: 10,
          padding: '1.25rem',
          marginBottom: '1rem',
          backgroundColor: '#fff',
        }}>
          {/* Header */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 600, fontSize: '0.95rem' }}>{spec.display_name}</span>
              <span style={{ fontSize: '0.75rem', color: '#999', marginLeft: 8 }}>{spec.connector_name}</span>
              {spec.category && (
                <span style={badgeStyle('#e6fffa', '#234e52')}>{spec.category}</span>
              )}
              <span style={badgeStyle(
                spec.execution_mode === 'agent' ? '#fef3c7' : '#ebf4ff',
                spec.execution_mode === 'agent' ? '#92400e' : '#2b6cb0',
              )}>
                {spec.execution_mode}
              </span>
              {!spec.enabled && (
                <span style={badgeStyle('#fed7d7', '#c53030')}>disabled</span>
              )}
            </div>
            <span style={{ fontSize: '0.75rem', color: '#999' }}>v{spec.version}</span>
          </div>

          {/* Info */}
          <div style={{ fontSize: '0.82rem', color: '#555', marginBottom: '0.75rem' }}>
            Auth: {spec.auth_type}
          </div>

          {/* Actions */}
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button
              onClick={() => handleEdit(spec.connector_name)}
              style={{
                padding: '5px 12px',
                fontSize: '0.78rem',
                fontWeight: 500,
                border: '1px solid #ddd',
                borderRadius: 6,
                backgroundColor: '#fff',
                cursor: 'pointer',
                fontFamily: 'inherit',
              }}
            >
              Edit
            </button>
            {spec.execution_mode === 'template' && (
              <button
                onClick={() => {
                  if (dryRunOpen === spec.connector_name) {
                    setDryRunOpen(null);
                  } else {
                    setDryRunOpen(spec.connector_name);
                    setDryRunResult(null);
                    setDryRunAction('');
                    setDryRunFields('{}');
                  }
                }}
                style={{
                  padding: '5px 12px',
                  fontSize: '0.78rem',
                  fontWeight: 500,
                  border: '1px solid #ddd',
                  borderRadius: 6,
                  backgroundColor: '#fff',
                  cursor: 'pointer',
                  fontFamily: 'inherit',
                }}
              >
                Dry Run
              </button>
            )}
            <button
              onClick={() => handleDelete(spec.connector_name)}
              disabled={deleting === spec.connector_name}
              style={{
                padding: '5px 12px',
                fontSize: '0.78rem',
                fontWeight: 500,
                border: '1px solid #e53e3e',
                borderRadius: 6,
                backgroundColor: '#fff',
                color: '#e53e3e',
                cursor: deleting === spec.connector_name ? 'not-allowed' : 'pointer',
                fontFamily: 'inherit',
              }}
            >
              {deleting === spec.connector_name ? 'Deleting...' : 'Delete'}
            </button>
          </div>

          {/* Dry Run Panel */}
          {dryRunOpen === spec.connector_name && (
            <div style={{
              marginTop: '0.75rem',
              padding: '0.75rem',
              border: '1px solid #edf2f7',
              borderRadius: 8,
              backgroundColor: '#fafafa',
            }}>
              <div style={{ marginBottom: '0.5rem' }}>
                <label style={{ display: 'block', fontSize: '0.78rem', fontWeight: 500, color: '#555', marginBottom: 4 }}>
                  Operation Action
                </label>
                <input
                  type="text"
                  value={dryRunAction}
                  onChange={e => setDryRunAction(e.target.value)}
                  placeholder="e.g. create_employee"
                  style={{
                    width: '100%',
                    padding: '6px 8px',
                    border: '1px solid #ddd',
                    borderRadius: 6,
                    fontSize: '0.82rem',
                    fontFamily: 'inherit',
                    boxSizing: 'border-box',
                    outline: 'none',
                  }}
                />
              </div>
              <div style={{ marginBottom: '0.5rem' }}>
                <label style={{ display: 'block', fontSize: '0.78rem', fontWeight: 500, color: '#555', marginBottom: 4 }}>
                  Extracted Fields (JSON)
                </label>
                <textarea
                  value={dryRunFields}
                  onChange={e => setDryRunFields(e.target.value)}
                  rows={4}
                  style={{
                    width: '100%',
                    padding: '6px 8px',
                    border: '1px solid #ddd',
                    borderRadius: 6,
                    fontSize: '0.82rem',
                    fontFamily: 'monospace',
                    boxSizing: 'border-box',
                    outline: 'none',
                    resize: 'vertical',
                  }}
                />
              </div>
              <button
                onClick={() => handleDryRun(spec.connector_name)}
                disabled={dryRunLoading}
                style={{
                  padding: '5px 12px',
                  fontSize: '0.78rem',
                  fontWeight: 500,
                  border: 'none',
                  borderRadius: 6,
                  backgroundColor: '#c4a882',
                  color: '#fff',
                  cursor: dryRunLoading ? 'not-allowed' : 'pointer',
                  fontFamily: 'inherit',
                  marginBottom: dryRunResult ? '0.5rem' : 0,
                }}
              >
                {dryRunLoading ? 'Rendering...' : 'Render'}
              </button>
              {dryRunResult && (
                <pre style={{
                  padding: '0.75rem',
                  backgroundColor: '#1a202c',
                  color: '#e2e8f0',
                  borderRadius: 6,
                  fontSize: '0.78rem',
                  overflow: 'auto',
                  lineHeight: 1.5,
                  marginTop: '0.5rem',
                }}>
                  {JSON.stringify(dryRunResult, null, 2)}
                </pre>
              )}
            </div>
          )}
        </div>
      ))}
    </>
  );
}
