'use client';

import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../../lib/api';
import type { SavedReport, WidgetAction } from '../../types';
import { BarChart3, Users, Layout, Share2, Loader2 } from 'lucide-react';

interface Props {
  data: Record<string, unknown>;
  props?: Record<string, unknown>;
  onAction?: (action: WidgetAction) => Promise<Record<string, unknown> | void>;
  threadId?: string;
}

interface Template {
  slug: string;
  agent_slug: string;
  title: string;
  description: string;
  chart_count: number;
}

const AGENT_COLORS: Record<string, string> = { hr: '#5b8abd', procurement: '#b07d4f', reports: '#4f8a5e' };

export default function SavedReportsBoard({ onAction }: Props) {
  const [reports, setReports] = useState<SavedReport[]>([]);
  const [sharedReports, setSharedReports] = useState<SavedReport[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [instantiating, setInstantiating] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [reportsRes, templatesRes] = await Promise.all([
        apiFetch('/api/reports'),
        apiFetch('/api/reports/templates'),
      ]);
      if (reportsRes.ok) {
        const d = await reportsRes.json();
        setReports(d.reports || []);
        setSharedReports(d.shared_reports || []);
      }
      if (templatesRes.ok) {
        const d = await templatesRes.json();
        setTemplates(d.templates || []);
      }
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const openReport = (reportId: string) => {
    if (onAction) {
      onAction({
        connector_name: 'norm_reports',
        action: 'open_report_builder',
        params: { report_id: reportId },
      });
    }
  };

  const deleteReport = async (id: string) => {
    await apiFetch(`/api/reports/${id}`, { method: 'DELETE' });
    setConfirmDelete(null);
    fetchAll();
  };

  const instantiateTemplate = async (slug: string) => {
    setInstantiating(slug);
    try {
      const res = await apiFetch(`/api/reports/templates/${slug}/instantiate`, { method: 'POST' });
      if (res.ok) {
        const report = await res.json();
        openReport(report.id);
        fetchAll();
      }
    } catch { /* ignore */ }
    setInstantiating(null);
  };

  if (loading) return <div style={{ padding: '2rem', color: '#888' }}>Loading reports...</div>;

  const confirmDeleteTitle = confirmDelete ? reports.find(r => r.id === confirmDelete)?.title : '';

  return (
    <div style={{ padding: '1rem' }}>
      {/* Page header */}
      <div style={{ marginBottom: '1.25rem' }}>
        <h2 style={{ margin: 0, fontSize: '1.1rem', fontWeight: 700, color: '#1a1a1a' }}>Reports</h2>
        <p style={{ margin: '0.2rem 0 0', fontSize: '0.78rem', color: '#999' }}>
          Build reports from data, use templates, or view shared reports from your team.
        </p>
      </div>

      {/* Templates section */}
      {templates.length > 0 && (
        <Section title="Templates" count={templates.length} icon={<Layout size={14} />}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: '0.6rem' }}>
            {templates.map(t => {
              const color = AGENT_COLORS[t.agent_slug] || '#888';
              return (
                <div key={t.slug} style={{
                  border: '1px solid #f0ebe5', borderRadius: 10, padding: '0.85rem',
                  backgroundColor: '#faf8f5', cursor: 'pointer',
                  transition: 'box-shadow 0.15s',
                }}
                  onClick={() => instantiateTemplate(t.slug)}
                  onMouseEnter={e => (e.currentTarget.style.boxShadow = '0 2px 8px rgba(0,0,0,0.06)')}
                  onMouseLeave={e => (e.currentTarget.style.boxShadow = 'none')}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                    <span style={{
                      fontSize: '0.58rem', fontWeight: 600, padding: '1px 5px', borderRadius: 3,
                      backgroundColor: `${color}18`, color,
                    }}>{t.agent_slug}</span>
                    <span style={{ fontSize: '0.62rem', color: '#bbb' }}>{t.chart_count} chart{t.chart_count !== 1 ? 's' : ''}</span>
                  </div>
                  <div style={{ fontSize: '0.85rem', fontWeight: 600, color: '#333' }}>{t.title}</div>
                  {t.description && <div style={{ fontSize: '0.7rem', color: '#999', marginTop: 3 }}>{t.description}</div>}
                  {instantiating === t.slug && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 6, fontSize: '0.65rem', color: '#c4a882' }}>
                      <Loader2 size={12} style={{ animation: 'spin 1s linear infinite' }} /> Creating...
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </Section>
      )}

      {/* Shared reports section */}
      {sharedReports.length > 0 && (
        <Section title="Shared" count={sharedReports.length} icon={<Share2 size={14} />}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '0.75rem' }}>
            {sharedReports.map(report => (
              <ReportCard key={report.id} report={report} onClick={() => openReport(report.id)} shared />
            ))}
          </div>
        </Section>
      )}

      {/* My reports section */}
      <Section title="My Reports" count={reports.length} icon={<BarChart3 size={14} />}>
        {reports.length === 0 ? (
          <div style={{ padding: '2rem', textAlign: 'center', color: '#bbb', fontSize: '0.82rem' }}>
            No reports yet. Ask Norm for data, then click &ldquo;+ Report&rdquo; on a chart to start building.
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '0.75rem' }}>
            {reports.map(report => (
              <ReportCard
                key={report.id}
                report={report}
                onClick={() => openReport(report.id)}
                onDelete={() => setConfirmDelete(report.id)}
                onPromote={async (slug) => {
                  const res = await apiFetch(`/api/reports/${report.id}/promote-to-dashboard`, {
                    method: 'POST', body: JSON.stringify({ agent_slug: slug }),
                  });
                  if (res.ok) fetchAll();
                }}
              />
            ))}
          </div>
        )}
      </Section>

      {/* Delete confirmation */}
      {confirmDelete && (
        <div style={{
          position: 'fixed', inset: 0, zIndex: 9999,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          backgroundColor: 'rgba(0,0,0,0.3)',
        }}>
          <div style={{
            backgroundColor: '#fff', borderRadius: 12, padding: '1.5rem', maxWidth: 360, width: '90%',
            boxShadow: '0 10px 40px rgba(0,0,0,0.15)', textAlign: 'center',
          }}>
            <h3 style={{ fontSize: '0.95rem', fontWeight: 600, color: '#333', margin: '0 0 0.5rem' }}>Delete report?</h3>
            <p style={{ fontSize: '0.82rem', color: '#888', margin: '0 0 0.25rem' }}>
              <strong style={{ color: '#555' }}>{confirmDeleteTitle}</strong>
            </p>
            <p style={{ fontSize: '0.78rem', color: '#aaa', margin: '0 0 1.25rem' }}>
              This will permanently delete the report and all its charts.
            </p>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
              <button onClick={() => setConfirmDelete(null)} style={{
                padding: '8px 20px', fontSize: '0.82rem', fontWeight: 500,
                border: '1px solid #ddd', borderRadius: 8, backgroundColor: '#fff',
                color: '#666', cursor: 'pointer', fontFamily: 'inherit',
              }}>Cancel</button>
              <button onClick={() => deleteReport(confirmDelete)} style={{
                padding: '8px 20px', fontSize: '0.82rem', fontWeight: 600,
                border: 'none', borderRadius: 8, backgroundColor: '#e53e3e',
                color: '#fff', cursor: 'pointer', fontFamily: 'inherit',
              }}>Delete</button>
            </div>
          </div>
        </div>
      )}

      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}

