'use client';

import { useState, useEffect } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import DashboardView from '../display/DashboardView';
import Button from '../ui/Button';
import IconButton from '../ui/IconButton';
import BackLink from '../ui/BackLink';
import Badge from '../ui/Badge';
import PageState from '../ui/PageState';

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

const sectionTitle: React.CSSProperties = { margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' };
const fieldRow: React.CSSProperties = { marginBottom: 12 };

// How an agent slug reads on a template card (the same names as the Agent picker).
const AGENT_LABELS: Record<string, string> = { hr: 'HR', procurement: 'Procurement', reports: 'Reports' };

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

  if (loading) return <PageState kind="loading" title="Loading templates…" />;

  // Live edit mode — full DashboardView with save toolbar
  if (view === 'live-edit' && editReportId) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        {/* Toolbar */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8,
          padding: '0 8px 12px', marginBottom: 4, borderBottom: '1px solid var(--line)', flexShrink: 0,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
            <Badge tone="info">Template editor</Badge>
            <span style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text-soft)', overflowWrap: 'anywhere' }}>{editingSlug}</span>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <Button size="sm" onClick={handleDiscardEdit}>Discard</Button>
            <Button size="sm" variant="primary" onClick={handleSaveToTemplate} disabled={savingTemplate}>
              {savingTemplate ? 'Saving…' : 'Save to template'}
            </Button>
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
      <div style={{ maxWidth: 800 }}>
        <div style={{ marginBottom: 8 }}>
          <BackLink label="Back to templates" onClick={() => setView('list')} />
        </div>
        <h3 style={{ ...sectionTitle, marginBottom: 16 }}>New template</h3>

        {error && <div style={{ marginBottom: 12 }}><PageState kind="error" title={error} /></div>}

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12, marginBottom: 12 }}>
          <div>
            <label className="n-label" htmlFor="tpl-slug">Slug</label>
            <input id="tpl-slug" className="n-input" value={draft.slug} onChange={e => setDraft(d => ({ ...d, slug: e.target.value }))} style={{ width: '100%' }} placeholder="e.g. sales-overview" />
          </div>
          <div>
            <label className="n-label" htmlFor="tpl-agent">Agent</label>
            <select id="tpl-agent" className="n-select" value={draft.agent_slug} onChange={e => setDraft(d => ({ ...d, agent_slug: e.target.value }))} style={{ width: '100%' }}>
              <option value="reports">Reports</option>
              <option value="hr">HR</option>
              <option value="procurement">Procurement</option>
            </select>
          </div>
        </div>

        <div style={fieldRow}>
          <label className="n-label" htmlFor="tpl-title">Title</label>
          <input id="tpl-title" className="n-input" value={draft.title} onChange={e => setDraft(d => ({ ...d, title: e.target.value }))} style={{ width: '100%' }} />
        </div>

        <div style={fieldRow}>
          <label className="n-label" htmlFor="tpl-description">Description</label>
          <input id="tpl-description" className="n-input" value={draft.description || ''} onChange={e => setDraft(d => ({ ...d, description: e.target.value }))} style={{ width: '100%' }} />
        </div>

        <div style={{ marginBottom: 16 }}>
          <label className="n-label" htmlFor="tpl-charts">Charts (JSON)</label>
          <textarea
            id="tpl-charts"
            className="n-input"
            value={chartsDraft}
            onChange={e => setChartsDraft(e.target.value)}
            spellCheck={false}
            style={{ display: 'block', width: '100%', minHeight: 200, fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-sm)', whiteSpace: 'pre', lineHeight: 1.5 }}
          />
        </div>

        <div style={{ display: 'flex', gap: 8 }}>
          <Button variant="primary" onClick={handleCreateSave} disabled={saving}>{saving ? 'Saving…' : 'Create'}</Button>
          <Button onClick={() => setView('list')}>Cancel</Button>
        </div>
      </div>
    );
  }

  // List view
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, minWidth: 0 }}>
          <h3 style={sectionTitle}>Dashboard templates</h3>
          {templates.length > 0 && (
            <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
              {templates.length} {templates.length === 1 ? 'template' : 'templates'}
            </span>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Button onClick={handleSeed}>Seed defaults</Button>
          <Button variant="primary" icon={Plus} onClick={() => { setDraft(EMPTY); setChartsDraft('[]'); setView('create'); setError(''); }}>
            New template
          </Button>
        </div>
      </div>

      {/* Here `error` carries the seed result ("Seeded 3 template(s)"). */}
      {error && (
        <div role="status" style={{ padding: '8px 12px', marginBottom: 12, borderRadius: 'var(--radius)', backgroundColor: 'var(--ok-bg)', color: 'var(--ok)', fontSize: 'var(--fs-sm)', fontWeight: 500 }}>
          {error}
        </div>
      )}

      {templates.length === 0 ? (
        <PageState kind="empty" title="No templates yet." detail={<>Click &ldquo;Seed defaults&rdquo; to add the built-in templates, or create a new one.</>} />
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12 }}>
          {templates.map(t => (
            <div key={t.slug} className="n-card" style={{
              padding: 16, display: 'flex', flexDirection: 'column', gap: 8,
              cursor: 'pointer', transition: 'border-color 0.12s',
            }}
              onClick={() => openLiveEdit(t)}
              onMouseEnter={e => (e.currentTarget.style.borderColor = 'var(--line-strong)')}
              onMouseLeave={e => (e.currentTarget.style.borderColor = '')}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>{t.title}</div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center', marginTop: 4 }}>
                    <Badge>{AGENT_LABELS[t.agent_slug] || t.agent_slug}</Badge>
                    <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{t.chart_count} chart{t.chart_count !== 1 ? 's' : ''}</span>
                    {!t.enabled && <Badge tone="warn">Disabled</Badge>}
                  </div>
                </div>
                <IconButton
                  icon={Trash2}
                  label={`Delete ${t.title}`}
                  iconSize={16}
                  onClick={(e) => { e.stopPropagation(); setConfirmDelete(t.slug); }}
                  style={{ margin: '-6px -6px 0 0' }}
                />
              </div>
              {t.description && <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{t.description}</div>}
            </div>
          ))}
        </div>
      )}

      {/* Delete confirmation */}
      {confirmDelete && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 9999, backgroundColor: 'rgba(26, 26, 26, 0.35)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <div role="dialog" aria-modal="true" aria-labelledby="tpl-delete-title" style={{ backgroundColor: 'var(--bg)', borderRadius: 'var(--radius-lg)', padding: 24, width: '100%', maxWidth: 400, boxShadow: '0 12px 40px rgba(26, 26, 26, 0.18)' }}>
            <h3 id="tpl-delete-title" style={{ ...sectionTitle, marginBottom: 16, overflowWrap: 'anywhere' }}>Delete template &ldquo;{confirmDelete}&rdquo;?</h3>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <Button onClick={() => setConfirmDelete(null)}>Cancel</Button>
              <Button variant="danger" onClick={() => handleDelete(confirmDelete)}>Delete</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
