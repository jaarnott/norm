'use client';

import Badge from '../ui/Badge';
import PageState from '../ui/PageState';

/**
 * Tools and API endpoints — fully DERIVED from the config DB, the App Map,
 * MCP capability rows, the consolidators' own code and tool_calls usage.
 * Nothing here is a checkbox.
 *
 * Sep 2026 rule (docs/tool-architecture-strategy.md): an LLM only ever sees
 * TOOLS — consolidators and built-ins. API ENDPOINTS are building blocks. A
 * LEAK is an endpoint an LLM can still reach (an App claims it, or it is
 * enabled on MCP); "done" is zero leaks.
 *
 * The connector specs list fetches the coverage once and hands it in: it also
 * shows each connector's counts on its own row, so this section keeps only the
 * totals and anything that needs fixing (leaks, drifted code).
 */

interface Row {
  action: string;
  status: 'consolidator' | 'built-in' | 'endpoint';
  kind: 'tool' | 'endpoint';
  added_at?: string | null;
  calls_30d: number;
  app: string | null;
  mcp: boolean;
  used_by: string[];
  superseded_by?: string | null;
  leak: boolean;
}

export interface CoverageConnector {
  connector: string;
  split: boolean;
  counts: { consolidator: number; 'built-in': number; endpoint: number };
  leaks: Row[];
  unused: Row[];
  drift: { action: string; state: string }[];
  tools: Row[];
}

export interface Coverage {
  window_days: number;
  totals: { consolidator: number; 'built-in': number; endpoint: number; leaks: number };
  connectors: CoverageConnector[];
}

const section: React.CSSProperties = { borderTop: '1px solid var(--line)', marginTop: 32, paddingTop: 24, lineHeight: 1.45 };
const title: React.CSSProperties = { margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' };
// The small label over each list.
const label: React.CSSProperties = { margin: '20px 0 8px', fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--text-soft)' };
const mono: React.CSSProperties = { fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-sm)' };
const meta: React.CSSProperties = { color: 'var(--muted)', whiteSpace: 'nowrap' };

export default function ConsolidatorCoveragePanel({ data, error }: { data: Coverage | null; error?: string | null }) {
  const heading = <h3 style={title}>Tools and endpoints</h3>;
  if (error) {
    return (
      <div style={section}>
        {heading}
        <div style={{ marginTop: 12 }}>
          <PageState kind="error" title="Couldn't load tools and endpoints" detail={error} />
        </div>
      </div>
    );
  }
  if (!data) return <div style={section}>{heading}<PageState kind="loading" title="Loading tools and endpoints…" /></div>;

  const t = data.totals;
  const n = (k: number, one: string, many = `${one}s`) => `${k} ${k === 1 ? one : many}`;
  const withLeaks = data.connectors.filter(c => c.leaks.length > 0);
  const withDrift = data.connectors.filter(c => c.drift.length > 0);
  const count: React.CSSProperties = { fontWeight: 600, color: 'var(--text)' };

  return (
    <div style={section}>
      {heading}
      <p style={{ margin: '2px 0 0', maxWidth: 760, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
        An LLM only sees tools; endpoints are what tools are built from. A leak is an endpoint an LLM can still reach.
      </p>

      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '6px 16px', marginTop: 12, fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>
        <span><strong style={count}>{t.consolidator}</strong> {t.consolidator === 1 ? 'consolidator' : 'consolidators'}</span>
        <span><strong style={count}>{t['built-in']}</strong> {t['built-in'] === 1 ? 'built-in' : 'built-ins'}</span>
        <span><strong style={count}>{t.endpoint}</strong> API {t.endpoint === 1 ? 'endpoint' : 'endpoints'}</span>
        <Badge tone={t.leaks ? 'error' : 'ok'}>{n(t.leaks, 'leak')}</Badge>
        {withDrift.length > 0 && (
          <Badge tone="warn">{(k => `${n(k, 'consolidator')} out of step with ${k === 1 ? 'its reviewed file' : 'their reviewed files'}`)(withDrift.reduce((k, c) => k + c.drift.length, 0))}</Badge>
        )}
      </div>

      {withLeaks.length > 0 && (
        <>
          <div style={label}>Leaks — endpoints an LLM can reach</div>
          <div className="n-card" style={{ overflowX: 'auto' }}>
            <table className="n-table" style={{ minWidth: 640 }}>
              <thead>
                <tr>
                  <th>Endpoint</th>
                  <th>How an LLM reaches it</th>
                  <th className="num">Last {data.window_days} days</th>
                </tr>
              </thead>
              <tbody>
                {withLeaks.flatMap(c => c.leaks.map(l => (
                  <tr key={`${c.connector}.${l.action}`}>
                    <td style={{ ...mono, color: 'var(--error)' }}>{c.connector}.{l.action}</td>
                    <td style={{ color: 'var(--text-soft)' }}>
                      {[l.app ? `claimed by the ${l.app} App` : '', l.mcp ? 'enabled on MCP' : ''].filter(Boolean).join(' · ')}
                      {l.superseded_by ? <> · use <strong style={count}>{l.superseded_by}</strong></> : null}
                    </td>
                    <td className="num" style={meta}>{n(l.calls_30d, 'call')}</td>
                  </tr>
                )))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {withDrift.length > 0 && (
        <>
          <div style={label}>Consolidator code vs the reviewed files</div>
          <div className="n-card" style={{ overflowX: 'auto' }}>
            <table className="n-table" style={{ minWidth: 560 }}>
              <thead>
                <tr>
                  <th>Consolidator</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {withDrift.flatMap(c => c.drift.map(d => (
                  <tr key={`${c.connector}.${d.action}`}>
                    <td style={mono}>{c.connector}.{d.action}</td>
                    <td style={{ color: d.state === 'differs_from_file' ? 'var(--error)' : 'var(--warn)' }}>
                      {d.state === 'differs_from_file'
                        ? 'Live code differs from its file in config/consolidators — the next sync script overwrites it'
                        : 'No reviewed file in config/consolidators (edited here only)'}
                    </td>
                  </tr>
                )))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
