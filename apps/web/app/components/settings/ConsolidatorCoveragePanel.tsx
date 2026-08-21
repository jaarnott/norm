'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../lib/api';

/**
 * The consolidator-migration dashboard — fully DERIVED from the config DB,
 * agent bindings, MCP capability rows and tool_calls usage. Nothing here is
 * a checkbox: a tool is "done" because the config says it's a consolidator
 * and its raw twin is demoted, never because someone ticked it.
 *
 * Lifecycle per action: raw → consolidator exists (raw twin still exposed =
 * a LEAK, listed in red) → raw twin demoted (Backend) → done.
 */

interface ToolRow {
  action: string;
  status: 'consolidator' | 'backend' | 'raw';
  added_at?: string | null;
  calls_30d: number;
  agents: string[];
  mcp: boolean;
  superseded_by?: string | null;
  leak: boolean;
}

interface ConnectorRow {
  connector: string;
  counts: { consolidator: number; backend: number; raw: number };
  leaks: ToolRow[];
  backlog: ToolRow[];
  drift: { action: string; state: string }[];
  tools: ToolRow[];
}

interface Coverage {
  window_days: number;
  totals: { consolidator: number; backend: number; raw: number; leaks: number };
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
        Consolidator coverage
      </h3>
      <div style={{ fontSize: '0.74rem', color: '#777', marginBottom: 10, maxWidth: 760 }}>
        The migration to fewer, richer tools — derived live from the config, the agent bindings and the
        last {data.window_days} days of real usage. A <strong>leak</strong> is a raw tool that already has a
        consolidator but is still offered to agents.
      </div>

      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', fontSize: '0.8rem', marginBottom: 10 }}>
        <span><strong style={{ color: '#7c3aed' }}>{t.consolidator}</strong> consolidators</span>
        <span><strong style={{ color: '#92400e' }}>{t.backend}</strong> demoted backends</span>
        <span><strong style={{ color: '#666' }}>{t.raw}</strong> raw remaining</span>
        <span><strong style={{ color: t.leaks ? '#c0392b' : '#065f46' }}>{t.leaks}</strong> leaks</span>
      </div>

      {withLeaks.length > 0 && (
        <>
          <div style={label}>Leaks — converted but still exposed</div>
          {withLeaks.flatMap(c => c.leaks.map(l => (
            <div key={`${c.connector}.${l.action}`} style={row}>
              <span style={{ width: 260, fontFamily: 'monospace', fontSize: '0.72rem', color: '#c0392b' }}>
                {c.connector}.{l.action}
              </span>
              <span style={{ flex: 1, color: '#777' }}>
                use <strong>{l.superseded_by}</strong> · offered to {l.agents.join(', ') || 'MCP only'}
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
            style={{ ...row, cursor: 'pointer' }}
            onClick={() => setOpen(open === c.connector ? null : c.connector)}
          >
            <span style={{ width: 180, fontWeight: 600 }}>{c.connector}</span>
            <span style={{ flex: 1, color: '#777' }}>
              {c.counts.consolidator} consolidated · {c.counts.backend} backend · {c.counts.raw} raw
              {c.leaks.length > 0 && <span style={{ color: '#c0392b' }}> · {c.leaks.length} leaks</span>}
            </span>
            <span style={{ color: '#aaa', fontSize: '0.7rem' }}>{open === c.connector ? 'hide' : 'backlog'}</span>
          </div>
          {open === c.connector && c.backlog.slice(0, 15).map(b => (
            <div key={b.action} style={{ ...row, paddingLeft: 28, background: '#fbfaf8' }}>
              <span style={{ width: 260, fontFamily: 'monospace', fontSize: '0.72rem' }}>{b.action}</span>
              <span style={{ flex: 1, color: '#999' }}>
                {b.agents.length ? `agents: ${b.agents.join(', ')}` : 'not agent-exposed'}
              </span>
              <span style={{ width: 90, textAlign: 'right', color: '#999' }}>{b.calls_30d} calls</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
