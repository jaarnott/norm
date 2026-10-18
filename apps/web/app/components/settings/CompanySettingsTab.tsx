'use client';

import { useCallback, useEffect, useId, useMemo, useState } from 'react';
import type { CSSProperties } from 'react';
import { apiFetch } from '../../lib/api';
import { FUNCTIONS_MODES, currencyLabel, timeZoneOptions, type FunctionsMode } from '../../lib/companySettings';
import { TEAM_CHANGED_EVENT } from '../../hooks/useTeam';
import Button from '../ui/Button';
import PageState from '../ui/PageState';
import { ConfirmDialog } from '../ui/Dialog';

/** GET/PUT /api/organizations/{id}/company-settings. */
interface CompanySettings {
  currency: string;
  default_timezone: string;
  tax_label: string;
  functions_mode: FunctionsMode;
  /** owner or admin of the organisation, or a platform admin */
  can_edit: boolean;
  /** the ISO 4217 codes the API accepts */
  currencies: string[];
}

type Draft = Pick<CompanySettings, 'currency' | 'default_timezone' | 'tax_label'>;

const sectionTitle: CSSProperties = { margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' };
const intro: CSSProperties = { margin: '4px 0 16px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' };
const help: CSSProperties = { margin: '4px 0 0', fontSize: 'var(--fs-xs)', color: 'var(--muted)' };
const alertBox: CSSProperties = {
  marginTop: 12, padding: '8px 12px', fontSize: 'var(--fs-sm)',
  color: 'var(--error)', backgroundColor: 'var(--error-bg)', borderRadius: 'var(--radius)',
};

/** The API's reason for a refusal. */
async function reason(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => null);
  const detail = body?.detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail.message === 'string') return detail.message;
  return `${fallback} (error ${res.status})`;
}

function draftOf(s: CompanySettings): Draft {
  return { currency: s.currency, default_timezone: s.default_timezone, tax_label: s.tax_label };
}

/**
 * Settings → General: the currency, default time zone and tax wording Norm
 * Functions uses for money and organisation-wide dates, and — for owners and
 * admins only — the Functions mode switch. Everyone in the organisation can
 * read the first three; `can_edit` from the API decides who sees controls,
 * so nothing is offered that the API would refuse.
 */
