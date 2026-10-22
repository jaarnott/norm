'use client';

import { useEffect, useState } from 'react';
import { Check, Copy, LoaderCircle, Share2, Trash2 } from 'lucide-react';
import { apiFetch } from '../../../lib/api';
import { memberName } from '../../../lib/memberNames';
import PageHeader from '../../ui/PageHeader';
import PageState from '../../ui/PageState';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import IconButton from '../../ui/IconButton';
import Icon from '../../ui/Icon';

/** A dashboard as /api/dashboards describes it (services/dashboards.py). */
export interface DashboardRef {
  ref: string;
  slug: string;
  name: string;
  description: string | null;
  agent: string;
  source: 'mine' | 'shared' | 'template';
  owner: string;
  shared: boolean;
  can_change: boolean;
  version: number | null;
}

interface Available {
  mine: DashboardRef[];
  shared: DashboardRef[];
  templates: DashboardRef[];
  chosen: string | null;
}

const SPIN: React.CSSProperties = { animation: 'n-spin 1s linear infinite' };

/**
 * Change dashboard: the person's own, those shared with their organisation,
 * and Norm's templates. Using one never copies it — they see it as it is and
 * get its updates; "Make a copy" is for changing one.
 */
export default function PageChooser({ agentSlug, onChosen, onCancel }: {
  agentSlug: string;
  onChosen: () => void;
  /** back to the dashboard on show, when there is one */
  onCancel?: () => void;
}) {
  const [data, setData] = useState<Available | null>(null);
  const [failed, setFailed] = useState(false);
  const [acting, setActing] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = () => {
    apiFetch(`/api/dashboards/${agentSlug}/available`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d) => setData(d))
      .catch(() => setFailed(true));
  };
  useEffect(load, [agentSlug]);

  const act = async (key: string, path: string, init: RequestInit) => {
    setActing(key);
    setMessage(null);
    const res = await apiFetch(path, init);
    setActing(null);
    if (res.ok) return true;
    const json = await res.json().catch(() => ({}));
    setMessage(json?.detail || `That didn’t work (error ${res.status}).`);
    return false;
  };

  const use = async (d: DashboardRef) => {
    if (await act(d.ref, `/api/dashboards/${agentSlug}/use`, { method: 'POST', body: JSON.stringify({ ref: d.ref }) })) onChosen();
  };
  const copy = async (d: DashboardRef) => {
    if (await act(`copy:${d.ref}`, '/api/dashboards/copy', { method: 'POST', body: JSON.stringify({ ref: d.ref }) })) onChosen();
  };
  const remove = async (d: DashboardRef) => {
    if (!window.confirm(`Remove “${d.name}”? Anyone using it goes back to their next choice.`)) return;
    if (await act(`remove:${d.ref}`, `/api/dashboards/${encodeURIComponent(d.slug)}`, { method: 'DELETE' })) load();
  };

  const title = `${memberName(agentSlug)} dashboards`;
  const groups: [string, DashboardRef[]][] = data
    ? [['Yours', data.mine], ['Shared with you', data.shared], ['Norm’s templates', data.templates]]
    : [];
  const empty = data && !data.mine.length && !data.shared.length && !data.templates.length;

  return (
    <div className="n-page">
      <PageHeader
        title={title}
        meta="Use one as it is: you’ll see its updates. Make a copy to change one."
        actions={onCancel ? <Button size="sm" variant="secondary" onClick={onCancel}>Back</Button> : undefined}
      />
      {message && <div role="alert" style={{ color: 'var(--error)', fontSize: 'var(--fs-sm)', marginBottom: 12 }}>{message}</div>}
      {failed && <PageState kind="error" title="Couldn’t load dashboards" detail="Try again in a moment." />}
      {!data && !failed && <PageState kind="loading" title="Loading dashboards…" />}
      {empty && <PageState kind="empty" title="No dashboards here yet." detail="Ask Norm to build one." />}
      {groups.filter(([, list]) => list.length > 0).map(([heading, list]) => (
        <section key={heading} style={{ marginBottom: 24 }}>
          <h3 style={{ margin: '0 0 10px', fontSize: 'var(--fs-lg)', fontWeight: 600, color: 'var(--text)' }}>{heading}</h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12 }}>
            {list.map((d) => {
              const inUse = data?.chosen === d.ref;
              return (
                <div key={d.ref} className="n-card" style={{ padding: 16, borderColor: inUse ? 'var(--brand-soft)' : 'var(--line)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontSize: 'var(--fs-base)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>{d.name}</div>
                      <div style={{ marginTop: 2, fontSize: 'var(--fs-xs)', color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                        {d.source === 'mine' ? 'Yours' : d.source === 'template' ? 'Norm’s template' : `${d.owner}’s`}
                        {d.source === 'mine' && d.shared && <><Icon icon={Share2} size="meta" tone="muted" /> shared</>}
                      </div>
                    </div>
                    {inUse
                      ? <Badge tone="ok"><Icon icon={Check} size="meta" strokeWidth={2} /> In use</Badge>
                      : acting === d.ref
                        ? <Icon icon={LoaderCircle} size="dense" tone="muted" style={SPIN} />
                        : <Button size="sm" onClick={() => use(d)} disabled={acting !== null}>Use</Button>}
                  </div>
                  {d.description && <div style={{ marginTop: 8, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{d.description}</div>}
                  <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
                    {d.source !== 'mine' && (
                      <Button size="sm" variant="quiet" icon={Copy} disabled={acting !== null} onClick={() => copy(d)}>
                        {acting === `copy:${d.ref}` ? 'Copying…' : 'Make a copy'}
                      </Button>
                    )}
                    {d.source === 'mine' && (
                      <IconButton icon={Trash2} label={`Remove ${d.name}`} iconSize={16} onClick={() => remove(d)} />
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
