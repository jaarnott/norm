'use client';

import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../../lib/api';
import type { SavedReport, WidgetAction } from '../../types';

interface Props {
  data: Record<string, unknown>;
  props?: Record<string, unknown>;
  onAction?: (action: WidgetAction) => Promise<Record<string, unknown> | void>;
  threadId?: string;
}

export default function SavedReportsBoard({ onAction }: Props) {
  const [reports, setReports] = useState<SavedReport[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchReports = useCallback(async () => {
    try {
      const res = await apiFetch('/api/reports');
      if (res.ok) {
        const data = await res.json();
        setReports(data.reports || []);
      }
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { fetchReports(); }, [fetchReports]);

  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const confirmDeleteTitle = confirmDelete ? reports.find(r => r.id === confirmDelete)?.title : '';

  const deleteReport = async (id: string) => {
    await apiFetch(`/api/reports/${id}`, { method: 'DELETE' });
    setConfirmDelete(null);
    fetchReports();
  };

  const openReport = (reportId: string) => {
    if (onAction) {
      onAction({
        connector_name: 'norm_reports',
        action: 'open_report_builder',
        params: { report_id: reportId },
      });
    }
  };

  if (loading) return <div style={{ padding: '2rem', color: '#888' }}>Loading reports...</div>;

  if (reports.length === 0) {
    return (
      <div style={{ padding: '3rem', textAlign: 'center', color: '#999' }}>
        <div style={{ fontSize: '2rem', marginBottom: '0.75rem' }}>📊</div>
        <div style={{ fontSize: '0.9rem', fontWeight: 500 }}>No saved reports yet</div>
        <div style={{ fontSize: '0.78rem', marginTop: '0.5rem', color: '#bbb' }}>
          Ask Norm for data, then click &ldquo;+ Report&rdquo; on a chart to start building.
        </div>
      </div>
    );
  }

  return (
    <div style={{ padding: '1rem' }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '0.75rem' }}>
        {reports.map(report => (
          <div
            key={report.id}
            onClick={() => openReport(report.id)}
            style={{
              border: '1px solid #e2e8f0', borderRadius: 10, padding: '1rem',
              backgroundColor: '#fff', cursor: 'pointer',
              transition: 'border-color 0.15s, box-shadow 0.15s',
            }}
            onMouseEnter={e => {
              e.currentTarget.style.borderColor = '#c4a882';
              e.currentTarget.style.boxShadow = '0 2px 8px rgba(0,0,0,0.06)';
            }}
            onMouseLeave={e => {
              e.currentTarget.style.borderColor = '#e2e8f0';
              e.currentTarget.style.boxShadow = 'none';
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start' }}>
              <div>
                <div style={{ fontSize: '0.88rem', fontWeight: 600, color: '#333', marginBottom: 4 }}>
                  {report.title}
                </div>
                <div style={{ fontSize: '0.72rem', color: '#aaa', display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                  {report.charts.length} chart{report.charts.length !== 1 ? 's' : ''}
                  {report.status === 'saved' && (
                    <span style={{ color: '#48bb78' }}>Saved</span>
                  )}
                  {report.status === 'draft' && (
                    <span style={{ color: '#ed8936' }}>Draft</span>
                  )}
                  {report.is_dashboard && (
                    <span style={{ fontSize: '0.62rem', fontWeight: 600, color: '#4f8a5e', backgroundColor: '#f0faf2', padding: '1px 6px', borderRadius: 3 }}>Dashboard</span>
                  )}
                </div>
              </div>
              <div style={{ display: 'flex', gap: 4 }}>
                {!report.is_dashboard && (
                  <select
                    onClick={e => e.stopPropagation()}
                    onChange={async (e) => {
                      const slug = e.target.value;
                      if (!slug) return;
                      e.target.value = '';
                      const res = await apiFetch(`/api/reports/${report.id}/promote-to-dashboard`, {
                        method: 'POST',
                        body: JSON.stringify({ agent_slug: slug }),
                      });
                      if (res.ok) {
                        const updated = await res.json();
                        setReports(prev => prev.map(r => r.id === updated.id ? updated : r));
                      }
                    }}
                    defaultValue=""
                    style={{
                      border: 'none', background: 'none', color: '#ccc',
                      cursor: 'pointer', fontSize: '0.62rem', padding: '0 2px',
                      fontFamily: 'inherit',
                    }}
                    title="Set as dashboard"
                  >
                    <option value="" disabled>Dashboard</option>
                    <option value="reports">Reports</option>
                    <option value="hr">HR</option>
                    <option value="procurement">Procurement</option>
                  </select>
                )}
                <button
                  onClick={(e) => { e.stopPropagation(); setConfirmDelete(report.id); }}
                  onMouseEnter={e => (e.currentTarget.style.color = '#e53e3e')}
                  onMouseLeave={e => (e.currentTarget.style.color = '#ccc')}
                  style={{
                    border: 'none', background: 'none', color: '#ccc',
                    cursor: 'pointer', fontSize: '0.85rem', padding: '0 4px',
                    transition: 'color 0.15s',
                  }}
                  title="Delete report"
                >&times;</button>
              </div>
            </div>
            {report.description && (
              <div style={{ fontSize: '0.75rem', color: '#999', marginTop: 4 }}>{report.description}</div>
            )}
            <div style={{ fontSize: '0.65rem', color: '#ccc', marginTop: 8 }}>
              {report.updated_at ? new Date(report.updated_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : ''}
            </div>
          </div>
        ))}
      </div>

      {/* Delete confirmation modal */}
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
            <div style={{ fontSize: '1.5rem', marginBottom: '0.75rem' }}>&#128465;</div>
            <h3 style={{ fontSize: '0.95rem', fontWeight: 600, color: '#333', margin: '0 0 0.5rem' }}>Delete report?</h3>
            <p style={{ fontSize: '0.82rem', color: '#888', margin: '0 0 0.25rem' }}>
              <strong style={{ color: '#555' }}>{confirmDeleteTitle}</strong>
            </p>
            <p style={{ fontSize: '0.78rem', color: '#aaa', margin: '0 0 1.25rem' }}>
              This will permanently delete the report and all its charts.
            </p>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
              <button
                onClick={() => setConfirmDelete(null)}
                style={{
                  padding: '8px 20px', fontSize: '0.82rem', fontWeight: 500,
                  border: '1px solid #ddd', borderRadius: 8, backgroundColor: '#fff',
                  color: '#666', cursor: 'pointer', fontFamily: 'inherit',
                }}
              >Cancel</button>
              <button
                onClick={() => deleteReport(confirmDelete)}
                style={{
                  padding: '8px 20px', fontSize: '0.82rem', fontWeight: 600,
                  border: 'none', borderRadius: 8, backgroundColor: '#e53e3e',
                  color: '#fff', cursor: 'pointer', fontFamily: 'inherit',
                }}
              >Delete</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