export default function CompanySettingsTab({ orgId }: { orgId: string }) {
  const [settings, setSettings] = useState<CompanySettings | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  // The mode waiting on its confirmation — nothing changes until confirmed.
  const [switching, setSwitching] = useState<FunctionsMode | null>(null);
  const fieldId = useId();
  const url = `/api/organizations/${orgId}/company-settings`;

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const res = await apiFetch(url);
      if (!res.ok) {
        setLoadError(await reason(res, 'Couldn’t load company settings'));
        return;
      }
      const data: CompanySettings = await res.json();
      setSettings(data);
      setDraft(draftOf(data));
    } catch {
      setLoadError('Couldn’t load company settings — check your connection and try again.');
    }
  }, [url]);

  useEffect(() => { load(); }, [load]);

  const savedZone = settings?.default_timezone ?? '';
  const zones = useMemo(
    () => timeZoneOptions(savedZone, typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : []),
    [savedZone],
  );
  const currencyNames = useMemo(() => {
    try {
      return new Intl.DisplayNames(['en'], { type: 'currency' });
    } catch {
      return undefined;
    }
  }, []);

  if (loadError) return <PageState kind="error" title={loadError} />;
  if (!settings || !draft) return <PageState kind="loading" title="Loading company settings…" />;

  const editable = settings.can_edit;
  const currencies = settings.currencies.includes(settings.currency) ? settings.currencies : [settings.currency, ...settings.currencies];
  const changes: Partial<Draft> = {};
  if (draft.currency !== settings.currency) changes.currency = draft.currency;
  if (draft.default_timezone !== settings.default_timezone) changes.default_timezone = draft.default_timezone;
  if (draft.tax_label.trim() !== settings.tax_label) changes.tax_label = draft.tax_label.trim();
  const dirty = Object.keys(changes).length > 0;

  const edit = (patch: Partial<Draft>) => {
    setDraft({ ...draft, ...patch });
    setSaved(false);
    setSaveError(null);
  };

  const save = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const res = await apiFetch(url, { method: 'PUT', body: JSON.stringify(changes) });
      if (!res.ok) {
        setSaveError(await reason(res, 'Couldn’t save'));
        return;
      }
      const data: CompanySettings = await res.json();
      setSettings(data);
      setDraft(draftOf(data));
      setSaved(true);
    } catch {
      setSaveError('Couldn’t save — check your connection and try again.');
    } finally {
      setSaving(false);
    }
  };

  // Throws on failure, so the confirmation stays open and says why (going
  // live is refused while a Functions venue has no time zone).
  const switchMode = async (mode: FunctionsMode) => {
    const res = await apiFetch(url, { method: 'PUT', body: JSON.stringify({ functions_mode: mode }) });
    if (!res.ok) throw new Error(await reason(res, 'The Functions mode wasn’t changed'));
    const data: CompanySettings = await res.json();
    setSettings(data);
    // The Functions tab and pages come and go with the mode.
    window.dispatchEvent(new CustomEvent(TEAM_CHANGED_EVENT));
  };

  const target = FUNCTIONS_MODES.find((m) => m.id === switching);

  return (
    // Full width, like the AI team tab.
    <div style={{ lineHeight: 1.45 }}>
      <h2 style={sectionTitle}>General</h2>
      <p style={intro}>How Norm shows money and organisation-wide dates — on quotes, menus and Functions reports.</p>

      <div className="n-card" style={{ padding: 16 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 16 }}>
          <div style={{ minWidth: 0 }}>
            <label className="n-label" htmlFor={`${fieldId}-currency`}>Currency</label>
            <select id={`${fieldId}-currency`} className="n-select" value={draft.currency} disabled={!editable || saving}
              onChange={(e) => edit({ currency: e.target.value })} style={{ width: '100%' }}>
              {currencies.map((code) => <option key={code} value={code}>{currencyLabel(code, currencyNames)}</option>)}
            </select>
            <p style={help}>Every amount in Functions is in this currency.</p>
          </div>
          <div style={{ minWidth: 0 }}>
            <label className="n-label" htmlFor={`${fieldId}-tz`}>Default time zone</label>
            <select id={`${fieldId}-tz`} className="n-select" value={draft.default_timezone} disabled={!editable || saving}
              onChange={(e) => edit({ default_timezone: e.target.value })} style={{ width: '100%' }}>
              {zones.map((zone) => <option key={zone} value={zone}>{zone.replace(/_/g, ' ')}</option>)}
            </select>
            <p style={help}>For dates that belong to no venue. Each venue keeps its own (Venues).</p>
          </div>
          <div style={{ minWidth: 0 }}>
            <label className="n-label" htmlFor={`${fieldId}-tax`}>Tax wording</label>
            <input id={`${fieldId}-tax`} className="n-input" value={draft.tax_label} maxLength={40} disabled={!editable || saving}
              onChange={(e) => edit({ tax_label: e.target.value })} placeholder="incl. GST" style={{ width: '100%' }} />
            <p style={help}>Printed beside prices, e.g. “incl. GST”.</p>
          </div>
        </div>

        {editable ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 16 }}>
            <Button variant="primary" onClick={save} disabled={!dirty || saving || !draft.tax_label.trim()}>
              {saving ? 'Saving…' : 'Save'}
            </Button>
            {saved && !dirty && <span role="status" style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>Saved</span>}
          </div>
        ) : (
          <p style={{ ...help, marginTop: 12 }}>Only an owner or admin of the organisation can change these.</p>
        )}
        {saveError && <div role="alert" style={alertBox}>{saveError}</div>}
      </div>

      {editable && (
        <div style={{ marginTop: 32 }}>
          <h2 style={sectionTitle}>Norm Functions</h2>
          <p style={intro}>Whether Functions — enquiries, events, proposals and the venue inboxes — is on for everyone in the organisation. Only owners and admins see this.</p>
          <div role="radiogroup" aria-label="Functions mode" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {FUNCTIONS_MODES.map((m) => {
              const on = settings.functions_mode === m.id;
              // The current mode reads as a selected tile: --selected fill, tan edge.
              return (
                <label key={m.id} style={{
                  display: 'flex', gap: 10, alignItems: 'flex-start', cursor: on ? 'default' : 'pointer',
                  padding: '10px 12px', borderRadius: 'var(--radius)',
                  border: `1px solid ${on ? 'var(--brand-soft)' : 'var(--line)'}`,
                  background: on ? 'var(--selected)' : 'var(--bg)',
                }}>
                  <input type="radio" name={`${fieldId}-mode`} checked={on} onChange={() => setSwitching(m.id)}
                    style={{ flex: '0 0 auto', width: 16, height: 16, margin: '2px 0 0', accentColor: 'var(--accent)', cursor: 'inherit' }} />
                  <span style={{ minWidth: 0 }}>
                    <span style={{ display: 'block', fontSize: 'var(--fs-base)', fontWeight: on ? 600 : 500, color: 'var(--text)' }}>{m.label}</span>
                    <span style={{ display: 'block', marginTop: 2, fontSize: 'var(--fs-sm)', color: on ? 'var(--text-soft)' : 'var(--muted)' }}>{m.description}</span>
                  </span>
                </label>
              );
            })}
          </div>
        </div>
      )}

      {target && (
        <ConfirmDialog
          title={`Switch Functions to ${target.label}?`}
          confirmLabel={`Switch to ${target.label}`}
          busyLabel="Switching…"
          onConfirm={() => switchMode(target.id)}
          onClose={() => setSwitching(null)}
        >
          <p style={{ margin: 0 }}>{target.consequence}</p>
        </ConfirmDialog>
      )}
    </div>
  );
}
