'use client';

/**
 * Settings → Connections: Norm's own systems. Hospo HR and Hospo Functions are
 * connected like BambooHR, but with nothing to enter: Hospo HR is switched on
 * or off here; Hospo Functions follows Functions in Settings → Company. An App
 * shows its screens for the systems that are on (services/screens.py).
 */

import { useState } from 'react';
import { apiFetch, getToken } from '../../lib/api';
import { TEAM_CHANGED_EVENT, useTeam } from '../../hooks/useTeam';
import Badge from '../ui/Badge';

const ABOUT: Record<string, string> = {
  'hospo-hr': 'Hiring, Training and Reviews on Norm’s own HR. Turn it off if you use another system for these.',
  'hospo-functions': 'Function enquiries, events and proposals. Switched with Functions in Settings → Company.',
};

export default function BuiltInConnections() {
  const team = useTeam(getToken());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const builtIn = team.systems.filter((s) => s.built_in);
  if (!team.loaded || builtIn.length === 0) return null;

  const toggle = async (key: string, on: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const r = await apiFetch(`/api/connections/built-in/${key}/${on ? 'enable' : 'disable'}`, { method: 'POST' });
      if (!r.ok) {
        setError(r.status === 403 ? 'Only owners and managers can change connections.' : `Couldn't change it (${r.status})`);
        return;
      }
      window.dispatchEvent(new CustomEvent(TEAM_CHANGED_EVENT));
      team.refresh();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ marginBottom: 24 }}>
      <div className="n-eyebrow" style={{ marginBottom: 6 }}>Built into Norm</div>
      <ul style={{ listStyle: 'none', margin: 0, padding: 0, border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
        {builtIn.map((s, i) => (
          <li key={s.key} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px', borderTop: i ? '1px solid var(--line-soft)' : 'none' }}>
            <div style={{ flex: '1 1 auto', minWidth: 0 }}>
              <div style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>{s.name}</div>
              <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{ABOUT[s.key]}</div>
            </div>
            {s.key === 'hospo-hr' ? (
              <button
                type="button"
                className={s.on ? 'n-btn n-btn--secondary n-btn--sm' : 'n-btn n-btn--primary n-btn--sm'}
                disabled={busy}
                onClick={() => toggle(s.key, !s.on)}
              >
                {s.on ? 'Turn off' : 'Turn on'}
              </button>
            ) : (
              <Badge tone={s.on ? 'ok' : 'neutral'}>{s.on ? 'On' : 'Off'}</Badge>
            )}
          </li>
        ))}
      </ul>
      {error && <p role="alert" style={{ margin: '6px 0 0', fontSize: 'var(--fs-sm)', color: 'var(--error)' }}>{error}</p>}
    </div>
  );
}
