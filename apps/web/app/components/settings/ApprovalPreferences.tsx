'use client';

/**
 * What Norm may do without asking you — Settings → Preferences.
 *
 * One list of the writes Norm can make for you (apps/api/app/routers/
 * approval_preferences.py). Ordinary writes are "Ask me" or "Always allow";
 * receiving and reconciling invoices have levels, and receiving has switches
 * at Autopilot. Yours alone — the same choices the approval card's "always
 * allow" box and Norm itself (set_approval_preference) change.
 *
 * Replaced two panels with two homes for one question: per-user "workflow
 * modes", and a per-venue receiving ladder.
 */

import { useEffect, useState } from 'react';
import type { CSSProperties } from 'react';
import { Lock } from 'lucide-react';
import { apiFetch, getStoredUser } from '../../lib/api';
import Badge from '../ui/Badge';
import Icon from '../ui/Icon';
import PageState from '../ui/PageState';

interface Level { id: string; label: string; description?: string }
interface Switch { id: string; label: string }
export interface ToolPref {
  key: string;
  label: string;
  summary: string;
  kind: 'ask' | 'locked' | 'levels';
  value?: 'ask' | 'always';
  level?: string;
  levels?: Level[];
  switches?: Switch[];
  options?: Record<string, boolean>;
}

const RECEIVE = 'loadedhub.review_and_receive_invoices';

/** Why a receiving switch matters, beside the switch. */
const SWITCH_NOTE: Record<string, string> = {
  auto_create_suppliers:
    'The riskiest one. Supplier identity is what picks the extraction prompt, so a duplicate supplier sends every future invoice from that business to the wrong rules. Norm refuses to create one if anything existing plausibly matches.',
  receive_without_unit:
    'Norm picks the closest unit Loaded already has. It never invents one — that is the switch above.',
  receive_with_unconfirmed_unit:
    'The line has a unit, it just came from Loaded rather than the invoice copy. Off means someone confirms it first; the blocker names which unit it would use.',
  receive_unreconciled_totals:
    "The copy's own arithmetic doesn't add up. Norm diagnoses which figure was misread and suggests the correction; this switch lets it receive on the line values when no correction is confident.",
  auto_delete_duplicates:
    'A destructive write: Norm deletes the draft when the same invoice number from the same supplier was already received.',
  auto_delete_non_invoices:
    'A destructive write: statements and letters Loaded ingested as invoice drafts are deleted rather than received.',
  auto_delete_unreadable:
    'The riskiest delete: a draft with no readable copy could be a real delivery behind a bad scan. Off means a person decides each one.',
};

/** Each group is a white .n-card block, stacked. */
const card: CSSProperties = { padding: '14px 16px', marginBottom: 12 };
const cardTitle: CSSProperties = { margin: 0, fontSize: 'var(--fs-base)', fontWeight: 600, lineHeight: 1.35, color: 'var(--text)' };

/** The one switch look: on = --primary track with a white knob, off = a
 *  --field outline with a --field knob. It is the native checkbox drawn as a
 *  switch (the knob is a background gradient), so the label, keyboard, focus
 *  ring and onChange are exactly a checkbox's. */
function switchStyle(on: boolean, disabled: boolean): CSSProperties {
  const knob = on ? 'var(--on-primary)' : 'var(--field)';
  const r = on ? 7 : 5;
  return {
    appearance: 'none',
    WebkitAppearance: 'none',
    flex: '0 0 auto',
    width: 36,
    height: 20,
    margin: '1px 0 0',
    borderRadius: 999,
    border: `1px solid ${on ? 'var(--primary)' : 'var(--field)'}`,
    backgroundColor: on ? 'var(--primary)' : 'var(--bg)',
    backgroundImage: `radial-gradient(circle, ${knob} ${r}px, transparent ${r + 0.75}px)`,
    backgroundSize: '18px 18px',
    backgroundRepeat: 'no-repeat',
    backgroundPosition: on ? 'right center' : 'left center',
    cursor: disabled ? 'default' : 'pointer',
    opacity: disabled ? 0.45 : 1,
    transition: 'background-position 0.12s, background-color 0.12s, border-color 0.12s',
  };
}

/** A segment's label, with a hidden 600-weight copy underneath reserving its
 *  width: the chosen side is bolder, and without the reserve each row's
 *  control would shift by a few pixels depending on which side is chosen. */
