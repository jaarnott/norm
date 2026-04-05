'use client';

import { useState, useEffect } from 'react';
import { apiFetch } from '../../../lib/api';
import { Layout, BarChart3, Share2, Loader2, Check } from 'lucide-react';

interface Template {
  slug: string;
  title: string;
  description: string | null;
  chart_count: number;
}

interface DashboardSummary {
  id: string;
  title: string;
  description: string | null;
  charts: { id: string }[];
  status: string;
  is_published: boolean;
  updated_at: string | null;
}

interface AvailableData {
  active_id: string | null;
  templates: Template[];
  own: DashboardSummary[];
  shared: DashboardSummary[];
}

interface DashboardPickerProps {
  agentSlug: string;
  onDashboardSelected: () => void;
}

const AGENT_LABELS: Record<string, string> = { hr: 'HR', procurement: 'Procurement', reports: 'Reports' };
const AGENT_COLORS: Record<string, string> = { hr: '#5b8abd', procurement: '#b07d4f', reports: '#4f8a5e' };

export default function DashboardPicker({ agentSlug, onDashboardSelected }: DashboardPickerProps) {
  const [data, setData] = useState<AvailableData | null>(null);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  useEffect(() => {
    apiFetch(`/api/reports/dashboards/${agentSlug}/available`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setData(d); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [agentSlug]);

  const reload = () => {
    apiFetch(`/api/reports/dashboards/${agentSlug}/available`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setData(d); })
      .catch(() => {});
  };

  const deleteDashboard = async (id: string) => {
    await apiFetch(`/api/reports/${id}`, { method: 'DELETE' });
    setConfirmDelete(null);
    // If the deleted dashboard was active, clear the preference
    if (data?.active_id === id) {
      await apiFetch(`/api/reports/dashboards/${agentSlug}/set-active`, {
        method: 'POST',
        body: JSON.stringify({ dashboard_id: '' }),
      }).catch(() => {});
    }
    reload();
  };

  const setActive = async (dashboardId: string) => {
    setActing(dashboardId);
    const res = await apiFetch(`/api/reports/dashboards/${agentSlug}/set-active`, {
      method: 'POST',
      body: JSON.stringify({ dashboard_id: dashboardId }),
    });
    if (res.ok) onDashboardSelected();
    setActing(null);
  };

  const instantiateTemplate = async (slug: string) => {
    setActing(`tmpl-${slug}`);
    const res = await apiFetch(`/api/reports/templates/${slug}/instantiate`, { method: 'POST' });
    if (res.ok) {
      const report = await res.json();
      // Set the newly created dashboard as active
      await apiFetch(`/api/reports/dashboards/${agentSlug}/set-active`, {
        method: 'POST',
        body: JSON.stringify({ dashboard_id: report.id }),
      });
      onDashboardSelected();
    }
    setActing(null);
  };

  if (loading) {
    return (
      <div style={{ padding: '3rem', textAlign: 'center', color: '#999' }}>
        <Loader2 size={24} style={{ animation: 'spin 1s linear infinite' }} />
        <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
      </div>
    );
  }

  const agentLabel = AGENT_LABELS[agentSlug] || agentSlug;
  const color = AGENT_COLORS[agentSlug] || '#888';
  const activeId = data?.active_id;
  const templates = data?.templates || [];
  const own = data?.own || [];
  const shared = data?.shared || [];
  const hasContent = templates.length > 0 || own.length > 0 || shared.length > 0;

  return (
    <div style={{ padding: '1.5rem', maxWidth: 900, margin: '0 auto' }}>
      {/* Header */}
      <div style={{ textAlign: 'center', marginBottom: '1.5rem' }}>
        <h2 style={{ margin: 0, fontSize: '1.1rem', fontWeight: 700, color: '#1a1a1a' }}>
          {agentLabel} Dashboards
        </h2>
        <p style={{ margin: '0.3rem 0 0', fontSize: '0.78rem', color: '#999' }}>
          Choose a dashboard to display, or create one from a template.
        </p>
      </div>

      {!hasContent && (
        <div style={{ textAlign: 'center', padding: '2rem', color: '#bbb', fontSize: '0.85rem' }}>
          No dashboards available yet. Ask Norm to build a dashboard, or check the Templates tab in Settings.
        </div>
      )}

      {/* Templates */}
      {templates.length > 0 && (
        <Section title="Templates" icon={<Layout size={14} />} count={templates.length}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: '0.6rem' }}>
            {templates.map(t => (
              <div
                key={t.slug}
                onClick={() => instantiateTemplate(t.slug)}
                style={{
                  border: '1px solid #f0ebe5', borderRadius: 10, padding: '1rem',
                  backgroundColor: '#faf8f5', cursor: 'pointer',
                  transition: 'box-shadow 0.15s',
                }}
                onMouseEnter={e => (e.currentTarget.style.boxShadow = '0 2px 10px rgba(0,0,0,0.06)')}
                onMouseLeave={e => (e.currentTarget.style.boxShadow = 'none')}
              >
                <div style={{ fontSize: '0.85rem', fontWeight: 600, color: '#333' }}>{t.title}</div>
                <div style={{ fontSize: '0.65rem', color: '#bbb', marginTop: 2 }}>{t.chart_count} chart{t.chart_count !== 1 ? 's' : ''}</div>
                {t.description && <div style={{ fontSize: '0.72rem', color: '#999', marginTop: 4 }}>{t.description}</div>}
                <div style={{ marginTop: 8 }}>
                  {acting === `tmpl-${t.slug}` ? (
                    <span style={{ fontSize: '0.68rem', color: color }}><Loader2 size={12} style={{ animation: 'spin 1s linear infinite', verticalAlign: 'middle' }} /> Creating...</span>
                  ) : (
                    <span style={{ fontSize: '0.68rem', fontWeight: 600, color }}>Use Template</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* My Dashboards */}
      {own.length > 0 && (
        <Section title="My Dashboards" icon={<BarChart3 size={14} />} count={own.length}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '0.6rem' }}>
            {own.map(d => (
              <DashboardCard
                key={d.id}
                dashboard={d}
                isActive={activeId === d.id}
                acting={acting === d.id}
                color={color}
                onSetActive={() => setActive(d.id)}
                onDelete={() => setConfirmDelete(d.id)}
              />
            ))}
          </div>
        </Section>
      )}

      {/* Shared Dashboards */}
      {shared.length > 0 && (
        <Section title="Shared" icon={<Share2 size={14} />} count={shared.length}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '0.6rem' }}>
            {shared.map(d => (
              <DashboardCard
                key={d.id}
                dashboard={d}
                isActive={activeId === d.id}
                acting={acting === d.id}
                color={color}
                onSetActive={() => setActive(d.id)}
                shared
              />
            ))}
          </div>
        </Section>
      )}

      {/* Delete confirmation */}
      {confirmDelete && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 9999, backgroundColor: 'rgba(0,0,0,0.3)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ backgroundColor: '#fff', borderRadius: 12, padding: '1.5rem', maxWidth: 360, width: '90%', boxShadow: '0 10px 40px rgba(0,0,0,0.15)', textAlign: 'center' }}>
            <h3 style={{ fontSize: '0.95rem', fontWeight: 600, color: '#333', margin: '0 0 0.5rem' }}>Delete dashboard?</h3>
            <p style={{ fontSize: '0.78rem', color: '#888', margin: '0 0 0.25rem' }}>
              <strong style={{ color: '#555' }}>{own.find(d => d.id === confirmDelete)?.title}</strong>
            </p>
            <p style={{ fontSize: '0.75rem', color: '#aaa', margin: '0 0 1.25rem' }}>This will permanently delete this dashboard and all its charts.</p>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
              <button onClick={() => setConfirmDelete(null)} style={{ padding: '8px 20px', fontSize: '0.82rem', fontWeight: 500, border: '1px solid #ddd', borderRadius: 8, backgroundColor: '#fff', color: '#666', cursor: 'pointer', fontFamily: 'inherit' }}>Cancel</button>
              <button onClick={() => deleteDashboard(confirmDelete)} style={{ padding: '8px 20px', fontSize: '0.82rem', fontWeight: 600, border: 'none', borderRadius: 8, backgroundColor: '#e53e3e', color: '#fff', cursor: 'pointer', fontFamily: 'inherit' }}>Delete</button>
            </div>
          </div>
        </div>
      )}

      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}

function Section({ title, icon, count, children }: {
  title: string; icon: React.ReactNode; count: number; children: React.ReactNode;
}) {
  return (
    <div style={{ marginBottom: '1.5rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: '0.6rem' }}>
        <span style={{ color: '#bbb' }}>{icon}</span>
        <span style={{ fontSize: '0.78rem', fontWeight: 600, color: '#555' }}>{title}</span>
        <span style={{ fontSize: '0.62rem', color: '#bbb', backgroundColor: '#f5f5f5', padding: '1px 6px', borderRadius: 8 }}>{count}</span>
      </div>
      {children}
    </div>
  );
}

function DashboardCard({ dashboard, isActive, acting, color, onSetActive, onDelete, shared }: {
  dashboard: DashboardSummary; isActive: boolean; acting: boolean; color: string;
  onSetActive: () => void; onDelete?: () => void; shared?: boolean;
}) {
  return (
    <div
      onClick={onSetActive}
      style={{
        border: `1px solid ${isActive ? color : '#e2e8f0'}`,
        borderRadius: 10, padding: '1rem', backgroundColor: isActive ? `${color}08` : '#fff',
        cursor: acting ? 'not-allowed' : 'pointer', transition: 'border-color 0.15s, box-shadow 0.15s',
      }}
      onMouseEnter={e => { if (!isActive) e.currentTarget.style.borderColor = color; e.currentTarget.style.boxShadow = '0 2px 8px rgba(0,0,0,0.06)'; }}
      onMouseLeave={e => { if (!isActive) e.currentTarget.style.borderColor = '#e2e8f0'; e.currentTarget.style.boxShadow = 'none'; }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start' }}>
        <div>
          <div style={{ fontSize: '0.85rem', fontWeight: 600, color: '#333' }}>{dashboard.title}</div>
          <div style={{ fontSize: '0.65rem', color: '#aaa', display: 'flex', alignItems: 'center', gap: 6, marginTop: 2, flexWrap: 'wrap' }}>
            {dashboard.charts.length} chart{dashboard.charts.length !== 1 ? 's' : ''}
            {dashboard.status === 'saved' && <span style={{ color: '#48bb78' }}>Saved</span>}
            {shared && <Share2 size={10} style={{ color: '#bbb' }} />}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          {isActive ? (
            <span style={{ display: 'flex', alignItems: 'center', gap: 3, fontSize: '0.65rem', fontWeight: 600, color, padding: '2px 8px', borderRadius: 4, backgroundColor: `${color}14` }}>
              <Check size={12} /> Active
            </span>
          ) : acting ? (
            <Loader2 size={14} style={{ color: '#bbb', animation: 'spin 1s linear infinite' }} />
          ) : (
            <span style={{ fontSize: '0.65rem', fontWeight: 600, color: '#bbb' }}>Set Active</span>
          )}
          {onDelete && (
            <button
              onClick={e => { e.stopPropagation(); onDelete(); }}
              onMouseEnter={e => (e.currentTarget.style.color = '#e53e3e')}
              onMouseLeave={e => (e.currentTarget.style.color = '#ccc')}
              style={{ border: 'none', background: 'none', color: '#ccc', cursor: 'pointer', fontSize: '0.85rem', padding: '0 4px', transition: 'color 0.15s' }}
              title="Delete dashboard"
            >&times;</button>
          )}
        </div>
      </div>
      {dashboard.description && <div style={{ fontSize: '0.72rem', color: '#999', marginTop: 4 }}>{dashboard.description}</div>}
      {dashboard.updated_at && (
        <div style={{ fontSize: '0.6rem', color: '#ccc', marginTop: 6 }}>
          {new Date(dashboard.updated_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
        </div>
      )}
    </div>
  );
}
