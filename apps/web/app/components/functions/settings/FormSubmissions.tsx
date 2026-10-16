'use client';

/**
 * What a web form has received: when, from whom, where the lead came from
 * (campaign tags, an ad click or the referring site), whether the email went
 * out, and the enquiry it created. A row opens the full answers.
 */
import { useEffect, useId, useState } from 'react';
import { ExternalLink } from 'lucide-react';
import Badge, { type BadgeTone } from '../../ui/Badge';
import Button from '../../ui/Button';
import Dialog from '../../ui/Dialog';
import PageState from '../../ui/PageState';
import { fnGet } from '../shared/api';
import { instantLabel } from '../shared/format';
import { errorText } from './errors';
import { answerText, sourceSummary } from './formFields';
import { openFnPage } from './nav';
import type { FormField, FormSubmission, WebForm } from './types';
import { CheckboxRow, Notice, muted } from './ui';

const PAGE = 25;

const DELIVERY: Record<string, { tone: BadgeTone; label: string }> = {
  sent: { tone: 'ok', label: 'Emailed' },
  pending: { tone: 'info', label: 'Sending' },
  failed: { tone: 'error', label: 'Email failed' },
  skipped: { tone: 'neutral', label: 'Not emailed' },
};

const ATTRIBUTION_LABELS: Record<string, string> = {
  utm_source: 'Source',
  utm_medium: 'Medium',
  utm_campaign: 'Campaign',
  utm_term: 'Search term',
  utm_content: 'Ad content',
  gclid: 'Google click id',
  gbraid: 'Google click id (app)',
  wbraid: 'Google click id (web)',
  referrer: 'Came from',
  landing_page: 'Landing page',
};

/** An answer as the customer saw it: choice values shown by their labels. */
function shown(field: FormField | undefined, value: unknown): string {
  const options = field && Array.isArray(field.options) ? field.options : null;
  if (!options) return answerText(value);
  const label = (v: unknown) => options.find((o) => o.value === String(v))?.label ?? String(v);
  if (Array.isArray(value)) return value.map(label).join(', ');
  if (typeof value === 'string') return label(value);
  return answerText(value);
}

