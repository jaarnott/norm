'use client';

/**
 * How far a venue lets Norm go when receiving invoices.
 *
 * A ladder, climbed per venue and on evidence: Approve all → Approve fixes →
 * Autopilot. Only the top rung has switches, and each one authorises Norm to
 * write into Loaded unattended — a stock item, unit, brand or supplier created
 * there cannot be undone from Norm. So every switch starts off, and the
 * readiness number sits beside them rather than three levels away in another
 * panel: "would autopilot have been right?" is the question these answer.
 */

import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../../lib/api';

interface Venue { id: string; name: string }
interface Settings {
  mode: string;
  [gate: string]: string | boolean;
}

const MODE_COPY: Record<string, { label: string; blurb: string }> = {
  approve_all: {
    label: 'Approve all',
    blurb: 'Norm reviews every invoice and proposes changes. Nothing is received without you.',
  },
  approve_fixes: {
    label: 'Approve fixes',
    blurb: 'Norm receives an invoice it had nothing to say about. Anything it wants to change still waits for you.',
  },
  autopilot: {
    label: 'Autopilot',
    blurb: 'Norm applies its own suggestions and receives. It may also create what Loaded is missing — but only the kinds you tick below.',
  },
};

const GATE_ORDER = [
  'auto_create_units',
  'auto_create_items',
  'auto_create_brands',
  'auto_create_suppliers',
  'receive_without_unit',
  'receive_with_unconfirmed_unit',
  'receive_without_po',
];

const GATE_NOTE: Record<string, string> = {
  auto_create_suppliers:
    'The riskiest one. Supplier identity is what picks the extraction prompt, so a duplicate supplier sends every future invoice from that business to the wrong rules. Norm refuses to create one if anything existing plausibly matches.',
  receive_without_unit:
    'Norm picks the closest unit Loaded already has. It never invents one — that is the switch above.',
  receive_with_unconfirmed_unit:
    'The line has a unit, it just came from Loaded rather than the invoice copy. Off means someone confirms it first; the blocker names which unit it would use.',
};

