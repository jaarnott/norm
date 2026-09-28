'use client';

/**
 * Settings → Connections, for civilians: the systems Norm works in, one row
 * per connection, one status cell per venue, with "used by" chips naming the
 * APPS that ride each pipe (hierarchy v2 — users see Apps, never components).
 * Expanding a row opens the same connect ceremony the chat card uses.
 */

import { useEffect, useState } from 'react';
import { apiFetch, getToken } from '../../lib/api';
import ConnectorConnectCard from '../display/ConnectorConnectCard';
import { useTeam } from '../../hooks/useTeam';

interface ConnVenue { venue_id: string; venue_name: string; status: string }

const STATUS: Record<string, { color: string; label: string }> = {
  connected: { color: '#2e7d4f', label: 'Connected' },
  needs_reconnect: { color: '#b8860b', label: 'Needs attention' },
  not_connected: { color: '#b0aca4', label: 'Not connected' },
};

export default function ConnectionsMatrix() {
  const team = useTeam(getToken());
  const [info, setInfo] = useState<Record<string, ConnVenue[] | 'loading'>>({});
  const [open, setOpen] = useState<string | null>(null);

  // connector -> {display name, app names that use it (hired members' apps)}
  const rows: Record<string, { name: string; usedBy: Set<string> }> = {};
  // Every App that's on (its member hired ∧ the App switched on — the server
  // folds both into `enabled`), including Reports' and your team's own Apps.
  for (const a of team.apps) {
    if (team.gatingActive && !a.enabled) continue;
    for (const c of a.required_connections) {
      rows[c.connector] ??= { name: c.display_name, usedBy: new Set() };
      rows[c.connector].usedBy.add(a.name);
    }
  }

  useEffect(() => {
    for (const conn of Object.keys(rows)) {
      if (info[conn]) continue;
      setInfo((p) => ({ ...p, [conn]: 'loading' }));
      apiFetch(`/api/connectors/${conn}/connect-info`)
        .then((r) => (r.ok ? r.json() : { venues: [] }))
        .then((d) => setInfo((p) => ({ ...p, [conn]: d.venues ?? [] })))
        .catch(() => setInfo((p) => ({ ...p, [conn]: [] })));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [team.apps]);

  const venueNames: { id: string; name: string }[] = [];
  for (const v of Object.values(info)) {
    if (v === 'loading' || !Array.isArray(v)) continue;
    for (const venue of v) {
      if (!venueNames.some((x) => x.id === venue.venue_id)) {
        venueNames.push({ id: venue.venue_id, name: venue.venue_name });
      }
    }
  }

  const connectors = Object.keys(rows).sort();
  if (connectors.length === 0) {
    return (
      <div style={{ fontSize: '0.78rem', color: '#8a8a8a', padding: '0.5rem 0' }}>
        {team.gatingActive
          ? 'Nothing to connect yet — hire a team member and the systems its Apps work in appear here.'
          : 'Connections appear here once team hiring is switched on. Until then, manage each venue\u2019s credentials under Venues.'}
      </div>
    );
  }

  return (
    <div>
      <p style={{ fontSize: '0.72rem', color: '#8a8a8a', margin: '0 0 0.8rem' }}>
        The systems Norm works in. Connect each one per venue; every App that works in it is ready.
      </p>
      <table style={{ borderCollapse: 'collapse', fontSize: '0.76rem', width: '100%' }}>
        <thead>
          <tr>
            <th style={{ textAlign: 'left', padding: '4px 10px 4px 0', color: '#8a8a8a', fontWeight: 600 }}>Connection</th>
            {venueNames.map((v) => (
              <th key={v.id} style={{ textAlign: 'center', padding: '4px 8px', color: '#8a8a8a', fontWeight: 600 }}>{v.name}</th>
            ))}
            <th style={{ textAlign: 'left', padding: '4px 8px', color: '#8a8a8a', fontWeight: 600 }}>Used by</th>
          </tr>
        </thead>
        <tbody>
          {connectors.map((conn) => {
            const r = rows[conn];
            const venues = info[conn];
            return (
              <tr key={conn} style={{ borderTop: '1px solid #eee' }}>
                <td style={{ padding: '6px 10px 6px 0' }}>
                  <button type="button" onClick={() => setOpen(open === conn ? null : conn)}
                    style={{ border: 'none', background: 'none', cursor: 'pointer', fontFamily: 'inherit', fontWeight: 600, fontSize: '0.78rem', padding: 0, textAlign: 'left', whiteSpace: 'nowrap' }}>
                    {r.name}
                  </button>
                </td>
                {venueNames.map((v) => {
                  const cell = Array.isArray(venues) ? venues.find((x) => x.venue_id === v.id) : null;
                  const st = cell ? STATUS[cell.status] ?? STATUS.not_connected : null;
                  return (
                    <td key={v.id} title={st?.label} style={{ textAlign: 'center', padding: '6px 8px', color: st?.color ?? '#ccc' }}>●</td>
                  );
                })}
                <td style={{ padding: '6px 8px', color: '#6b6b6b' }}>{[...r.usedBy].join(', ')}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {open && (
        <div style={{ maxWidth: 560, marginTop: '1rem' }}>
          <ConnectorConnectCard data={{ connector_name: open }} onAction={async () => {}} />
        </div>
      )}
    </div>
  );
}
