'use client';

// The words: what Haiku is told when it rewords insights (with a "try it"
// that shows before and after), and what Norm is told in a chat that began
// from an insight.
import { useState } from 'react';
import Button from '../../ui/Button';
import { call } from './api';
import { Actions, Problems, muted, sectionTitle } from './bits';
import type { Meta, SettingRow } from './types';

interface Trial { venue: string; results: { insight_id: string; template: string; worded: string; kept: boolean }[] }

function TextSetting({ row, meta, placeholders, onSaved, extra }: {
  row: SettingRow; meta: Meta; placeholders?: Record<string, string>; onSaved: () => void; extra?: (draft: string) => React.ReactNode;
}) {
  const [draft, setDraft] = useState(String(row.value ?? ''));
  const [busy, setBusy] = useState(false);
  const [problems, setProblems] = useState<string[] | null>(null);
  // A new value from the server (after a save): start from it.
  const [seen, setSeen] = useState(row.value);
  if (seen !== row.value) {
    setSeen(row.value);
    setDraft(String(row.value ?? ''));
  }

  const save = async (value: string | null) => {
    setBusy(true);
    setProblems(null);
    const res = await call('PUT', '/settings', { changes: { [row.name]: value } });
    setBusy(false);
    if (!res.ok) setProblems(res.problems ?? null);
    else onSaved();
  };
  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text)' }}>{row.label}</span>
        {row.changed && <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--accent)' }}>changed</span>}
      </div>
      <p style={{ ...muted, margin: '2px 0 6px' }}>{row.help}</p>
      {placeholders && (
        <p style={{ ...muted, margin: '0 0 6px' }}>
          Placeholders: {Object.entries(placeholders).map(([k, v]) => <code key={k} title={v} style={{ marginRight: 6 }}>{`{${k}}`}</code>)}
        </p>
      )}
      <textarea className="n-input" aria-label={row.label} value={draft} onChange={e => setDraft(e.target.value)}
        rows={Math.min(18, Math.max(6, draft.split('\n').length + 1))}
        style={{ width: '100%', fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', lineHeight: 1.5 }} />
      <Problems problems={problems} />
      <Actions dirty={draft !== row.value} busy={busy} changed={row.changed} meta={meta}
        onSave={() => save(draft)} onReset={() => save(null)} resetLabel="Use the default words" />
      {extra?.(draft)}
    </div>
  );
}

export default function WordingCard({ settings, meta, placeholders, version, onSaved }: {
  settings: SettingRow[]; meta: Meta; placeholders: Record<string, string>; version: string; onSaved: () => void;
}) {
  const instructions = settings.find(s => s.name === 'wording_instructions');
  const guidance = settings.find(s => s.name === 'insight_guidance');
  const wordingOn = settings.find(s => s.name === 'wording_enabled');
  const [trial, setTrial] = useState<Trial | null>(null);
  const [trying, setTrying] = useState(false);
  const [trialProblems, setTrialProblems] = useState<string[] | null>(null);

  const tryIt = async (text: string) => {
    setTrying(true);
    setTrialProblems(null);
    const res = await call<Trial>('POST', '/try-wording', { instructions: text });
    setTrying(false);
    if (res.ok && res.data) setTrial(res.data);
    else setTrialProblems(res.problems ?? null);
  };

  return (
    <section className="n-card" style={{ padding: '16px 18px', marginBottom: 16 }} aria-labelledby="sugg-words">
      <h3 id="sugg-words" style={sectionTitle}>Words</h3>
      <p style={{ ...muted, margin: '4px 0 0' }}>
        Insight prompts are written from each detector&apos;s template (below, per team member), then reworded by Haiku in the
        organisation&apos;s own words — checked so no number is added or changed.
        {wordingOn && (wordingOn.value ? ' Rewording is on.' : ' Rewording is off.')} Wording version {version}.
      </p>
      {instructions && (
        <TextSetting row={instructions} meta={meta} onSaved={onSaved} extra={draft => (
          <div style={{ marginTop: 8 }}>
            <Button size="sm" onClick={() => tryIt(draft)} disabled={trying}>{trying ? 'Trying…' : 'Try these words'}</Button>
            <span style={{ ...muted, marginLeft: 8 }}>One Haiku call on a venue&apos;s live insights — about $0.004, Norm&apos;s cost. Nothing is saved.</span>
            <Problems problems={trialProblems} />
            {trial && (
              <div style={{ marginTop: 10 }}>
                <div style={muted}>At {trial.venue}:</div>
                {trial.results.map(r => (
                  <div key={r.insight_id} style={{ padding: '8px 0', borderBottom: '1px solid var(--line)', fontSize: 'var(--fs-sm)' }}>
                    <div style={{ color: 'var(--muted)' }}>{r.template}</div>
                    <div style={{ color: 'var(--text)', marginTop: 2 }}>
                      {r.kept ? '→ ' : '✗ '}{r.worded || '(no answer)'}
                      {!r.kept && <span style={{ ...muted, marginLeft: 6 }}>the check would keep the template</span>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )} />
      )}
      {guidance && <TextSetting row={guidance} meta={meta} placeholders={placeholders} onSaved={onSaved} />}
    </section>
  );
}