export default function InvoiceAutopilotPanel() {
  const [venues, setVenues] = useState<Venue[]>([]);
  const [venueId, setVenueId] = useState('');
  const [settings, setSettings] = useState<Settings | null>(null);
  const [gates, setGates] = useState<Record<string, string>>({});
  const [readiness, setReadiness] = useState<{ rate: number; attempts: number } | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  // Loading is a STATE, not the absence of data. Without this the panel
  // rendered "Loading…" forever on any failure — which is exactly what a
  // stale dev server (404 on a route it never picked up) looked like.
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const res = await apiFetch('/api/venues');
        if (!res.ok) throw new Error(`Could not load venues (${res.status})`);
        const data = await res.json().catch(() => ({}));
        const list = Array.isArray(data) ? data : data.venues || [];
        setVenues(list);
        if (list.length && !venueId) setVenueId(list[0].id);
        else if (!list.length) setLoaded(true);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Could not load venues');
        setLoaded(true);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const load = useCallback(async () => {
    if (!venueId) return;
    setMessage('');
    setError(null);
    try {
      const res = await apiFetch(`/api/venues/${venueId}/invoice-autopilot`);
      if (res.status === 404) {
        throw new Error(
          'This build of the API doesn’t have the invoice-autopilot settings yet — restart the API server.',
        );
      }
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(
          typeof data.detail === 'string' ? data.detail : `Could not load settings (${res.status})`,
        );
      }
      setSettings(data.settings);
      setGates(data.gates || {});
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load these settings');
    } finally {
      setLoaded(true);
    }
    // The evidence for moving up a rung, next to the control that moves it.
    // Admin-only, so a 403 here is normal — the settings still work without it.
    try {
      const rep = await apiFetch(
        `/api/supplier-invoice-specs/autopilot-confidence?days=30&venue_id=${venueId}`,
      );
      const rd = await rep.json().catch(() => ({}));
      setReadiness(rep.ok && rd?.totals
        ? { rate: rd.rates?.autopilot_ready ?? 0, attempts: rd.totals.attempts ?? 0 }
        : null);
    } catch {
      setReadiness(null);
    }
  }, [venueId]);
  useEffect(() => { void load(); }, [load]);

  const save = async (next: Settings) => {
    setSaving(true);
    setMessage('');
    try {
      const res = await apiFetch(`/api/venues/${venueId}/invoice-autopilot`, {
        method: 'PUT',
        body: JSON.stringify(next),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Error ${res.status}`);
      setSettings(data.settings);
      setMessage('Saved');
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'Could not save');
    } finally {
      setSaving(false);
    }
  };

  if (!loaded) return <div style={{ fontSize: '0.78rem', color: '#888' }}>Loading…</div>;
  if (!settings) {
    return (
      <div style={{ maxWidth: 720 }}>
        <h3 style={{ margin: '0 0 4px', fontSize: '1rem' }}>Receiving invoices</h3>
        <div style={{ fontSize: '0.78rem', color: '#c0392b' }}>
          {error || (venues.length ? 'Could not load these settings.' : 'No venues to configure.')}
        </div>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 720 }}>
      <h3 style={{ margin: '0 0 4px', fontSize: '1rem' }}>Receiving invoices</h3>
      <div style={{ fontSize: '0.74rem', color: '#777', marginBottom: 12 }}>
        Set per venue, because venues differ in how tidy their Loaded catalogue is.
      </div>

      <select value={venueId} onChange={(e) => setVenueId(e.target.value)}
        style={{ fontSize: '0.78rem', padding: '4px 8px', borderRadius: 4, border: '1px solid #d8d4cc', marginBottom: 14, fontFamily: 'inherit' }}>
        {venues.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
      </select>

      {readiness && readiness.attempts > 0 && (
        <div style={{ fontSize: '0.74rem', color: '#555', background: '#f7f5f1', border: '1px solid #e8e3da', borderRadius: 6, padding: '8px 12px', marginBottom: 14 }}>
          Over the last 30 days, autopilot would have been right on{' '}
          <strong>{Math.round(readiness.rate * 100)}%</strong> of the {readiness.attempts} invoices a
          person received here.
        </div>
      )}

      {Object.entries(MODE_COPY).map(([mode, copy]) => (
        <label key={mode} style={{ display: 'block', padding: '8px 10px', marginBottom: 6, border: `1px solid ${settings.mode === mode ? '#8a6d3b' : '#e8e3da'}`, borderRadius: 6, cursor: 'pointer', background: settings.mode === mode ? '#fdfaf4' : '#fff' }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
            <input type="radio" name="mode" checked={settings.mode === mode} disabled={saving}
              onChange={() => void save({ ...settings, mode })} />
            <strong style={{ fontSize: '0.8rem' }}>{copy.label}</strong>
          </div>
          <div style={{ fontSize: '0.72rem', color: '#777', marginLeft: 22 }}>{copy.blurb}</div>
        </label>
      ))}

      {settings.mode === 'autopilot' && (
        <div style={{ marginTop: 12, paddingLeft: 10, borderLeft: '2px solid #e8e3da' }}>
          <div style={{ fontSize: '0.74rem', color: '#777', marginBottom: 8 }}>
            What Norm may do on its own. Everything here writes to Loaded and cannot be undone from
            Norm — leave a switch off and invoices needing it wait for you instead, saying so.
          </div>
          {GATE_ORDER.filter((g) => gates[g]).map((g) => (
            <label key={g} style={{ display: 'block', marginBottom: 8, cursor: 'pointer' }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
                <input type="checkbox" checked={!!settings[g]} disabled={saving}
                  onChange={(e) => void save({ ...settings, [g]: e.target.checked })} />
                <span style={{ fontSize: '0.78rem' }}>{gates[g]}</span>
              </div>
              {GATE_NOTE[g] && (
                <div style={{ fontSize: '0.7rem', color: '#9ca3af', marginLeft: 22 }}>{GATE_NOTE[g]}</div>
              )}
            </label>
          ))}
        </div>
      )}

      {message && <div style={{ fontSize: '0.74rem', color: message === 'Saved' ? '#2e7d4f' : '#c0392b', marginTop: 10 }}>{message}</div>}
    </div>
  );
}
