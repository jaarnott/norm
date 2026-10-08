'use client';

import { Fragment, useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, RefreshCw } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import Badge, { type BadgeTone } from '../ui/Badge';
import BackLink from '../ui/BackLink';
import Button from '../ui/Button';
import Icon from '../ui/Icon';
import PageState from '../ui/PageState';

/**
 * Would autopilot have got these invoices right?
 *
 * Every human receive is a free experiment in the counterfactual "accept every
 * suggestion, then receive". A receive counts as CLEAN only when the person
 * accepted all of Norm's suggestions and typed nothing themselves — accepting
 * everything but also hand-fixing a quantity means autopilot would have
 * produced a different invoice, so it counts against readiness.
 *
 * Norm's own autopilot receives are deliberately kept out of the rates (it
 * accepted everything a moment before receiving, so they are clean by
 * construction and would flatter the number). They are shown as volume.
 */

interface Row {
  id: string;
  created_at: string | null;
  venue_name: string | null;
  supplier_name: string | null;
  reference_number: string | null;
  outcome: string;
  mode: string;
  actor: string;
  suggestion_count: number;
  accepted_count: number;
  dismissed_count: number;
  pending_count: number;
  manual_edit_count: number;
  manual_fields: string[];
  issues_waved_count: number;
  // The end-state verdict recorded at receive: would autopilot (all flags
  // on) have sent the identical receive? diffs carry sent vs auto per field.
  auto?: {
    verdict: 'matched' | 'differed' | 'never_auto' | 'unscored';
    gates_needed?: string[];
    ungated?: string[];
    diffs?: { path: string; sent: unknown; auto: unknown }[];
  } | null;
}

interface SupplierRow {
  supplier_name: string;
  attempts: number;
  clean: number;
  no_suggestions: number;
  edited: number;
  dojo: number;
  autopilot_ready: number | null;
  avg_suggestions: number;
}

interface Report {
  window: { days: number; actor: string; since: string };
  totals: Record<string, number>;
  rates: { autopilot_ready: number | null; suggestion_quality: number | null; dojo: number | null };
  autopilot: Record<string, number>;
  auto?: Record<string, number>;
  flags?: { gate: string; label: string; sole_unlock: number; with_others: number }[];
  suppliers: SupplierRow[];
  top_missed_fields: { field: string; count: number }[];
  recent: Row[];
}

const OUTCOME_STYLE: Record<string, { tone: BadgeTone; label: string }> = {
  clean: { tone: 'ok', label: 'Clean' },
  no_suggestions: { tone: 'info', label: 'No changes' },
  edited: { tone: 'warn', label: 'Edited' },
  dojo: { tone: 'error', label: 'Filed' },
  not_reviewed: { tone: 'neutral', label: 'Not reviewed' },
};

function Pill({ outcome }: { outcome: string }) {
  const s = OUTCOME_STYLE[outcome] || OUTCOME_STYLE.not_reviewed;
  return <Badge tone={s.tone}>{s.label}</Badge>;
}

