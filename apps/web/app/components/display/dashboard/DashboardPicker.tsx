'use client';

import { useState, useEffect } from 'react';
import { apiFetch } from '../../../lib/api';
import { memberName } from '../../../lib/memberNames';
import { Share2, LoaderCircle, Check, Trash2 } from 'lucide-react';
import PageHeader from '../../ui/PageHeader';
import PageState from '../../ui/PageState';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import IconButton from '../../ui/IconButton';
import Icon from '../../ui/Icon';

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
  /** A menu PAGE (not a conversation): draw the page header. */
  asPage?: boolean;
}

const SPIN: React.CSSProperties = { animation: 'n-spin 1s linear infinite' };

export default function DashboardPicker({ agentSlug, onDashboardSelected, asPage = false }: DashboardPickerProps) {
  const [data, setData] = useState<AvailableData | null>(null);
  const [loading, setLoading] = useState(true);
  // Display only: a failed load reads as an error, not as "no dashboards".
  const [loadFailed, setLoadFailed] = useState(false);
  const [acting, setActing] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  useEffect(() => {
    apiFetch(`/api/reports/dashboards/${agentSlug}/available`)
      .then(r => { if (!r.ok) setLoadFailed(true); return r.ok ? r.json() : null; })
      .then(d => { if (d) setData(d); })
      .catch(() => { setLoadFailed(true); })
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

  // "time_attendance" reads as "Time & attendance dashboards", never the slug.
  const title = agentSlug ? `${memberName(agentSlug)} dashboards` : 'Dashboards';
  const intro = 'Choose a dashboard to display, or create one from a template.';
  const activeId = data?.active_id;
  const templates = data?.templates || [];
  const own = data?.own || [];
  const shared = data?.shared || [];
  const hasContent = templates.length > 0 || own.length > 0 || shared.length > 0;
  const showIntro = !loading && !loadFailed && hasContent;

  const header = asPage ? (
    <PageHeader title={title} meta={showIntro ? intro : undefined} />
  ) : (
    <div style={{ marginBottom: 16 }}>
      <h2 style={{ margin: 0, fontSize: 'var(--fs-md)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>{title}</h2>
      {showIntro && <p style={{ margin: '2px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{intro}</p>}
    </div>
  );

  if (loading) {
    return (
      <div style={asPage ? undefined : { padding: 8 }}>
        {header}
        <PageState kind="loading" title="Loading dashboards…" />
      </div>
    );
  }

  const cardGrid: React.CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12 };

  return (
    <div style={asPage ? undefined : { padding: 8 }}>
      {header}

      {loadFailed ? (
        <PageState kind="error" title="Couldn’t load dashboards" detail="Norm couldn’t reach the dashboard list. Try again in a moment." />
      ) : !hasContent && (
        <PageState kind="empty" title="No dashboards available yet." detail="Ask Norm to build a dashboard, or check the Templates tab in Settings." />
      )}

      {/* Templates */}
      {templates.length > 0 && (
        <Section title="Templates" count={templates.length} noun="template" large={asPage}>
          <div style={cardGrid}>
            {templates.map(t => (
              <div
                key={t.slug}
                className="n-card"
                onClick={() => instantiateTemplate(t.slug)}
                style={{ padding: 16, cursor: 'pointer', transition: 'border-color 0.12s' }}
                onMouseEnter={e => (e.currentTarget.style.borderColor = 'var(--line-strong)')}
                onMouseLeave={e => (e.currentTarget.style.borderColor = 'var(--line)')}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 'var(--fs-base)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>{t.title}</div>
                    <div style={{ marginTop: 2, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{t.chart_count} chart{t.chart_count !== 1 ? 's' : ''}</div>
                  </div>
                  {acting === `tmpl-${t.slug}` ? (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flex: '0 0 auto', height: 30, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
                      <Icon icon={LoaderCircle} size="dense" tone="muted" style={SPIN} /> Creating…
                    </span>
                  ) : (
                    // No handler of its own: the click is the card's.
                    <Button size="sm">Use template</Button>
                  )}
                </div>
                {t.description && <div style={{ marginTop: 8, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{t.description}</div>}
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* My Dashboards */}
      {own.length > 0 && (
        <Section title="My dashboards" count={own.length} noun="dashboard" large={asPage}>
          <div style={cardGrid}>
            {own.map(d => (
              <DashboardCard
                key={d.id}
                dashboard={d}
                isActive={activeId === d.id}
                acting={acting === d.id}
                onSetActive={() => setActive(d.id)}
                onDelete={() => setConfirmDelete(d.id)}
              />
            ))}
          </div>
        </Section>
      )}

      {/* Shared Dashboards */}
      {shared.length > 0 && (
        <Section title="Shared" count={shared.length} noun="dashboard" large={asPage}>
          <div style={cardGrid}>
            {shared.map(d => (
              <DashboardCard
                key={d.id}
                dashboard={d}
                isActive={activeId === d.id}
                acting={acting === d.id}
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
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-dashboard-title"
            className="n-card"
            style={{ padding: 20, maxWidth: 380, width: 'calc(100% - 32px)', boxSizing: 'border-box', boxShadow: '0 10px 40px rgba(0,0,0,0.15)' }}
          >
            <h3 id="delete-dashboard-title" style={{ fontSize: 'var(--fs-md)', fontWeight: 600, color: 'var(--text)', margin: '0 0 8px' }}>Delete dashboard?</h3>
            <p style={{ fontSize: 'var(--fs-base)', color: 'var(--text)', margin: '0 0 4px' }}>
              <strong style={{ fontWeight: 600 }}>{own.find(d => d.id === confirmDelete)?.title}</strong>
            </p>
            <p style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', margin: '0 0 20px' }}>This will permanently delete this dashboard and all its charts.</p>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap' }}>
              <Button onClick={() => setConfirmDelete(null)}>Cancel</Button>
              <Button variant="danger" icon={Trash2} onClick={() => deleteDashboard(confirmDelete)}>Delete</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/** A group of cards. On a page its title is a section title (18px); in a
 *  conversation it stays under the 16px view title. */
function Section({ title, count, noun, large, children }: {
  title: string; count: number; noun: string; large: boolean; children: React.ReactNode;
}) {
  return (
    <section style={{ marginBottom: 24 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
        <h3 style={{ margin: 0, fontSize: large ? 'var(--fs-lg)' : 'var(--fs-base)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>{title}</h3>
        <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{count} {noun}{count !== 1 ? 's' : ''}</span>
      </div>
      {children}
    </section>
  );
}

function DashboardCard({ dashboard, isActive, acting, onSetActive, onDelete, shared }: {
  dashboard: DashboardSummary; isActive: boolean; acting: boolean;
  onSetActive: () => void; onDelete?: () => void; shared?: boolean;
}) {
  // The active dashboard wears the selected border (tan); others firm up on hover.
  const restingBorder = isActive ? 'var(--brand-soft)' : 'var(--line)';
  return (
    <div
      className="n-card"
      onClick={onSetActive}
      style={{
        padding: 16, borderColor: restingBorder,
        cursor: acting ? 'not-allowed' : 'pointer', transition: 'border-color 0.12s',
      }}
      onMouseEnter={e => { if (!isActive) e.currentTarget.style.borderColor = 'var(--line-strong)'; }}
      onMouseLeave={e => { if (!isActive) e.currentTarget.style.borderColor = 'var(--line)'; }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 'var(--fs-base)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>{dashboard.title}</div>
          <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 6, marginTop: 2, flexWrap: 'wrap' }}>
            {dashboard.charts.length} chart{dashboard.charts.length !== 1 ? 's' : ''}
            {dashboard.status === 'saved' && <Badge tone="ok">Saved</Badge>}
            {shared && <Icon icon={Share2} size="meta" tone="muted" label="Shared" />}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, flex: '0 0 auto' }}>
          {isActive ? (
            <Badge tone="ok"><Icon icon={Check} size="meta" strokeWidth={2} /> Active</Badge>
          ) : acting ? (
            <Icon icon={LoaderCircle} size="dense" tone="muted" style={SPIN} />
          ) : (
            // No handler of its own: the click is the card's.
            <Button size="sm">Set active</Button>
          )}
          {onDelete && (
            <IconButton
              icon={Trash2}
              label="Delete dashboard"
              iconSize={16}
              onClick={e => { e.stopPropagation(); onDelete(); }}
            />
          )}
        </div>
      </div>
      {dashboard.description && <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', marginTop: 8 }}>{dashboard.description}</div>}
      {dashboard.updated_at && (
        <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginTop: 8 }}>
          Updated {new Date(dashboard.updated_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
        </div>
      )}
    </div>
  );
}
