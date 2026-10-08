'use client';

import { useState, useEffect, useCallback } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { apiFetch } from '../../lib/api';
import { memberName } from '../../lib/memberNames';
import type { SavedReport, WidgetAction } from '../../types';
import { BarChart3, LoaderCircle, Share2, Trash2 } from 'lucide-react';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import IconButton from '../ui/IconButton';
import Icon from '../ui/Icon';

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

const INTRO = 'Build reports from data, use templates, or view shared reports from your team.';
const DIVIDER = '1px solid var(--line)';
const META: CSSProperties = { fontSize: 'var(--fs-xs)', color: 'var(--muted)', whiteSpace: 'nowrap' };
const plural = (n: number, word: string) => `${n} ${word}${n !== 1 ? 's' : ''}`;

export default function SavedReportsBoard({ props, onAction }: Props) {
  // A page instance (FunctionalPage marks it) gets the page header; the same
  // board in a conversation or a dashboard tile stays compact.
  const isPage = !!props?.persistVenue;
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

  const header = isPage ? (
    <PageHeader title="Reports" meta={INTRO} />
  ) : (
    <div style={{ marginBottom: 16 }}>
      <h2 style={{ margin: 0, fontSize: 'var(--fs-md)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>Reports</h2>
      <p style={{ margin: '2px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{INTRO}</p>
    </div>
  );

  // The page frame supplies the gutters; in a conversation or a dashboard tile
  // the board keeps its own.
  const frame: CSSProperties | undefined = isPage ? undefined : { padding: 16 };

  if (loading) return <div style={frame}>{header}<PageState kind="loading" title="Loading reports…" /></div>;

  const confirmDeleteTitle = confirmDelete ? reports.find(r => r.id === confirmDelete)?.title : '';

  return (
    <div style={frame}>
      {header}

      {/* Templates section */}
      {templates.length > 0 && (
        <Section title="Templates" count={templates.length} page={isPage}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 12 }}>
            {templates.map(t => (
              <div
                key={t.slug}
                className="n-card"
                style={{ padding: 16, minWidth: 0, cursor: 'pointer', transition: 'background-color 0.12s, border-color 0.12s' }}
                onClick={() => instantiateTemplate(t.slug)}
                onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--surface)'; e.currentTarget.style.borderColor = 'var(--line-strong)'; }}
                onMouseLeave={e => { e.currentTarget.style.backgroundColor = ''; e.currentTarget.style.borderColor = ''; }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                  <Badge>{memberName(t.agent_slug)}</Badge>
                  <span style={META}>{plural(t.chart_count, 'chart')}</span>
                </div>
                <div style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>{t.title}</div>
                {t.description && <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{t.description}</div>}
                {instantiating === t.slug && (
                  <div role="status" style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 10, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
                    <Icon icon={LoaderCircle} size="dense" style={{ animation: 'n-spin 1s linear infinite' }} /> Creating…
                  </div>
                )}
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Shared reports section */}
      {sharedReports.length > 0 && (
        <Section title="Shared" count={sharedReports.length} page={isPage}>
          <div className="n-card" style={{ overflow: 'hidden' }}>
            {sharedReports.map((report, i) => (
              <ReportRow key={report.id} report={report} first={i === 0} onClick={() => openReport(report.id)} shared />
            ))}
          </div>
        </Section>
      )}

      {/* My reports section */}
      <Section title="My reports" count={reports.length} page={isPage}>
        {reports.length === 0 ? (
          <PageState
            kind="empty"
            title="No reports yet"
            detail={<>Ask Norm for data, then click &ldquo;Report&rdquo; on a chart to start building.</>}
          />
        ) : (
          <div className="n-card" style={{ overflow: 'hidden' }}>
            {reports.map((report, i) => (
              <ReportRow
                key={report.id}
                report={report}
                first={i === 0}
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
          backgroundColor: 'rgba(26, 26, 26, 0.35)', padding: 16,
        }}>
          <div role="alertdialog" aria-modal="true" aria-labelledby="delete-report-title" style={{
            backgroundColor: 'var(--bg)', borderRadius: 'var(--radius-lg)', padding: 24, maxWidth: 400, width: '100%',
            boxShadow: '0 12px 40px rgba(26, 26, 26, 0.18)',
          }}>
            <h3 id="delete-report-title" style={{ margin: '0 0 8px', fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>Delete report?</h3>
            <p style={{ margin: '0 0 4px', fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)', overflowWrap: 'anywhere' }}>
              {confirmDeleteTitle}
            </p>
            <p style={{ margin: '0 0 20px', fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>
              This will permanently delete the report and all its charts.
            </p>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'flex-end' }}>
              <Button onClick={() => setConfirmDelete(null)}>Cancel</Button>
              <Button variant="danger" icon={Trash2} onClick={() => deleteReport(confirmDelete)}>Delete</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// --- Sub-components ---

function Section({ title, count, page, children }: {
  title: string; count?: number; page: boolean; children: ReactNode;
}) {
  return (
    <section style={{ marginBottom: 28 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
        <h2 style={{ margin: 0, fontSize: page ? 'var(--fs-lg)' : 'var(--fs-md)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>{title}</h2>
        {count !== undefined && <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', fontVariantNumeric: 'tabular-nums' }}>{count}</span>}
      </div>
      {children}
    </section>
  );
}

function ReportRow({ report, first, onClick, onDelete, onPromote, shared }: {
  report: SavedReport;
  first: boolean;
  onClick: () => void;
  onDelete?: () => void;
  onPromote?: (agentSlug: string) => void;
  shared?: boolean;
}) {
  const updated = report.updated_at
    ? new Date(report.updated_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
    : '';
  return (
    <div
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '8px 12px', padding: '12px 16px',
        borderTop: first ? undefined : DIVIDER, cursor: 'pointer', transition: 'background-color 0.12s',
      }}
      onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'var(--surface-alt)'; }}
      onMouseLeave={e => { e.currentTarget.style.backgroundColor = ''; }}
    >
      <div style={{ flex: '1 1 240px', minWidth: 0, display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <span style={{
          flex: '0 0 auto', display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          width: 32, height: 32, borderRadius: 'var(--radius)', background: 'var(--surface-alt)', color: 'var(--icon)',
        }}>
          <Icon icon={shared ? Share2 : BarChart3} size="dense" />
        </span>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)', overflowWrap: 'anywhere' }}>{report.title}</span>
            {report.status === 'saved' && <Badge tone="ok">Saved</Badge>}
            {report.status === 'draft' && <Badge tone="info">Draft</Badge>}
            {report.is_dashboard && <Badge>Dashboard</Badge>}
            {report.is_published && <Badge>Shared</Badge>}
          </div>
          {report.description && (
            <div style={{ marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{report.description}</div>
          )}
        </div>
      </div>
      <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12 }}>
        <span style={META}>
          {plural(report.charts.length, 'chart')}{updated ? ` · ${updated}` : ''}
        </span>
        {(onPromote && !report.is_dashboard) || onDelete ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }} onClick={e => e.stopPropagation()}>
            {onPromote && !report.is_dashboard && (
              <select
                className="n-select"
                onChange={e => { if (e.target.value) { onPromote(e.target.value); e.target.value = ''; } }}
                defaultValue=""
                aria-label="Set as dashboard"
                title="Set as dashboard"
                style={{ fontSize: 'var(--fs-sm)' }}
              >
                <option value="" disabled>Dashboard</option>
                <option value="reports">Reports</option>
                <option value="hr">HR</option>
                <option value="procurement">Procurement</option>
              </select>
            )}
            {onDelete && <IconButton icon={Trash2} label="Delete report" iconSize={16} onClick={onDelete} />}
          </div>
        ) : null}
      </div>
    </div>
  );
}