export default function FormSubmissions({ form, timeZone }: { form: WebForm; timeZone: string }) {
  const [includeSpam, setIncludeSpam] = useState(false);
  const [pages, setPages] = useState(1);
  const [state, setState] = useState<{ key: string; rows: FormSubmission[]; more: boolean; error: string | null } | null>(null);
  const [open, setOpen] = useState<FormSubmission | null>(null);
  const toggleId = useId();
  const key = `${form.id}:${includeSpam}:${pages}`;

  useEffect(() => {
    let alive = true;
    fnGet<{ submissions: FormSubmission[] }>(`/forms/${form.id}/submissions`, { limit: PAGE * pages, offset: 0, include_spam: includeSpam }).then(
      (r) => alive && setState({ key, rows: r.submissions, more: r.submissions.length >= PAGE * pages, error: null }),
      (e) => alive && setState((s) => ({ key, rows: s?.rows ?? [], more: false, error: errorText(e) })),
    );
    return () => {
      alive = false;
    };
  }, [form.id, includeSpam, pages, key]);

  const fields = form.fields ?? [];
  const nameKey = fields.find((f) => f.type === 'name')?.key;
  const emailKey = fields.find((f) => f.type === 'email')?.key;
  const loading = state?.key !== key;
  const rows = state?.rows ?? [];

  if (!state && loading) return <PageState kind="loading" title="Loading submissions…" />;

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
        <span style={muted}>
          {form.submissions != null ? `${form.submissions} received` : `Latest ${rows.length}`}
          {form.last_submitted_at ? ` · last ${instantLabel(form.last_submitted_at, timeZone)}` : ''}
          {includeSpam ? ' · spam included' : ''}
        </span>
        <CheckboxRow id={toggleId} checked={includeSpam} onChange={(on) => { setIncludeSpam(on); setPages(1); }} label="Show spam" />
      </div>
      {state?.error && <Notice tone="error" style={{ marginBottom: 10 }}>{state.error}</Notice>}
      {rows.length === 0 && !loading ? (
        <div className="n-card"><PageState kind="empty" title="Nothing received yet" detail="Submissions appear here as soon as someone sends the form." /></div>
      ) : (
        <div className="n-card" style={{ overflowX: 'auto' }}>
          <table className="n-table">
            <thead>
              <tr>
                <th>Received</th>
                <th>From</th>
                <th>Source</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => {
                const who = (nameKey && answerText(s.payload[nameKey])) || (emailKey && answerText(s.payload[emailKey])) || s.ref_code || 'Someone';
                const d = s.delivery_status ? DELIVERY[s.delivery_status] : null;
                return (
                  <tr key={s.id} onClick={() => setOpen(s)} style={{ cursor: 'pointer' }}>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      <button type="button" onClick={(e) => { e.stopPropagation(); setOpen(s); }}
                        style={{ padding: 0, border: 'none', background: 'none', font: 'inherit', color: 'var(--text)', cursor: 'pointer', textAlign: 'left' }}>
                        {instantLabel(s.submitted_at, timeZone)}
                      </button>
                    </td>
                    <td style={{ minWidth: 160 }}>
                      <div style={{ fontWeight: 500 }}>{who}</div>
                      {emailKey && nameKey && <div style={{ ...muted, fontSize: 'var(--fs-xs)' }}>{answerText(s.payload[emailKey])}</div>}
                    </td>
                    <td style={{ minWidth: 120 }}>{sourceSummary(s.attribution)}</td>
                    <td>
                      <span style={{ display: 'inline-flex', gap: 6, flexWrap: 'wrap' }}>
                        {s.spam_reason && <Badge tone="error" title={s.spam_reason}>Spam</Badge>}
                        {s.enquiry_id && <Badge tone="ok">Enquiry</Badge>}
                        {d && !s.spam_reason && <Badge tone={d.tone}>{d.label}</Badge>}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {(state?.more || loading) && rows.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <Button size="sm" onClick={() => setPages((p) => p + 1)} disabled={loading}>{loading ? 'Loading…' : 'Show more'}</Button>
        </div>
      )}
      {open && <SubmissionDialog submission={open} form={form} timeZone={timeZone} onClose={() => setOpen(null)} />}
    </div>
  );
}

function SubmissionDialog({ submission: s, form, timeZone, onClose }: {
  submission: FormSubmission;
  form: WebForm;
  timeZone: string;
  onClose: () => void;
}) {
  const fields = form.fields ?? [];
  const known = new Set(fields.map((f) => f.key));
  const answers = [
    ...fields.filter((f) => f.type !== 'section').map((f) => ({ key: f.key, label: f.label, value: shown(f, s.payload[f.key]) })),
    // Answers to fields that have since been removed from the form.
    ...Object.keys(s.payload).filter((k) => !known.has(k)).map((k) => ({ key: k, label: k, value: answerText(s.payload[k]) })),
  ].filter((a) => a.value);
  const attribution = Object.entries(s.attribution ?? {});
  const d = s.delivery_status ? DELIVERY[s.delivery_status] : null;
  const dl = { display: 'grid', gridTemplateColumns: 'minmax(110px, 34%) minmax(0, 1fr)', gap: '6px 14px', margin: 0, fontSize: 'var(--fs-base)' } as const;
  return (
    <Dialog title={`Submission ${s.ref_code ?? ''}`.trim()} width={620} onClose={onClose} closeButton>
      <p style={{ ...muted, marginTop: 0 }}>Received {instantLabel(s.submitted_at, timeZone, true)}</p>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
        {s.spam_reason && <Badge tone="error">Spam: {s.spam_reason}</Badge>}
        {d && <Badge tone={d.tone}>{d.label}</Badge>}
      </div>
      {s.delivery_error && <Notice tone="error" style={{ marginBottom: 12 }}>The email didn’t go: {s.delivery_error}</Notice>}
      <dl style={dl}>
        {answers.map((a) => (
          <div key={a.key} style={{ display: 'contents' }}>
            <dt style={{ color: 'var(--muted)' }}>{a.label}</dt>
            <dd style={{ margin: 0, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{a.value}</dd>
          </div>
        ))}
      </dl>
      <h3 style={{ margin: '18px 0 8px', fontSize: 'var(--fs-base)', fontWeight: 600 }}>Where it came from</h3>
      {attribution.length === 0 ? (
        <p style={muted}>Direct — no campaign tags or referring site.</p>
      ) : (
        <dl style={dl}>
          {attribution.map(([k, v]) => (
            <div key={k} style={{ display: 'contents' }}>
              <dt style={{ color: 'var(--muted)' }}>{ATTRIBUTION_LABELS[k] ?? k}</dt>
              <dd style={{ margin: 0, overflowWrap: 'anywhere' }}>{v}</dd>
            </div>
          ))}
        </dl>
      )}
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 20, flexWrap: 'wrap' }}>
        {s.enquiry_id && (
          <Button icon={ExternalLink} onClick={() => openFnPage('enquiries', { enquiry: s.enquiry_id })}>Open enquiry</Button>
        )}
        <Button onClick={onClose}>Close</Button>
      </div>
    </Dialog>
  );
}
