'use client';

import { Fragment, useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import Badge from '../ui/Badge';
import Icon from '../ui/Icon';
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

interface ConnectorRow {
  connector: string;
  split: boolean;
  counts: { consolidator: number; 'built-in': number; endpoint: number };
  leaks: Row[];
  unused: Row[];
  drift: { action: string; state: string }[];
  tools: Row[];
}

interface Coverage {
  window_days: number;
  totals: { consolidator: number; 'built-in': number; endpoint: number; leaks: number };
  connectors: ConnectorRow[];
}

const section: React.CSSProperties = { borderTop: '1px solid var(--line)', marginTop: 32, paddingTop: 24, lineHeight: 1.45 };
const title: React.CSSProperties = { margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' };
// The small label over each list.
const label: React.CSSProperties = { margin: '20px 0 8px', fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--text-soft)' };
const mono: React.CSSProperties = { fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-sm)' };
const meta: React.CSSProperties = { color: 'var(--muted)', whiteSpace: 'nowrap' };
const srOnly: React.CSSProperties = { position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)', whiteSpace: 'nowrap' };

export default function ConsolidatorCoveragePanel() {
  const [data, setData] = useState<Coverage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await apiFetch('/api/connector-specs/coverage');
      if (!res.ok) throw new Error(`Error ${res.status}`);
      setData(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load coverage');
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

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
  const withLeaks = data.connectors.filter(c => c.leaks.length > 0);
  const withDrift = data.connectors.filter(c => c.drift.length > 0);
  const strong: React.CSSProperties = { fontWeight: 600, color: 'var(--text-soft)' };
  const count: React.CSSProperties = { fontWeight: 600, color: 'var(--text)' };

  return (
    <div style={section}>
      {heading}
      <p style={{ margin: '2px 0 0', maxWidth: 760, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
        An LLM only ever sees <strong style={strong}>tools</strong> — consolidators and built-ins. <strong style={strong}>API endpoints</strong> are
        the building blocks tools are made from. A <strong style={strong}>leak</strong> is an endpoint an LLM can still reach: an App
        claims it, or it is enabled on MCP. Derived live from the config and the last {data.window_days} days of usage.
      </p>

      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '6px 16px', marginTop: 12, fontSize: 'var(--fs-base)', color: 'var(--text-soft)' }}>
        <span><strong style={count}>{t.consolidator}</strong> consolidators</span>
        <span><strong style={count}>{t['built-in']}</strong> built-ins</span>
        <span><strong style={count}>{t.endpoint}</strong> API endpoints</span>
        <Badge tone={t.leaks ? 'error' : 'ok'}>{t.leaks} leaks</Badge>
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
                    <td className="num" style={meta}>{l.calls_30d} calls</td>
                  </tr>
                )))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {withDrift.length > 0 && (
        <>
          <div style={label}>Drift — config vs canonical files</div>
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
                        ? 'config differs from config/consolidators file'
                        : 'no canonical file in config/consolidators'}
                    </td>
                  </tr>
                )))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <div style={label}>By connector</div>
      <div className="n-card" style={{ overflowX: 'auto' }}>
        <table className="n-table" style={{ minWidth: 640 }}>
          <thead>
            <tr>
              <th>Connector</th>
              <th>Tools and endpoints</th>
              {/* relative: keeps the hidden label inside the card's scroller, not the page's */}
              <th className="num" style={{ position: 'relative' }}><span style={srOnly}>Unused endpoints</span></th>
            </tr>
          </thead>
          <tbody>
            {data.connectors.map(c => (
              <Fragment key={c.connector}>
                <tr
                  style={{ cursor: c.unused.length ? 'pointer' : 'default' }}
                  onClick={() => setOpen(open === c.connector ? null : c.connector)}
                >
                  <td style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>{c.connector}</td>
                  <td style={{ color: 'var(--text-soft)' }}>
                    {c.counts.consolidator} consolidators · {c.counts['built-in']} built-ins · {c.counts.endpoint} endpoints
                    {!c.split && <> <Badge tone="warn">Not split yet</Badge></>}
                    {c.leaks.length > 0 && <> <Badge tone="error">{c.leaks.length} leaks</Badge></>}
                  </td>
                  <td className="num" style={{ whiteSpace: 'nowrap' }}>
                    {c.unused.length > 0 && (
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 'var(--fs-sm)', fontWeight: 500, color: 'var(--accent)' }}>
                        {open === c.connector ? 'Hide' : `${c.unused.length} unused endpoints`}
                        <Icon icon={open === c.connector ? ChevronUp : ChevronDown} size="dense" />
                      </span>
                    )}
                  </td>
                </tr>
                {open === c.connector && c.unused.slice(0, 30).map(u => (
                  <tr key={u.action} style={{ background: 'var(--surface)' }}>
                    <td style={{ ...mono, paddingLeft: 28 }}>{u.action}</td>
                    <td style={{ color: 'var(--muted)' }}>no tool calls it</td>
                    <td className="num" style={meta}>{u.calls_30d} calls</td>
                  </tr>
                ))}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
