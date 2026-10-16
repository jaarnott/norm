'use client';

/**
 * Which event fields each booking type uses at this venue — one tick per
 * booking type × field. An unticked field is hidden on the event page, the
 * client portal and the prints for that booking type.
 */
import { Fragment, useMemo, useState } from 'react';
import { fnSend } from '../shared/api';
import type { FnMeta } from '../shared/meta';
import { errorText } from './errors';
import { SHORT_BOOKING_LABELS, fieldGroups, fullMatrix, overridesOf, sameMatrix } from './enabledFields';
import type { EnabledFields, VenueProfile, VenueSettings } from './types';
import { Panel, SaveRow, Section } from './ui';

export default function EnabledFieldsSection({ venueId, settings, enabled, meta, canWrite, onSaved }: {
  venueId: string;
  settings: VenueSettings | null;
  enabled: EnabledFields;
  meta: FnMeta;
  canWrite: boolean;
  onSaved: (profile: VenueProfile) => void;
}) {
  const types = meta.booking_types.map((b) => b.value);
  const keys = meta.field_catalogue.map((f) => f.key);
  const groups = useMemo(() => fieldGroups(meta.field_catalogue), [meta.field_catalogue]);
  const base = fullMatrix(enabled, types, keys);
  const [draft, setDraft] = useState<EnabledFields>(() => fullMatrix(enabled, types, keys));
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dirty = !sameMatrix(draft, base);
  const offCount = types.reduce((n, bt) => n + keys.filter((k) => draft[bt]?.[k] === false).length, 0);

  const toggle = (bt: string, key: string) => {
    setDraft((d) => ({ ...d, [bt]: { ...d[bt], [key]: !(d[bt]?.[key] ?? true) } }));
    setSaved(false);
    setError(null);
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const profile = await fnSend<VenueProfile>('PUT', `/venues/${venueId}/settings`, {
        ...(settings ? { version: settings.version } : {}),
        enabled_fields: overridesOf(draft),
      });
      setDraft(fullMatrix(profile.enabled_fields, types, keys));
      setSaved(true);
      onSaved(profile);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  const th = { padding: '8px 6px', textAlign: 'center' as const, width: 72 };
  return (
    <Section title="Event fields" meta={`What staff and clients fill in, per booking type · ${offCount} switched off`}>
      <Panel style={{ padding: 0 }}>
        <div style={{ overflowX: 'auto' }}>
          <table className="n-table" style={{ minWidth: 340 }}>
            <thead>
              <tr>
                <th style={{ position: 'sticky', left: 0 }}>Field</th>
                {meta.booking_types.map((b) => (
                  <th key={b.value} style={th} title={b.label} scope="col">
                    <span aria-hidden>{SHORT_BOOKING_LABELS[b.value] ?? b.label}</span>
                    <span style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>{b.label}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => (
                <Fragment key={g.section}>
                  <tr>
                    <td colSpan={types.length + 1} style={{ background: 'var(--surface)', padding: '8px 12px' }}>
                      <span className="n-eyebrow">{g.title}</span>
                    </td>
                  </tr>
                  {g.rows.map((r) => (
                    <tr key={r.key}>
                      <td style={{ paddingLeft: r.depth ? 30 : 12, color: r.depth ? 'var(--text-soft)' : 'var(--text)' }}>{r.label}</td>
                      {types.map((bt) => {
                        const on = draft[bt]?.[r.key] !== false;
                        const parentOff = r.parent ? draft[bt]?.[r.parent] === false : false;
                        const label = `${g.title}: ${r.label} — ${meta.booking_types.find((b) => b.value === bt)?.label ?? bt}`;
                        return (
                          <td key={bt} style={{ textAlign: 'center', padding: '6px' }}>
                            <input
                              type="checkbox"
                              aria-label={label}
                              title={parentOff ? `${label} (hidden while its parent field is off)` : label}
                              checked={on}
                              disabled={!canWrite}
                              onChange={() => toggle(bt, r.key)}
                              style={{ width: 16, height: 16, margin: 0, accentColor: 'var(--accent)', opacity: parentOff ? 0.45 : 1, cursor: canWrite ? 'pointer' : 'default' }}
                            />
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
        <div style={{ padding: '0 16px 16px' }}>
          <SaveRow canWrite={canWrite} dirty={dirty} saving={saving} saved={saved} error={error} onSave={save}
            onDiscard={() => { setDraft(base); setError(null); }} label="Save event fields" />
        </div>
      </Panel>
    </Section>
  );
}
