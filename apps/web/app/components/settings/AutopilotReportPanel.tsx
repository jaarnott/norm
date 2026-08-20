'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../lib/api';

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

const OUTCOME_STYLE: Record<string, { bg: string; fg: string; label: string }> = {
  clean: { bg: '#d1fae5', fg: '#065f46', label: 'CLEAN' },
  no_suggestions: { bg: '#e8f0fb', fg: '#1d4ed8', label: 'NO CHANGES' },
  edited: { bg: '#fdf6e7', fg: '#8a6d3b', label: 'EDITED' },
  dojo: { bg: '#fee2e2', fg: '#991b1b', label: 'FILED' },
  not_reviewed: { bg: '#f3f4f6', fg: '#6b7280', label: 'NOT REVIEWED' },
};

function Pill({ outcome }: { outcome: string }) {
  const s = OUTCOME_STYLE[outcome] || OUTCOME_STYLE.not_reviewed;
  return (
    <span style={{ fontSize: '0.58rem', fontWeight: 700, padding: '1px 6px', borderRadius: 3, background: s.bg, color: s.fg, whiteSpace: 'nowrap' }}>
      {s.label}
    </span>
  );
}

const pct = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v * 100)}%`);

const VERDICT_STYLE: Record<string, { bg: string; fg: string; label: string }> = {
  matched: { bg: '#d1fae5', fg: '#065f46', label: 'WOULD MATCH' },
  differed: { bg: '#fdf6e7', fg: '#8a6d3b', label: 'WOULD DIFFER' },
  never_auto: { bg: '#fee2e2', fg: '#991b1b', label: 'NEEDS A PERSON' },
  unscored: { bg: '#f3f4f6', fg: '#6b7280', label: 'UNSCORED' },
};

function VerdictPill({ verdict }: { verdict?: string | null }) {
  const s = verdict ? VERDICT_STYLE[verdict] : null;
  if (!s) return null;
  return (
    <span style={{ fontSize: '0.58rem', fontWeight: 700, padding: '1px 6px', borderRadius: 3, background: s.bg, color: s.fg, whiteSpace: 'nowrap' }}>
      {s.label}
    </span>
  );
}

const fmtVal = (v: unknown) => (v == null || v === '' ? '—' : String(v));

function Tile({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div style={{ flex: 1, minWidth: 190, border: '1px solid #eee', borderRadius: 8, padding: '10px 12px', background: '#fff' }}>
      <div style={{ fontSize: '0.66rem', color: '#888', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{label}</div>
      <div style={{ fontSize: '1.5rem', fontWeight: 700, color: '#3a3a3a', lineHeight: 1.3 }}>{value}</div>
      <div style={{ fontSize: '0.66rem', color: '#999' }}>{hint}</div>
    </div>
  );
}

const rowStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px',
  borderBottom: '1px solid #f4f4f4', fontSize: '0.78rem',
};
const sectionLabel: React.CSSProperties = {
  fontSize: '0.75rem', fontWeight: 600, color: '#888', textTransform: 'uppercase', marginBottom: 6,
};

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
    <div>
      <button type="button" onClick={onBack}
        style={{ border: 'none', background: 'none', padding: 0, marginBottom: 8, fontSize: '0.74rem', color: '#8a6d3b', cursor: 'pointer', fontFamily: 'inherit' }}>
        ← Back to supplier specs
      </button>
      <h3 style={{ margin: '0 0 4px', fontSize: '1rem' }}>Autopilot readiness</h3>
      <div style={{ fontSize: '0.74rem', color: '#777', marginBottom: 12, maxWidth: 760 }}>
        Every invoice a person receives is a test of what autopilot would have done. An invoice is
        <strong> clean</strong> only when they accepted all of Norm&rsquo;s suggestions and changed nothing by hand —
        accepting everything but also retyping a value means autopilot would have produced a different invoice.
        Norm&rsquo;s own autopilot receives are excluded from these rates (it accepted everything itself, so they
        prove nothing) and reported separately.
      </div>

      <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 12 }}>
        {[7, 30, 90].map((d) => (
          <button key={d} type="button" onClick={() => setDays(d)}
            style={{ fontSize: '0.7rem', padding: '3px 10px', borderRadius: 4, cursor: 'pointer', fontFamily: 'inherit', border: '1px solid #d8d4cc', background: days === d ? '#8a6d3b' : '#fff', color: days === d ? '#fff' : '#666' }}>
            {d} days
          </button>
        ))}
        <span style={{ width: 10 }} />
        {([['user', 'Received by people'], ['norm', 'Received by Norm']] as const).map(([a, label]) => (
          <button key={a} type="button" onClick={() => setActor(a)}
            title={a === 'user'
              ? 'Every invoice a person received — the only honest test of what autopilot would have done'
              : 'What autopilot has actually received. Volume, not correctness: it accepted its own suggestions a moment earlier.'}
            style={{ fontSize: '0.7rem', padding: '3px 10px', borderRadius: 4, cursor: 'pointer', fontFamily: 'inherit', border: '1px solid #d8d4cc', background: actor === a ? '#8a6d3b' : '#fff', color: actor === a ? '#fff' : '#666' }}>
            {label}
          </button>
        ))}
        <button type="button" onClick={load} disabled={loading}
          style={{ fontSize: '0.7rem', padding: '3px 10px', borderRadius: 4, border: '1px solid #d8d4cc', background: '#fff', color: '#666', cursor: loading ? 'default' : 'pointer', fontFamily: 'inherit' }}>
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </div>

      {error && <div style={{ color: '#c0392b', fontSize: '0.78rem', marginBottom: 10 }}>{error}</div>}

      {!loading && attempts === 0 && (
        <div style={{ fontSize: '0.78rem', color: '#777', border: '1px dashed #ddd', borderRadius: 8, padding: '14px 16px', maxWidth: 720 }}>
          {actor === 'user'
            ? 'No invoices received by a person in this window. If Norm is receiving them, switch to “Received by Norm”.'
            : 'Norm hasn’t received anything itself in this window — that starts once a venue is moved off “Approve all”.'}
        </div>
      )}

      {report && attempts > 0 && (
        <>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 16 }}>
            <Tile label="Autopilot ready" value={pct(report.rates.autopilot_ready)}
              hint={`${(t.clean || 0) + (t.no_suggestions || 0)} of ${attempts} needed no human change`} />
            <Tile label="Suggestion quality" value={pct(report.rates.suggestion_quality)}
              hint="when Norm proposed changes, they were enough" />
            <Tile label="Filed for training" value={pct(report.rates.dojo)}
              hint={`${t.dojo || 0} invoice(s) Norm couldn't do`} />
          </div>

          <div style={{ ...sectionLabel }}>Outcomes</div>
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', fontSize: '0.78rem', marginBottom: 18 }}>
            {(['clean', 'no_suggestions', 'edited', 'dojo', 'not_reviewed'] as const).map((k) => (
              <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                <Pill outcome={k} /> <strong>{t[k] || 0}</strong>
              </span>
            ))}
            {(report.autopilot?.attempts || 0) > 0 && (
              <span style={{ color: '#999', marginLeft: 'auto' }}>
                Norm received {report.autopilot.attempts} unattended (not counted above)
              </span>
            )}
          </div>

          {(report.flags || []).length > 0 && (
            <div style={{ marginBottom: 18 }}>
              <div style={sectionLabel}>What the flags would unlock</div>
              <div style={{ fontSize: '0.72rem', color: '#777', marginBottom: 6 }}>
                Receives where autopilot would have sent <strong>exactly what you sent</strong> — counted
                against the flag it was waiting on. &ldquo;Alone&rdquo; means that flag was the only one missing.
              </div>
              <div style={{ ...rowStyle, fontWeight: 600, color: '#888', fontSize: '0.7rem', textTransform: 'uppercase' }}>
                <span style={{ flex: 1 }}>Flag</span>
                <span style={{ width: 90, textAlign: 'right' }}>Alone</span>
                <span style={{ width: 130, textAlign: 'right' }}>With other flags</span>
              </div>
              {(report.flags || []).map((f) => (
                <div key={f.gate} style={rowStyle}>
                  <span style={{ flex: 1 }}>{f.label}</span>
                  <span style={{ width: 90, textAlign: 'right', fontWeight: 700, color: '#065f46' }}>{f.sole_unlock}</span>
                  <span style={{ width: 130, textAlign: 'right', color: '#777' }}>{f.with_others}</span>
                </div>
              ))}
              {report.auto && (
                <div style={{ fontSize: '0.72rem', color: '#777', marginTop: 6 }}>
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
            <div style={{ marginBottom: 18 }}>
              <div style={sectionLabel}>By supplier — who is ready</div>
              <div style={{ ...rowStyle, fontWeight: 600, color: '#888', fontSize: '0.7rem', textTransform: 'uppercase' }}>
                <span style={{ flex: 1 }}>Supplier</span>
                <span style={{ width: 70, textAlign: 'right' }}>Invoices</span>
                <span style={{ width: 70, textAlign: 'right' }}>Clean</span>
                <span style={{ width: 70, textAlign: 'right' }}>Edited</span>
                <span style={{ width: 60, textAlign: 'right' }}>Filed</span>
                <span style={{ width: 70, textAlign: 'right' }}>Ready</span>
              </div>
              {report.suppliers.map((s) => (
                <div key={s.supplier_name} style={rowStyle}>
                  <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.supplier_name}</span>
                  <span style={{ width: 70, textAlign: 'right' }}>{s.attempts}</span>
                  <span style={{ width: 70, textAlign: 'right' }}>{s.clean + s.no_suggestions}</span>
                  <span style={{ width: 70, textAlign: 'right' }}>{s.edited}</span>
                  <span style={{ width: 60, textAlign: 'right' }}>{s.dojo}</span>
                  <span style={{ width: 70, textAlign: 'right', fontWeight: 700, color: (s.autopilot_ready ?? 0) >= 0.9 ? '#065f46' : (s.autopilot_ready ?? 0) >= 0.7 ? '#8a6d3b' : '#991b1b' }}>
                    {pct(s.autopilot_ready)}
                  </span>
                </div>
              ))}
            </div>
          )}

          {report.top_missed_fields.length > 0 && (
            <div style={{ marginBottom: 18 }}>
              <div style={sectionLabel}>What Norm keeps missing</div>
              <div style={{ fontSize: '0.72rem', color: '#777', marginBottom: 6 }}>
                Fields people had to fix by hand — the training backlog, most common first.
              </div>
              {report.top_missed_fields.map((f) => (
                <div key={f.field} style={rowStyle}>
                  <span style={{ flex: 1, fontFamily: 'monospace', fontSize: '0.72rem' }}>{f.field}</span>
                  <span style={{ width: 60, textAlign: 'right' }}>{f.count}</span>
                </div>
              ))}
            </div>
          )}

          <div style={sectionLabel}>Recent receives</div>
          <div style={{ fontSize: '0.72rem', color: '#777', marginBottom: 6 }}>
            Click a row to see what was sent vs what autopilot would have sent.
          </div>
          {report.recent.map((r) => (
            <div key={r.id}>
              <div
                style={{ ...rowStyle, cursor: 'pointer' }}
                onClick={() => setOpenRow(openRow === r.id ? null : r.id)}
              >
                <Pill outcome={r.outcome} />
                <VerdictPill verdict={r.auto?.verdict} />
                <span style={{ width: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.supplier_name || '—'}</span>
                <span style={{ width: 110, color: '#888', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.reference_number || '—'}</span>
                <span style={{ flex: 1, color: '#999', fontSize: '0.72rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {r.suggestion_count > 0
                    ? `${r.accepted_count}/${r.suggestion_count} accepted${r.dismissed_count ? `, ${r.dismissed_count} dismissed` : ''}${r.pending_count ? `, ${r.pending_count} ignored` : ''}`
                    : 'no suggestions'}
                  {(r.auto?.gates_needed || []).length > 0 && ` · waiting on ${(r.auto?.gates_needed || []).length} flag(s)`}
                </span>
                <span style={{ width: 90, color: '#aaa', fontSize: '0.7rem', textAlign: 'right' }}>
                  {r.created_at ? new Date(r.created_at).toLocaleDateString() : ''}
                </span>
              </div>
              {openRow === r.id && (
                <div style={{ padding: '8px 12px 12px 34px', borderBottom: '1px solid #f4f4f4', background: '#fbfaf8', fontSize: '0.74rem', color: '#555' }}>
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
                    <div style={{ marginBottom: 6, color: '#991b1b' }}>
                      Needed a person regardless: {(r.auto?.ungated || []).join(', ')}
                    </div>
                  )}
                  {(r.auto?.diffs || []).length > 0 ? (
                    <>
                      <div style={{ fontWeight: 600, marginBottom: 4 }}>Where autopilot would have differed</div>
                      {(r.auto?.diffs || []).map((d, i) => (
                        <div key={i} style={{ display: 'flex', gap: 10, padding: '2px 0', fontFamily: 'monospace', fontSize: '0.7rem' }}>
                          <span style={{ width: 240, color: '#888', overflow: 'hidden', textOverflow: 'ellipsis' }}>{d.path}</span>
                          <span>you sent <strong>{fmtVal(d.sent)}</strong></span>
                          <span style={{ color: '#8a6d3b' }}>autopilot: <strong>{fmtVal(d.auto)}</strong></span>
                        </div>
                      ))}
                    </>
                  ) : r.auto?.verdict === 'matched' ? (
                    <div style={{ color: '#065f46' }}>Autopilot would have sent exactly this receive.</div>
                  ) : (
                    <div style={{ color: '#999' }}>
                      {r.auto ? 'No field-level differences recorded.' : 'Received before end-state scoring existed — no comparison stored.'}
                    </div>
                  )}
                  {r.manual_fields.length > 0 && (
                    <div style={{ marginTop: 6, color: '#999' }}>
                      Action-log view (secondary): hand-edited {r.manual_fields.join(', ')}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </>
      )}
    </div>
  );
}
