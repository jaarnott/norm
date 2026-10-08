'use client';

/**
 * Settings → Connections, for civilians: the systems Norm works in, one row
 * per connection, one status cell per venue, with "used by" chips naming the
 * APPS that ride each pipe (hierarchy v2 — users see Apps, never components).
 * Expanding a row opens the same connect ceremony the chat card uses.
 */

import { useEffect, useState, type CSSProperties } from 'react';
import { Check, ChevronDown, ChevronRight, Minus, TriangleAlert, type LucideIcon } from 'lucide-react';
import { apiFetch, getToken } from '../../lib/api';
import ConnectorConnectCard from '../display/ConnectorConnectCard';
import { useTeam } from '../../hooks/useTeam';
import { useBreakpoint } from '../../hooks/useBreakpoint';
import Badge, { type BadgeTone } from '../ui/Badge';
import Icon from '../ui/Icon';
import PageState from '../ui/PageState';

interface ConnVenue { venue_id: string; venue_name: string; status: string }

// A status reads by its SHAPE as well as its colour — tick, warning, dash —
// so it survives colour blindness and a greyscale print.
interface StatusLook { tone: BadgeTone; icon: LucideIcon; label: string }
const STATUS: Record<string, StatusLook> = {
  connected: { tone: 'ok', icon: Check, label: 'Connected' },
  needs_reconnect: { tone: 'warn', icon: TriangleAlert, label: 'Needs attention' },
  not_connected: { tone: 'neutral', icon: Minus, label: 'Not connected' },
};

/** The status icon on its badge tint. Named (hover + screen reader) unless a visible label sits beside it. */
function StatusMark({ st, named = true }: { st: StatusLook; named?: boolean }) {
  return (
    <Badge tone={st.tone} title={named ? st.label : undefined}>
      <Icon icon={st.icon} size={14} strokeWidth={2} label={named ? st.label : undefined} />
    </Badge>
  );
}

export default function ConnectionsMatrix() {
  const team = useTeam(getToken());
  const { isDesktop } = useBreakpoint();
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
    if (!team.loaded) return <PageState kind="loading" title="Loading connections…" />;
    return team.gatingActive ? (
      <PageState kind="empty" title="Nothing to connect yet" detail="Hire a team member and the systems its Apps work in appear here." />
    ) : (
      <PageState
        kind="empty"
        title="Connections appear here once team hiring is switched on."
        detail={'Until then, manage each venue’s credentials under Venues.'}
      />
    );
  }

  // On a phone or iPad the venue columns scroll sideways; the connection name stays put.
  const pinned = (fill: string): CSSProperties =>
    isDesktop ? {} : { position: 'sticky', left: 0, zIndex: 1, background: fill, boxShadow: 'inset -1px 0 0 var(--line)' };

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '8px 24px', marginBottom: 12 }}>
        <p style={{ margin: 0, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
          The systems Norm works in. Connect each one per venue; every App that works in it is ready.
        </p>
        <ul aria-label="Status key" style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 16px', margin: 0, padding: 0, listStyle: 'none', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
          {Object.entries(STATUS).map(([key, st]) => (
            <li key={key} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <StatusMark st={st} named={false} />
              {st.label}
            </li>
          ))}
        </ul>
      </div>

      <div className="n-card" style={{ overflowX: 'auto' }}>
        <table className="n-table">
          <thead>
            <tr>
              <th scope="col" style={pinned('var(--surface-alt)')}>Connection</th>
              {venueNames.map((v) => (
                <th key={v.id} scope="col" style={{ textAlign: 'center' }}>{v.name}</th>
              ))}
              <th scope="col">Used by</th>
            </tr>
          </thead>
          <tbody>
            {connectors.map((conn) => {
              const r = rows[conn];
              const venues = info[conn];
              return (
                <tr key={conn}>
                  <td style={pinned('var(--bg)')}>
                    <button type="button" onClick={() => setOpen(open === conn ? null : conn)} aria-expanded={open === conn}
                      style={{ display: 'inline-flex', alignItems: 'center', gap: 6, border: 'none', background: 'none', cursor: 'pointer', fontFamily: 'inherit', fontWeight: 600, fontSize: 'var(--fs-base)', color: 'var(--text)', padding: 0, textAlign: 'left', whiteSpace: 'nowrap' }}>
                      <Icon icon={open === conn ? ChevronDown : ChevronRight} size="dense" tone="muted" />
                      {r.name}
                    </button>
                  </td>
                  {venueNames.map((v) => {
                    const cell = Array.isArray(venues) ? venues.find((x) => x.venue_id === v.id) : null;
                    const st = cell ? STATUS[cell.status] ?? STATUS.not_connected : null;
                    return (
                      <td key={v.id} style={{ textAlign: 'center' }}>
                        {st && <StatusMark st={st} />}
                      </td>
                    );
                  })}
                  <td>
                    {/* Wide enough for two chips a line, so a busy connection's row stays short. */}
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, minWidth: 240 }}>
                      {[...r.usedBy].map((app) => <Badge key={app}>{app}</Badge>)}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {open && (
        <div style={{ maxWidth: 560, marginTop: 16 }}>
          <ConnectorConnectCard data={{ connector_name: open }} onAction={async () => {}} />
        </div>
      )}
    </div>
  );
}
