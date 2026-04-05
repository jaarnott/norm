'use client';

import { useState, useEffect } from 'react';
import { apiFetch } from '../../lib/api';
import DashboardView from '../display/DashboardView';

interface DashboardTemplate {
  id: string;
  slug: string;
  agent_slug: string;
  title: string;
  description: string | null;
  charts: Record<string, unknown>[];
  chart_count: number;
  enabled: boolean;
  created_at: string | null;
  updated_at: string | null;
}

const EMPTY: DashboardTemplate = {
  id: '', slug: '', agent_slug: 'reports', title: '', description: '',
  charts: [], chart_count: 0, enabled: true, created_at: null, updated_at: null,
};

const labelStyle: React.CSSProperties = { fontSize: '0.75rem', fontWeight: 600, color: '#888', textTransform: 'uppercase' as const, marginBottom: 4, display: 'block' };
const inputStyle: React.CSSProperties = { width: '100%', padding: '6px 8px', border: '1px solid #ddd', borderRadius: 6, fontSize: '0.85rem', fontFamily: 'inherit', boxSizing: 'border-box' as const };

const AGENT_COLORS: Record<string, string> = { hr: '#5b8abd', procurement: '#b07d4f', reports: '#4f8a5e' };

export default function TemplatesPanel() {
  const [templates, setTemplates] = useState<DashboardTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<'list' | 'create' | 'live-edit'>('list');
  const [draft, setDraft] = useState<DashboardTemplate>(EMPTY);
  const [chartsDraft, setChartsDraft] = useState('[]');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  // Live edit state
  const [editingSlug, setEditingSlug] = useState('');
  const [editReportId, setEditReportId] = useState<string | null>(null);
  const [savingTemplate, setSavingTemplate] = useState(false);

  const fetchTemplates = () => {
    apiFetch('/api/dashboard-templates')
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.templates) setTemplates(d.templates); })
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => { fetchTemplates(); }, []);

  const handleCreateSave = async () => {
    setSaving(true);
    setError('');
    try {
      let charts: Record<string, unknown>[];
      try { charts = JSON.parse(chartsDraft); } catch { setError('Invalid JSON in charts'); setSaving(false); return; }

      const body = {
        slug: draft.slug,
        agent_slug: draft.agent_slug,
        title: draft.title,
        description: draft.description,
        charts,
        enabled: draft.enabled,
      };

      const res = await apiFetch('/api/dashboard-templates', { method: 'POST', body: JSON.stringify(body) });
      if (res.ok) {
        setView('list');
        fetchTemplates();
      } else {
        const err = await res.json().catch(() => ({}));
        setError(err.detail || 'Save failed');
      }
    } catch { setError('Save failed'); }
    setSaving(false);
  };

  const handleDelete = async (slug: string) => {
    const res = await apiFetch(`/api/dashboard-templates/${slug}`, { method: 'DELETE' });
    if (res.ok) { setConfirmDelete(null); fetchTemplates(); }
  };

  const handleSeed = async () => {
    const res = await apiFetch('/api/dashboard-templates/seed', { method: 'POST' });
    if (res.ok) {
      const d = await res.json();
      fetchTemplates();
      if (d.seeded > 0) setError(`Seeded ${d.seeded} template(s)`);
    }
  };

  const openLiveEdit = async (t: DashboardTemplate) => {
    // Create a temporary report from the template
    const res = await apiFetch(`/api/dashboard-templates/${t.slug}/edit`, { method: 'POST' });
    if (res.ok) {
      const d = await res.json();
      setEditingSlug(t.slug);
      setEditReportId(d.report_id);
      setView('live-edit');
      setError('');
    }
  };

  const handleSaveToTemplate = async () => {
    if (!editReportId || !editingSlug) return;
    setSavingTemplate(true);
    const res = await apiFetch(`/api/dashboard-templates/${editingSlug}/save-from-report/${editReportId}`, { method: 'POST' });
    if (res.ok) {
      setView('list');
      setEditReportId(null);
      setEditingSlug('');
      fetchTemplates();
    }
    setSavingTemplate(false);
  };

  const handleDiscardEdit = async () => {
    // Clean up the temp report by saving (which deletes it) or just deleting it
    if (editReportId) {
      await apiFetch(`/api/reports/${editReportId}`, { method: 'DELETE' }).catch(() => {});
    }
    setView('list');
    setEditReportId(null);
    setEditingSlug('');
  };

  if (loading) return <div style={{ padding: '1rem', color: '#999' }}>Loading...</div>;

  // Live edit mode — full DashboardView with save toolbar
  if (view === 'live-edit' && editReportId) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        {/* Toolbar */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '0.5rem 1rem', borderBottom: '1px solid #f0ebe5', backgroundColor: '#faf8f5', flexShrink: 0,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: '0.7rem', fontWeight: 600, color: '#c4a882', backgroundColor: '#faf6f0', padding: '2px 8px', borderRadius: 4, border: '1px solid #e8e0d4' }}>
              Template Editor
            </span>
            <span style={{ fontSize: '0.8rem', fontWeight: 600, color: '#555' }}>{editingSlug}</span>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button onClick={handleDiscardEdit} style={{
              padding: '5px 14px', fontSize: '0.75rem', fontWeight: 500, border: '1px solid #ddd', borderRadius: 6,
              backgroundColor: '#fff', color: '#666', cursor: 'pointer', fontFamily: 'inherit',
            }}>Discard</button>
            <button onClick={handleSaveToTemplate} disabled={savingTemplate} style={{
              padding: '5px 14px', fontSize: '0.75rem', fontWeight: 600, border: 'none', borderRadius: 6,
              backgroundColor: '#c4a882', color: '#fff', cursor: savingTemplate ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
            }}>{savingTemplate ? 'Saving...' : 'Save to Template'}</button>
          </div>
        </div>
        {/* Dashboard view — same as user sees */}
        <div style={{ flex: 1, overflow: 'auto' }}>
          <DashboardView
            data={{ report_id: editReportId }}
            props={{}}
          />
        </div>
      </div>
    );
  }

  // Create form
  if (view === 'create') {
    return (
      <div style={{ padding: '1rem', maxWidth: 800 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
          <h3 style={{ margin: 0, fontSize: '0.95rem', fontWeight: 700 }}>New Template</h3>
          <button onClick={() => setView('list')} style={{ padding: '4px 12px', fontSize: '0.75rem', border: '1px solid #ddd', borderRadius: 6, backgroundColor: '#fff', cursor: 'pointer', fontFamily: 'inherit' }}>Back</button>
        </div>

        {error && <div style={{ padding: '6px 10px', backgroundColor: '#fff5f5', border: '1px solid #fed7d7', borderRadius: 6, fontSize: '0.75rem', color: '#c53030', marginBottom: '0.75rem' }}>{error}</div>}

        <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
          <div style={{ flex: 1 }}>
            <label style={labelStyle}>Slug</label>
            <input value={draft.slug} onChange={e => setDraft(d => ({ ...d, slug: e.target.value }))} style={inputStyle} placeholder="e.g. sales-overview" />
          </div>
          <div style={{ flex: 1 }}>
            <label style={labelStyle}>Agent</label>
            <select value={draft.agent_slug} onChange={e => setDraft(d => ({ ...d, agent_slug: e.target.value }))} style={inputStyle}>
              <option value="reports">Reports</option>
              <option value="hr">HR</option>
              <option value="procurement">Procurement</option>
            </select>
          </div>
        </div>

        <div style={{ marginBottom: 12 }}>
          <label style={labelStyle}>Title</label>
          <input value={draft.title} onChange={e => setDraft(d => ({ ...d, title: e.target.value }))} style={inputStyle} />
        </div>

        <div style={{ marginBottom: 12 }}>
          <label style={labelStyle}>Description</label>
          <input value={draft.description || ''} onChange={e => setDraft(d => ({ ...d, description: e.target.value }))} style={inputStyle} />
        </div>

        <div style={{ marginBottom: 12 }}>
          <label style={labelStyle}>Charts (JSON)</label>
          <textarea
            value={chartsDraft}
            onChange={e => setChartsDraft(e.target.value)}
            spellCheck={false}
            style={{ ...inputStyle, minHeight: 200, fontFamily: 'monospace', fontSize: '0.72rem', resize: 'vertical', whiteSpace: 'pre', lineHeight: 1.5 }}
          />
        </div>

        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={handleCreateSave} disabled={saving} style={{
            padding: '6px 20px', fontSize: '0.8rem', fontWeight: 600, border: 'none', borderRadius: 6,
            backgroundColor: '#c4a882', color: '#fff', cursor: saving ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
          }}>{saving ? 'Saving...' : 'Create'}</button>
          <button onClick={() => setView('list')} style={{
            padding: '6px 20px', fontSize: '0.8rem', fontWeight: 600, border: '1px solid #ddd', borderRadius: 6,
            backgroundColor: '#fff', color: '#666', cursor: 'pointer', fontFamily: 'inherit',
          }}>Cancel</button>
        </div>
      </div>
    );
  }

  // List view
  return (
    <div style={{ padding: '1rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
        <h3 style={{ margin: 0, fontSize: '0.95rem', fontWeight: 700 }}>Dashboard Templates</h3>
        <div style={{ display: 'flex', gap: 6 }}>
          <button onClick={handleSeed} style={{
            padding: '4px 12px', fontSize: '0.72rem', fontWeight: 500, border: '1px solid #ddd', borderRadius: 6,
            backgroundColor: '#fff', color: '#888', cursor: 'pointer', fontFamily: 'inherit',
          }}>Seed defaults</button>
          <button onClick={() => { setDraft(EMPTY); setChartsDraft('[]'); setView('create'); setError(''); }} style={{
            padding: '4px 12px', fontSize: '0.72rem', fontWeight: 600, border: 'none', borderRadius: 6,
            backgroundColor: '#c4a882', color: '#fff', cursor: 'pointer', fontFamily: 'inherit',
          }}>+ New</button>
        </div>
      </div>

      {error && <div style={{ padding: '6px 10px', backgroundColor: '#f0faf2', border: '1px solid #c6f6d5', borderRadius: 6, fontSize: '0.75rem', color: '#2f855a', marginBottom: '0.75rem' }}>{error}</div>}

      {templates.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '2rem', color: '#999', fontSize: '0.85rem' }}>
          No templates yet. Click &ldquo;Seed defaults&rdquo; to add the built-in templates, or create a new one.
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12 }}>
          {templates.map(t => (
            <div key={t.slug} style={{
              border: '1px solid #f0ebe5', borderRadius: 10, padding: '1rem',
              backgroundColor: '#fff', display: 'flex', flexDirection: 'column', gap: 8,
              cursor: 'pointer', transition: 'box-shadow 0.15s',
            }}
              onClick={() => openLiveEdit(t)}
              onMouseEnter={e => (e.currentTarget.style.boxShadow = '0 2px 12px rgba(0,0,0,0.06)')}
              onMouseLeave={e => (e.currentTarget.style.boxShadow = 'none')}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start' }}>
                <div>
                  <div style={{ fontSize: '0.85rem', fontWeight: 600, color: '#333' }}>{t.title}</div>
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 2 }}>
                    <span style={{
                      fontSize: '0.6rem', fontWeight: 600, padding: '1px 6px', borderRadius: 3,
                      backgroundColor: `${AGENT_COLORS[t.agent_slug] || '#888'}18`,
                      color: AGENT_COLORS[t.agent_slug] || '#888',
                    }}>{t.agent_slug}</span>
                    <span style={{ fontSize: '0.65rem', color: '#bbb' }}>{t.chart_count} chart{t.chart_count !== 1 ? 's' : ''}</span>
                    {!t.enabled && <span style={{ fontSize: '0.6rem', color: '#dc3545', fontWeight: 600 }}>disabled</span>}
                  </div>
                </div>
                <button onClick={(e) => { e.stopPropagation(); setConfirmDelete(t.slug); }}
                  style={{ border: 'none', background: 'none', color: '#ccc', cursor: 'pointer', fontSize: '0.8rem' }}>&times;</button>
              </div>
              {t.description && <div style={{ fontSize: '0.72rem', color: '#999' }}>{t.description}</div>}
            </div>
          ))}
        </div>
      )}

      {/* Delete confirmation */}
      {confirmDelete && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 9999, backgroundColor: 'rgba(0,0,0,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ backgroundColor: '#fff', borderRadius: 12, padding: '1.5rem', maxWidth: 400, boxShadow: '0 4px 20px rgba(0,0,0,0.15)' }}>
            <div style={{ fontSize: '0.9rem', fontWeight: 600, marginBottom: 12 }}>Delete template &ldquo;{confirmDelete}&rdquo;?</div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button onClick={() => setConfirmDelete(null)} style={{ padding: '6px 16px', fontSize: '0.8rem', border: '1px solid #ddd', borderRadius: 6, backgroundColor: '#fff', cursor: 'pointer', fontFamily: 'inherit' }}>Cancel</button>
              <button onClick={() => handleDelete(confirmDelete)} style={{ padding: '6px 16px', fontSize: '0.8rem', fontWeight: 600, border: 'none', borderRadius: 6, backgroundColor: '#dc3545', color: '#fff', cursor: 'pointer', fontFamily: 'inherit' }}>Delete</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
