'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../lib/api';

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

const row: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 8, padding: '4px 8px',
  borderBottom: '1px solid #f4f4f4', fontSize: '0.76rem',
};
const label: React.CSSProperties = {
  fontSize: '0.72rem', fontWeight: 600, color: '#888', textTransform: 'uppercase',
  letterSpacing: '0.04em', margin: '10px 0 4px',
};

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

  if (error) return <div style={{ fontSize: '0.78rem', color: '#c0392b' }}>{error}</div>;
  if (!data) return null;

  const t = data.totals;
  const withLeaks = data.connectors.filter(c => c.leaks.length > 0);
  const withDrift = data.connectors.filter(c => c.drift.length > 0);

  return (
    <div style={{ borderTop: '1px solid #e8e4de', marginTop: '2rem', paddingTop: '1.5rem' }}>
      <h3 style={{ margin: '0 0 4px', fontSize: '0.85rem', fontWeight: 600, color: '#666', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        Tools and endpoints
      </h3>
      <div style={{ fontSize: '0.74rem', color: '#777', marginBottom: 10, maxWidth: 760 }}>
        An LLM only ever sees <strong>tools</strong> — consolidators and built-ins. <strong>API endpoints</strong> are
        the building blocks tools are made from. A <strong>leak</strong> is an endpoint an LLM can still reach: an App
        claims it, or it is enabled on MCP. Derived live from the config and the last {data.window_days} days of usage.
      </div>

      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', fontSize: '0.8rem', marginBottom: 10 }}>
        <span><strong style={{ color: '#7c3aed' }}>{t.consolidator}</strong> consolidators</span>
        <span><strong style={{ color: '#0f766e' }}>{t['built-in']}</strong> built-ins</span>
        <span><strong style={{ color: '#666' }}>{t.endpoint}</strong> API endpoints</span>
        <span><strong style={{ color: t.leaks ? '#c0392b' : '#065f46' }}>{t.leaks}</strong> leaks</span>
      </div>

      {withLeaks.length > 0 && (
        <>
          <div style={label}>Leaks — endpoints an LLM can reach</div>
          {withLeaks.flatMap(c => c.leaks.map(l => (
            <div key={`${c.connector}.${l.action}`} style={row}>
              <span style={{ width: 300, fontFamily: 'monospace', fontSize: '0.72rem', color: '#c0392b' }}>
                {c.connector}.{l.action}
              </span>
              <span style={{ flex: 1, color: '#777' }}>
                {[l.app ? `claimed by the ${l.app} App` : '', l.mcp ? 'enabled on MCP' : ''].filter(Boolean).join(' · ')}
                {l.superseded_by ? <> · use <strong>{l.superseded_by}</strong></> : null}
              </span>
              <span style={{ width: 90, textAlign: 'right', color: '#999' }}>{l.calls_30d} calls</span>
            </div>
          )))}
        </>
      )}

      {withDrift.length > 0 && (
        <>
          <div style={label}>Drift — config vs canonical files</div>
          {withDrift.flatMap(c => c.drift.map(d => (
            <div key={`${c.connector}.${d.action}`} style={row}>
              <span style={{ width: 320, fontFamily: 'monospace', fontSize: '0.72rem' }}>
                {c.connector}.{d.action}
              </span>
              <span style={{ color: d.state === 'differs_from_file' ? '#c0392b' : '#92400e' }}>
                {d.state === 'differs_from_file'
                  ? 'config differs from config/consolidators file'
                  : 'no canonical file in config/consolidators'}
              </span>
            </div>
          )))}
        </>
      )}

      <div style={label}>By connector</div>
      {data.connectors.map(c => (
        <div key={c.connector}>
          <div
            style={{ ...row, cursor: c.unused.length ? 'pointer' : 'default' }}
            onClick={() => setOpen(open === c.connector ? null : c.connector)}
          >
            <span style={{ width: 180, fontWeight: 600 }}>{c.connector}</span>
            <span style={{ flex: 1, color: '#777' }}>
              {c.counts.consolidator} consolidators · {c.counts['built-in']} built-ins · {c.counts.endpoint} endpoints
              {!c.split && <span style={{ color: '#92400e' }}> · not split yet</span>}
              {c.leaks.length > 0 && <span style={{ color: '#c0392b' }}> · {c.leaks.length} leaks</span>}
            </span>
            {c.unused.length > 0 && (
              <span style={{ color: '#aaa', fontSize: '0.7rem' }}>
                {open === c.connector ? 'hide' : `${c.unused.length} unused endpoints`}
              </span>
            )}
          </div>
          {open === c.connector && c.unused.slice(0, 30).map(u => (
            <div key={u.action} style={{ ...row, paddingLeft: 28, background: '#fbfaf8' }}>
              <span style={{ width: 300, fontFamily: 'monospace', fontSize: '0.72rem' }}>{u.action}</span>
              <span style={{ flex: 1, color: '#999' }}>no tool calls it</span>
              <span style={{ width: 90, textAlign: 'right', color: '#999' }}>{u.calls_30d} calls</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
