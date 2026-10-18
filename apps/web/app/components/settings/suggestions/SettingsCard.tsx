'use client';

// The global knobs: switches, how the slots fill, how candidates score, how
// long a suggestion stays away.
import { useMemo, useState } from 'react';
import { call } from './api';
import { Actions, Field, Problems, Toggle, muted, sectionTitle } from './bits';
import { fromField, shown, toField } from './format';
import type { Meta, SettingRow } from './types';

const GROUPS: { id: SettingRow['group']; title: string; help: string }[] = [
  { id: 'switches', title: 'Switches', help: 'Turn the insight run and its rewording on or off everywhere.' },
  { id: 'slots', title: 'How the slots fill', help: 'Insights first, then waiting work, then catalogue prompts.' },
  { id: 'scoring', title: 'Scoring', help: 'Higher scores win the slots.' },
  { id: 'memory', title: 'Repeats', help: 'How long a suggestion stays away once seen, dismissed or explained.' },
];

export default function SettingsCard({ settings, meta, onSaved }: { settings: SettingRow[]; meta: Meta; onSaved: () => void }) {
  const rows = useMemo(() => settings.filter(s => s.group !== 'wording'), [settings]);
  const [draft, setDraft] = useState<Record<string, unknown>>(() => Object.fromEntries(rows.map(r => [r.name, r.value])));
  const [text, setText] = useState<Record<string, string>>(() => Object.fromEntries(rows.map(r => [r.name, toField(r.kind, r.value)])));
  const [busy, setBusy] = useState(false);
  const [problems, setProblems] = useState<string[] | null>(null);
  // New values from the server (after a save): start the form from them.
  const [seen, setSeen] = useState(rows);
  if (seen !== rows) {
    setSeen(rows);
    setDraft(Object.fromEntries(rows.map(r => [r.name, r.value])));
    setText(Object.fromEntries(rows.map(r => [r.name, toField(r.kind, r.value)])));
  }

  const changes = Object.fromEntries(rows.filter(r => draft[r.name] !== r.value).map(r => [r.name, draft[r.name]]));
  const anyChanged = rows.some(r => r.changed);

  const save = async (body: Record<string, unknown>) => {
    setBusy(true);
    setProblems(null);
    const res = await call('PUT', '/settings', { changes: body });
    setBusy(false);
    if (!res.ok) setProblems(res.problems ?? null);
    else onSaved();
  };
  const undo = async () => {
    setBusy(true);
    const res = await call('POST', '/undo', { key: 'settings' });
    setBusy(false);
    if (!res.ok) setProblems(res.problems ?? null);
    else onSaved();
  };

  return (
    <section className="n-card" style={{ padding: '16px 18px', marginBottom: 16 }} aria-labelledby="sugg-settings">
      <h3 id="sugg-settings" style={sectionTitle}>Ranking and timing</h3>
      <p style={{ ...muted, margin: '4px 0 8px' }}>For every organisation and venue. Changes reach Home within a minute.</p>
      {GROUPS.map(g => (
        <div key={g.id} style={{ marginTop: 12 }}>
          <div style={{ fontSize: 'var(--fs-xs)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--muted)' }}>{g.title}</div>
          <div style={{ ...muted, marginBottom: 2 }}>{g.help}</div>
          {rows.filter(r => r.group === g.id).map(r => (
            <Field key={r.name} label={r.label} changed={r.changed}
              help={<>{r.help} Default {shown(r.kind, r.default)}.{r.changed && (
                <> <button type="button" className="n-btn n-btn--link" style={{ fontSize: 'inherit', padding: 0 }}
                  onClick={() => { setDraft(d => ({ ...d, [r.name]: r.default })); setText(t => ({ ...t, [r.name]: toField(r.kind, r.default) })); }}>Use the default</button></>
              )}</>}>
              {r.kind === 'bool' ? (
                <Toggle on={Boolean(draft[r.name])} label={r.label} onChange={v => setDraft(d => ({ ...d, [r.name]: v }))} />
              ) : (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                  {r.kind === 'times' && <span style={muted}>×</span>}
                  {r.kind === 'dollars' && <span style={muted}>$</span>}
                  <input className="n-input" inputMode="decimal" aria-label={r.label} style={{ width: 80, textAlign: 'right' }}
                    value={text[r.name] ?? ''}
                    onChange={e => {
                      const t = e.target.value;
                      setText(x => ({ ...x, [r.name]: t }));
                      const n = fromField(r.kind, t);
                      setDraft(d => ({ ...d, [r.name]: n === null ? t : n }));
                    }} />
                  {r.kind === 'percent' && <span style={muted}>%</span>}
                  {r.kind === 'days' && <span style={muted}>days</span>}
                </span>
              )}
            </Field>
          ))}
        </div>
      ))}
      <Problems problems={problems} />
      <Actions dirty={Object.keys(changes).length > 0} busy={busy} changed={anyChanged} meta={meta}
        onSave={() => save(changes)}
        onReset={() => save(Object.fromEntries(rows.filter(r => r.changed).map(r => [r.name, null])))}
        onUndo={undo} />
    </section>
  );
}