// --- Sub-components ---

function Section({ title, count, icon, children }: {
  title: string; count: number; icon: React.ReactNode; children: React.ReactNode;
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

function ReportCard({ report, onClick, onDelete, onPromote, shared }: {
  report: SavedReport;
  onClick: () => void;
  onDelete?: () => void;
  onPromote?: (agentSlug: string) => void;
  shared?: boolean;
}) {
  return (
    <div
      onClick={onClick}
      style={{
        border: '1px solid #e2e8f0', borderRadius: 10, padding: '1rem',
        backgroundColor: '#fff', cursor: 'pointer',
        transition: 'border-color 0.15s, box-shadow 0.15s',
      }}
      onMouseEnter={e => { e.currentTarget.style.borderColor = '#c4a882'; e.currentTarget.style.boxShadow = '0 2px 8px rgba(0,0,0,0.06)'; }}
      onMouseLeave={e => { e.currentTarget.style.borderColor = '#e2e8f0'; e.currentTarget.style.boxShadow = 'none'; }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start' }}>
        <div>
          <div style={{ fontSize: '0.88rem', fontWeight: 600, color: '#333', marginBottom: 4 }}>{report.title}</div>
          <div style={{ fontSize: '0.72rem', color: '#aaa', display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            {report.charts.length} chart{report.charts.length !== 1 ? 's' : ''}
            {report.status === 'saved' && <span style={{ color: '#48bb78' }}>Saved</span>}
            {report.status === 'draft' && <span style={{ color: '#ed8936' }}>Draft</span>}
            {report.is_dashboard && (
              <span style={{ fontSize: '0.62rem', fontWeight: 600, color: '#4f8a5e', backgroundColor: '#f0faf2', padding: '1px 6px', borderRadius: 3 }}>Dashboard</span>
            )}
            {report.is_published && (
              <span style={{ fontSize: '0.62rem', fontWeight: 600, color: '#5b8abd', backgroundColor: '#eef4fb', padding: '1px 6px', borderRadius: 3 }}>Shared</span>
            )}
            {shared && <Share2 size={11} style={{ color: '#bbb' }} />}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 4 }} onClick={e => e.stopPropagation()}>
          {onPromote && !report.is_dashboard && (
            <select
              onChange={e => { if (e.target.value) { onPromote(e.target.value); e.target.value = ''; } }}
              defaultValue=""
              style={{ border: 'none', background: 'none', color: '#ccc', cursor: 'pointer', fontSize: '0.62rem', padding: '0 2px', fontFamily: 'inherit' }}
              title="Set as dashboard"
            >
              <option value="" disabled>Dashboard</option>
              <option value="reports">Reports</option>
              <option value="hr">HR</option>
              <option value="procurement">Procurement</option>
            </select>
          )}
          {onDelete && (
            <button
              onClick={onDelete}
              onMouseEnter={e => (e.currentTarget.style.color = '#e53e3e')}
              onMouseLeave={e => (e.currentTarget.style.color = '#ccc')}
              style={{ border: 'none', background: 'none', color: '#ccc', cursor: 'pointer', fontSize: '0.85rem', padding: '0 4px', transition: 'color 0.15s' }}
              title="Delete report"
            >&times;</button>
          )}
        </div>
      </div>
      {report.description && <div style={{ fontSize: '0.75rem', color: '#999', marginTop: 4 }}>{report.description}</div>}
      <div style={{ fontSize: '0.65rem', color: '#ccc', marginTop: 8 }}>
        {report.updated_at ? new Date(report.updated_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : ''}
      </div>
    </div>
  );
}