function segmentLabel(text: string) {
  return (
    <span style={{ display: 'inline-grid' }}>
      <span aria-hidden="true" style={{ gridArea: '1 / 1', fontWeight: 600, visibility: 'hidden' }}>{text}</span>
      <span style={{ gridArea: '1 / 1', textAlign: 'center' }}>{text}</span>
    </span>
  );
}

export default function ApprovalPreferences() {
  const [tools, setTools] = useState<ToolPref[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const [readiness, setReadiness] = useState<{ rate: number; attempts: number } | null>(null);
  const isAdmin = getStoredUser()?.role === 'admin';

  useEffect(() => {
    apiFetch('/api/approval-preferences')
      .then(async r => {
        if (!r.ok) throw new Error(`Could not load your approval settings (${r.status})`);
        setTools((await r.json()).tools || []);
      })
      .catch(e => setError(e instanceof Error ? e.message : 'Could not load your approval settings'))
      .finally(() => setLoaded(true));
    // "Would autopilot have been right?" — the evidence for moving up a level,
    // next to the control that moves it. Admin-only, so a 403 is normal.
    if (isAdmin) {
      apiFetch('/api/supplier-invoice-specs/autopilot-confidence?days=30')
        .then(r => (r.ok ? r.json() : null))
        .then(d => setReadiness(d?.totals ? { rate: d.rates?.autopilot_ready ?? 0, attempts: d.totals.attempts ?? 0 } : null))
        .catch(() => setReadiness(null));
    }
  }, [isAdmin]);

  const save = async (key: string, value: unknown) => {
    setSaving(key);
    setError(null);
    try {
      const r = await apiFetch('/api/approval-preferences', { method: 'PUT', body: JSON.stringify({ key, value }) });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Could not save (${r.status})`);
      setTools(prev => prev.map(t => (t.key === key ? data : t)));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save');
    } finally {
      setSaving(null);
    }
  };

  // Section header: 18px/600 title and its muted line, as on every settings tab.
  const header = (
    <>
      <h2 style={{ margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>
        What Norm may do without asking
      </h2>
      <p style={{ margin: '4px 0 16px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
        Norm asks before it changes anything, with a card that shows exactly what will change. Choose
        where it needn&apos;t ask. These settings are yours alone — you can also change them from an
        approval card, or by asking Norm.
      </p>
    </>
  );

  if (!loaded) return <div style={{ maxWidth: 720, lineHeight: 1.45 }}>{header}<PageState kind="loading" title="Loading…" /></div>;

  const levelled = tools.filter(t => t.kind === 'levels');
  const others = tools.filter(t => t.kind !== 'levels');

  return (
    <div style={{ maxWidth: 720, lineHeight: 1.45 }}>
      {header}
      {error && <div style={{ marginBottom: 12 }}><PageState kind="error" title={error} /></div>}

      {levelled.map(t => {
        const options = t.options || {};
        const top = t.levels?.[t.levels.length - 1]?.id;
        const busy = saving === t.key;
        return (
          <div key={t.key} className="n-card" style={card}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
              <h3 style={cardTitle}>{t.label}</h3>
              {busy && <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>Saving…</span>}
            </div>
            {t.key === RECEIVE && readiness && readiness.attempts > 0 && (
              <div style={{ margin: '10px 0 0', padding: '8px 12px', borderRadius: 'var(--radius)', background: 'var(--surface-alt)', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
                Over the last 30 days, autopilot would have been right on{' '}
                <strong style={{ fontWeight: 600, color: 'var(--text)' }}>{Math.round(readiness.rate * 100)}%</strong> of the {readiness.attempts} invoices a person received.
              </div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 12 }}>
              {(t.levels || []).map(lv => {
                const on = t.level === lv.id;
                // The chosen level reads as a selected tile: --selected fill, tan edge.
                return (
                  <label key={lv.id} style={{
                    display: 'flex', gap: 10, alignItems: 'flex-start', cursor: busy ? 'default' : 'pointer',
                    padding: '10px 12px', borderRadius: 'var(--radius)',
                    border: `1px solid ${on ? 'var(--brand-soft)' : 'var(--line)'}`,
                    background: on ? 'var(--selected)' : 'var(--bg)',
                  }}>
                    <input type="radio" name={t.key} checked={on} disabled={busy}
                      onChange={() => void save(t.key, { level: lv.id, options })}
                      style={{ flex: '0 0 auto', width: 16, height: 16, margin: '2px 0 0', accentColor: 'var(--accent)', cursor: 'inherit' }} />
                    <span style={{ minWidth: 0 }}>
                      <span style={{ display: 'block', fontSize: 'var(--fs-base)', fontWeight: on ? 600 : 500, color: 'var(--text)' }}>{lv.label}</span>
                      {lv.description && <span style={{ display: 'block', marginTop: 2, fontSize: 'var(--fs-sm)', color: on ? 'var(--text-soft)' : 'var(--muted)' }}>{lv.description}</span>}
                    </span>
                  </label>
                );
              })}
            </div>
            {t.level === top && (t.switches || []).length > 0 && (
              <div style={{ marginTop: 16, paddingTop: 14, borderTop: '1px solid var(--line)' }}>
                <div style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text)' }}>What Norm may also do on its own</div>
                <p style={{ margin: '2px 0 4px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
                  Everything here writes to Loaded and can&apos;t be undone from Norm — leave a switch off
                  and invoices needing it wait for you instead, saying so.
                </p>
                {(t.switches || []).map((sw, i) => {
                  const on = !!options[sw.id];
                  return (
                    <label key={sw.id} style={{
                      display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16,
                      padding: '10px 0', borderTop: i ? '1px solid var(--line-soft)' : 'none',
                      cursor: busy ? 'default' : 'pointer',
                    }}>
                      <span style={{ minWidth: 0 }}>
                        <span style={{ display: 'block', fontSize: 'var(--fs-base)', fontWeight: 500, color: 'var(--text)' }}>
                          {sw.label[0].toUpperCase() + sw.label.slice(1)}
                        </span>
                        {SWITCH_NOTE[sw.id] && <span style={{ display: 'block', marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>{SWITCH_NOTE[sw.id]}</span>}
                      </span>
                      <input type="checkbox" role="switch" checked={on} disabled={busy}
                        onChange={e => void save(t.key, { level: t.level, options: { ...options, [sw.id]: e.target.checked } })}
                        style={switchStyle(on, busy)} />
                    </label>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}

      {others.length > 0 && (
        <div className="n-card" style={{ ...card, paddingBottom: 4 }}>
          <h3 style={{ ...cardTitle, marginBottom: 8 }}>Everything else Norm can change</h3>
          {others.map(t => {
            const busy = saving === t.key;
            return (
              <div key={t.key} style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px 16px',
                flexWrap: 'wrap', padding: '10px 0', borderTop: '1px solid var(--line)',
              }}>
                <span style={{ flex: '1 1 220px', minWidth: 0, fontSize: 'var(--fs-base)', color: 'var(--text)' }}>
                  {t.label}
                  {t.kind === 'locked' && (
                    <span style={{ display: 'block', marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
                      Always asks — Norm can&apos;t give itself more freedom without you saying yes.
                    </span>
                  )}
                </span>
                {t.kind === 'locked' ? (
                  <Badge><Icon icon={Lock} size={12} />Asks first</Badge>
                ) : (
                  // Ask me | Always allow: the chosen side is a selected chip
                  // (--selected fill, tan edge); the other is a quiet button.
                  <span role="radiogroup" aria-label={t.label} style={{
                    display: 'inline-flex', flex: '0 0 auto', gap: 2, padding: 2,
                    border: '1px solid var(--line-strong)', borderRadius: 'var(--radius)', background: 'var(--bg)',
                  }}>
                    {(['ask', 'always'] as const).map(v => {
                      const on = (t.value || 'ask') === v;
                      return (
                        <button key={v} role="radio" aria-checked={on} disabled={busy || on}
                          onClick={() => void save(t.key, v)}
                          className="n-btn n-btn--quiet n-btn--sm"
                          style={{
                            height: 28, padding: '0 10px', borderRadius: 'var(--radius-sm)',
                            ...(on ? { background: 'var(--selected)', borderColor: 'var(--brand-soft)', color: 'var(--text)', fontWeight: 600, opacity: 1 } : {}),
                          }}>
                          {segmentLabel(v === 'ask' ? 'Ask me' : 'Always allow')}
                        </button>
                      );
                    })}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