const pct = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v * 100)}%`);

const VERDICT_STYLE: Record<string, { tone: BadgeTone; label: string }> = {
  matched: { tone: 'ok', label: 'Would match' },
  differed: { tone: 'warn', label: 'Would differ' },
  never_auto: { tone: 'error', label: 'Needs a person' },
  unscored: { tone: 'neutral', label: 'Unscored' },
};

function VerdictPill({ verdict }: { verdict?: string | null }) {
  const s = verdict ? VERDICT_STYLE[verdict] : null;
  if (!s) return null;
  return <Badge tone={s.tone}>{s.label}</Badge>;
}

const fmtVal = (v: unknown) => (v == null || v === '' ? '—' : String(v));

function Tile({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="n-card" style={{ flex: 1, minWidth: 190, padding: '12px 16px' }}>
      <div style={{ fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--text-soft)' }}>{label}</div>
      <div style={{ fontSize: 'var(--fs-2xl)', fontWeight: 700, lineHeight: 1.25, color: 'var(--text)', fontVariantNumeric: 'tabular-nums' }}>{value}</div>
      <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{hint}</div>
    </div>
  );
}

const sectionTitle: React.CSSProperties = { marginBottom: 8, fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' };
const sectionHint: React.CSSProperties = { marginBottom: 8, fontSize: 'var(--fs-sm)', color: 'var(--muted)' };

// The filter chip from the thread list: the pressed one sits on --selected
// with a tan edge, the rest are outlined.
const chip = (on: boolean): React.CSSProperties => ({
  flex: '0 0 auto',
  whiteSpace: 'nowrap',
  padding: '4px 8px',
  fontSize: 'var(--fs-xs)',
  fontWeight: on ? 600 : 500,
  color: on ? 'var(--text)' : 'var(--text-soft)',
  backgroundColor: on ? 'var(--selected)' : 'transparent',
  border: `1px solid ${on ? 'var(--brand-soft)' : 'var(--line)'}`,
  borderRadius: 999,
  cursor: 'pointer',
});

export default function AutopilotReportPanel({ onBack }: { onBack: () => void }) {
  const [report, setReport] = useState<Report | null>(null);
  const [days, setDays] = useState(30);
  // Whose receives to score. The panel never sent this, so it always showed
  // the humans-only scope — and a venue where Norm receives everything read
  // "Nothing recorded yet" however many rows it had.
  const [actor, setActor] = useState<'user' | 'norm'>('user');
  const [loading, setLoading] = useState(true);
  const [openRow, setOpenRow] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/supplier-invoice-specs/autopilot-confidence?days=${days}&actor=${actor}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      setReport(data as Report);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the report');
    } finally {
      setLoading(false);
    }
  }, [days, actor]);
  useEffect(() => { load(); }, [load]);

  const t = report?.totals || {};
  const attempts = t.attempts || 0;

  return (
    <div style={{ lineHeight: 1.45, color: 'var(--text)' }}>
      <div style={{ marginBottom: 8 }}>
        <BackLink label="Back to supplier specs" onClick={onBack} />
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 4 }}>
        <h3 style={{ flex: 1, minWidth: 0, margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>Autopilot readiness</h3>
        <Button size="sm" icon={RefreshCw} onClick={load} disabled={loading}>
          {loading ? 'Loading…' : 'Refresh'}
        </Button>
      </div>
      <div style={{ maxWidth: 760, marginBottom: 14, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
        Every invoice a person receives is a test of what autopilot would have done. An invoice is
        <strong> clean</strong> only when they accepted all of Norm&rsquo;s suggestions and changed nothing by hand —
        accepting everything but also retyping a value means autopilot would have produced a different invoice.
        Norm&rsquo;s own autopilot receives are excluded from these rates (it accepted everything itself, so they
        prove nothing) and reported separately.
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', columnGap: 16, rowGap: 8, marginBottom: 16 }}>
        <div role="group" aria-label="Window" style={{ display: 'flex', gap: 4 }}>
          {[7, 30, 90].map((d) => (
            <button key={d} type="button" aria-pressed={days === d} onClick={() => setDays(d)} style={chip(days === d)}>
              {d} days
            </button>
          ))}
        </div>
        <div role="group" aria-label="Received by" style={{ display: 'flex', gap: 4 }}>
          {([['user', 'Received by people'], ['norm', 'Received by Norm']] as const).map(([a, label]) => (
            <button key={a} type="button" aria-pressed={actor === a} onClick={() => setActor(a)}
              title={a === 'user'
                ? 'Every invoice a person received — the only honest test of what autopilot would have done'
                : 'What autopilot has actually received. Volume, not correctness: it accepted its own suggestions a moment earlier.'}
              style={chip(actor === a)}>
              {label}
            </button>
          ))}
        </div>
      </div>

      {error && <div style={{ marginBottom: 12 }}><PageState kind="error" title={error} /></div>}

      {loading && !report && !error && <PageState kind="loading" title="Loading the report…" />}

      {/* A failed load reads as the error above, never as an empty window. */}
      {!loading && !error && attempts === 0 && (actor === 'user' ? (
        <PageState kind="empty" title="No invoices received by a person in this window."
          detail="If Norm is receiving them, switch to “Received by Norm”." />
      ) : (
        <PageState kind="empty" title="Norm hasn’t received anything itself in this window."
          detail="That starts once a venue is moved off “Approve all”." />
      ))}

      {report && attempts > 0 && (
        <>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 20 }}>
            <Tile label="Autopilot ready" value={pct(report.rates.autopilot_ready)}
              hint={`${(t.clean || 0) + (t.no_suggestions || 0)} of ${attempts} needed no human change`} />
            <Tile label="Suggestion quality" value={pct(report.rates.suggestion_quality)}
              hint="When Norm proposed changes, they were enough" />
            <Tile label="Filed for training" value={pct(report.rates.dojo)}
              hint={`${t.dojo || 0} invoice(s) Norm couldn't do`} />
          </div>

          <div style={sectionTitle}>Outcomes</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, rowGap: 8, flexWrap: 'wrap', marginBottom: 20, fontSize: 'var(--fs-sm)' }}>
            {(['clean', 'no_suggestions', 'edited', 'dojo', 'not_reviewed'] as const).map((k) => (
              <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <Pill outcome={k} /> <strong style={{ fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{t[k] || 0}</strong>
              </span>
            ))}
            {(report.autopilot?.attempts || 0) > 0 && (
              <span style={{ marginLeft: 'auto', color: 'var(--muted)' }}>
                Norm received {report.autopilot.attempts} unattended (not counted above)
              </span>
            )}
          </div>

          {(report.flags || []).length > 0 && (
            <div style={{ marginBottom: 20 }}>
              <div style={{ ...sectionTitle, marginBottom: 2 }}>What the flags would unlock</div>
              <div style={sectionHint}>
                Receives where autopilot would have sent <strong>exactly what you sent</strong> — counted
                against the flag it was waiting on. &ldquo;Alone&rdquo; means that flag was the only one missing.
              </div>
              <div className="n-card" style={{ overflowX: 'auto' }}>
                <table className="n-table">
                  <thead>
                    <tr>
                      <th>Flag</th>
                      <th className="num" style={{ width: 90 }}>Alone</th>
                      <th className="num" style={{ width: 140 }}>With other flags</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(report.flags || []).map((f) => (
                      <tr key={f.gate}>
                        <td>{f.label}</td>
                        <td className="num" style={{ fontWeight: 600, color: 'var(--ok)' }}>{f.sole_unlock}</td>
                        <td className="num" style={{ color: 'var(--muted)' }}>{f.with_others}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {report.auto && (
                <div style={{ marginTop: 8, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
                  With every flag on: <strong>{report.auto.matched || 0}</strong> of{' '}
                  {(report.auto.matched || 0) + (report.auto.differed || 0) + (report.auto.never_auto || 0)}{' '}
                  scored receives identical · {report.auto.differed || 0} would differ ·{' '}
                  {report.auto.never_auto || 0} need a person regardless
                  {(report.auto.no_flags_needed || 0) > 0 && ` · ${report.auto.no_flags_needed} needed no flag at all`}
                </div>
              )}
            </div>
          )}

          {report.suppliers.length > 0 && (
            <div style={{ marginBottom: 20 }}>
              <div style={sectionTitle}>By supplier — who is ready</div>
              <div className="n-card" style={{ overflowX: 'auto' }}>
                <table className="n-table">
                  <thead>
                    <tr>
                      <th>Supplier</th>
                      <th className="num" style={{ width: 80 }}>Invoices</th>
                      <th className="num" style={{ width: 80 }}>Clean</th>
                      <th className="num" style={{ width: 80 }}>Edited</th>
                      <th className="num" style={{ width: 70 }}>Filed</th>
                      <th className="num" style={{ width: 80 }}>Ready</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.suppliers.map((s) => (
                      <tr key={s.supplier_name}>
                        <td style={{ whiteSpace: 'nowrap' }}>{s.supplier_name}</td>
                        <td className="num">{s.attempts}</td>
                        <td className="num">{s.clean + s.no_suggestions}</td>
                        <td className="num">{s.edited}</td>
                        <td className="num">{s.dojo}</td>
                        <td className="num" style={{ fontWeight: 600, color: (s.autopilot_ready ?? 0) >= 0.9 ? 'var(--ok)' : (s.autopilot_ready ?? 0) >= 0.7 ? 'var(--warn)' : 'var(--error)' }}>
                          {pct(s.autopilot_ready)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {report.top_missed_fields.length > 0 && (
            <div style={{ marginBottom: 20 }}>
              <div style={{ ...sectionTitle, marginBottom: 2 }}>What Norm keeps missing</div>
              <div style={sectionHint}>
                Fields people had to fix by hand — the training backlog, most common first.
              </div>
              <div className="n-card" style={{ overflowX: 'auto' }}>
                <table className="n-table">
                  <tbody>
                    {report.top_missed_fields.map((f) => (
                      <tr key={f.field}>
                        <td style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-sm)' }}>{f.field}</td>
                        <td className="num" style={{ width: 80 }}>{f.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div style={{ ...sectionTitle, marginBottom: 2 }}>Recent receives</div>
          <div style={sectionHint}>
            Click a row to see what was sent vs what autopilot would have sent.
          </div>
          <div className="n-card" style={{ overflowX: 'auto' }}>
            <table className="n-table">
              <thead>
                <tr>
                  <th style={{ width: 1, paddingRight: 0 }} />
                  <th>Outcome</th>
                  <th>Autopilot</th>
                  <th>Supplier</th>
                  <th>Reference</th>
                  <th>Suggestions</th>
                  <th className="num">Received</th>
                </tr>
              </thead>
              <tbody>
                {report.recent.map((r) => (
                  <Fragment key={r.id}>
                    <tr onClick={() => setOpenRow(openRow === r.id ? null : r.id)} style={{ cursor: 'pointer' }}>
                      <td style={{ paddingRight: 0 }}>
                        <Icon icon={openRow === r.id ? ChevronDown : ChevronRight} size="dense" tone="muted" style={{ display: 'block' }} />
                      </td>
                      <td><Pill outcome={r.outcome} /></td>
                      <td><VerdictPill verdict={r.auto?.verdict} /></td>
                      <td style={{ whiteSpace: 'nowrap' }}>{r.supplier_name || '—'}</td>
                      <td style={{ color: 'var(--muted)', whiteSpace: 'nowrap' }}>{r.reference_number || '—'}</td>
                      <td style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', whiteSpace: 'nowrap' }}>
                        {r.suggestion_count > 0
                          ? `${r.accepted_count}/${r.suggestion_count} accepted${r.dismissed_count ? `, ${r.dismissed_count} dismissed` : ''}${r.pending_count ? `, ${r.pending_count} ignored` : ''}`
                          : 'no suggestions'}
                        {(r.auto?.gates_needed || []).length > 0 && ` · waiting on ${(r.auto?.gates_needed || []).length} flag(s)`}
                      </td>
                      <td className="num" style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', whiteSpace: 'nowrap' }}>
                        {r.created_at
                          ? `${new Date(r.created_at).toLocaleDateString()} ${new Date(r.created_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`
                          : ''}
                      </td>
                    </tr>
                    {openRow === r.id && (
                      <tr>
                        <td colSpan={7} style={{ padding: '10px 12px 12px 38px', background: 'var(--surface)', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
                          {(r.auto?.gates_needed || []).length > 0 && (
                            <div style={{ marginBottom: 6 }}>
                              Flags autopilot was waiting on:{' '}
                              <strong>
                                {(r.auto?.gates_needed || [])
                                  .map((g) => (report.flags || []).find((f) => f.gate === g)?.label || g)
                                  .join(', ')}
                              </strong>
                            </div>
                          )}
                          {(r.auto?.ungated || []).length > 0 && (
                            <div style={{ marginBottom: 6, color: 'var(--error)' }}>
                              Needed a person regardless: {(r.auto?.ungated || []).join(', ')}
                            </div>
                          )}
                          {(r.auto?.diffs || []).length > 0 ? (
                            <>
                              <div style={{ marginBottom: 4, fontWeight: 600, color: 'var(--text)' }}>Where autopilot would have differed</div>
                              {(r.auto?.diffs || []).map((d, i) => (
                                <div key={i} style={{ display: 'flex', flexWrap: 'wrap', columnGap: 12, rowGap: 2, padding: '2px 0', fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)' }}>
                                  <span style={{ flex: '0 1 240px', minWidth: 0, color: 'var(--muted)', overflowWrap: 'anywhere' }}>{d.path}</span>
                                  <span>you sent <strong>{fmtVal(d.sent)}</strong></span>
                                  <span style={{ color: 'var(--warn)' }}>autopilot: <strong>{fmtVal(d.auto)}</strong></span>
                                </div>
                              ))}
                            </>
                          ) : r.auto?.verdict === 'matched' ? (
                            <div style={{ color: 'var(--ok)' }}>Autopilot would have sent exactly this receive.</div>
                          ) : (
                            <div style={{ color: 'var(--muted)' }}>
                              {r.auto ? 'No field-level differences recorded.' : 'Received before end-state scoring existed — no comparison stored.'}
                            </div>
                          )}
                          {r.manual_fields.length > 0 && (
                            <div style={{ marginTop: 6, color: 'var(--muted)' }}>
                              Action-log view (secondary): hand-edited {r.manual_fields.join(', ')}
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
