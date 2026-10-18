'use client';

// One insight detector: on/off, how much it counts, whose chats show it, its
// thresholds and its prompt templates — with a preview of a template against
// its example and against real live insights before it is saved.
import { useRef, useState } from 'react';
import { ChevronRight } from 'lucide-react';
import { AGENTS } from '../../layout/Sidebar';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import { call } from './api';
import { Actions, Field, Problems, Toggle, muted } from './bits';
import { fromField, shown, toField } from './format';
import type { DetectorRow } from './types';

const CADENCE: Record<DetectorRow['cadence'], string> = {
  daily: 'every day',
  weekly: 'Mondays',
  roster: 'Thursday–Sunday',
  'follow-up': 'after a note',
};
const MEMBERS = AGENTS.filter(a => a.id !== 'home');

interface Rendered { problems: string[]; example: string | null; live: { venue: string; now: string; rendered: string }[] }

function TemplateEditor({ detector, t, value, onChange }: {
  detector: string; t: DetectorRow['templates'][number]; value: string; onChange: (v: string) => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [preview, setPreview] = useState<Rendered | null>(null);
  const [busy, setBusy] = useState(false);
  const insert = (name: string) => {
    const el = ref.current;
    const at = el?.selectionStart ?? value.length;
    onChange(value.slice(0, at) + `{${name}}` + value.slice(el?.selectionEnd ?? at));
  };
  const run = async () => {
    setBusy(true);
    const res = await call<Rendered>('POST', '/render', { detector, template: t.name, text: value });
    setBusy(false);
    setPreview(res.ok && res.data ? res.data : { problems: res.problems ?? [], example: null, live: [] });
  };
  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text)' }}>
        {t.label}
        {value !== t.default && <span style={{ marginLeft: 6, fontSize: 'var(--fs-xs)', color: 'var(--accent)' }}>changed</span>}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, margin: '4px 0' }}>
        {Object.entries(t.placeholders).map(([name, what]) => (
          <button key={name} type="button" title={what} onClick={() => insert(name)}
            style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-xs)', padding: '1px 6px', borderRadius: 4, border: '1px solid var(--line)', background: 'var(--surface-alt)', cursor: 'pointer', color: 'var(--text-soft)' }}>
            {`{${name}}`}
          </button>
        ))}
      </div>
      <textarea ref={ref} className="n-input" aria-label={t.label} value={value} onChange={e => onChange(e.target.value)} rows={3}
        style={{ width: '100%', fontSize: 'var(--fs-sm)', lineHeight: 1.45 }} />
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 4 }}>
        <Button size="xs" onClick={run} disabled={busy}>{busy ? 'Filling in…' : 'Preview'}</Button>
        {value !== t.default && <Button size="xs" variant="quiet" onClick={() => onChange(t.default)}>Use the default words</Button>}
      </div>
      {preview && (
        <div style={{ marginTop: 6 }}>
          <Problems problems={preview.problems} />
          {preview.example && <p style={{ margin: '4px 0', fontSize: 'var(--fs-sm)' }}><span style={muted}>Example: </span>{preview.example}</p>}
          {preview.live.map((l, i) => (
            <div key={i} style={{ padding: '4px 0', borderTop: '1px solid var(--line)', fontSize: 'var(--fs-sm)' }}>
              <span style={muted}>{l.venue}: </span>{l.rendered}
            </div>
          ))}
          {preview.example && preview.live.length === 0 && (
            <p style={muted}>No live insight of this kind keeps its facts yet — they do from each venue&apos;s next run.</p>
          )}
        </div>
      )}
    </div>
  );
}

export default function DetectorCard({ d, onSaved }: { d: DetectorRow; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [weight, setWeight] = useState(() => String(d.valued ? d.weight : d.lift));
  const [members, setMembers] = useState<string[]>(d.members);
  const [thresholds, setThresholds] = useState<Record<string, string>>(() => Object.fromEntries(d.thresholds.map(t => [t.name, toField(t.kind, t.value)])));
  const [templates, setTemplates] = useState<Record<string, string>>(() => Object.fromEntries(d.templates.map(t => [t.name, t.value])));
  const [busy, setBusy] = useState(false);
  const [problems, setProblems] = useState<string[] | null>(null);
  // New values from the server (after a save): start the form from them.
  const [seen, setSeen] = useState(d);
  if (seen !== d) {
    setSeen(d);
    setWeight(String(d.valued ? d.weight : d.lift));
    setMembers(d.members);
    setThresholds(Object.fromEntries(d.thresholds.map(t => [t.name, toField(t.kind, t.value)])));
    setTemplates(Object.fromEntries(d.templates.map(t => [t.name, t.value])));
  }

  const weightKey = d.valued ? 'weight' : 'lift';
  const changes: Record<string, unknown> = {};
  if (Number(weight) !== (d.valued ? d.weight : d.lift)) changes[weightKey] = Number(weight);
  if ([...members].sort().join() !== [...d.members].sort().join()) changes.members = members;
  const th = Object.fromEntries(d.thresholds.filter(t => fromField(t.kind, thresholds[t.name] ?? '') !== t.value).map(t => [t.name, fromField(t.kind, thresholds[t.name] ?? '') ?? thresholds[t.name]]));
  if (Object.keys(th).length) changes.thresholds = th;
  const tp = Object.fromEntries(d.templates.filter(t => templates[t.name] !== t.value).map(t => [t.name, templates[t.name]]));
  if (Object.keys(tp).length) changes.templates = tp;

  const send = async (fn: () => Promise<{ ok: boolean; problems?: string[] }>) => {
    setBusy(true);
    setProblems(null);
    const res = await fn();
    setBusy(false);
    if (!res.ok) setProblems(res.problems ?? null);
    else onSaved();
  };

  return (
    <div className="n-card" style={{ padding: '10px 14px', marginBottom: 8, opacity: d.enabled ? 1 : 0.6 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <Toggle on={d.enabled} label={`${d.label} on`} disabled={busy}
          onChange={v => send(() => call('PUT', `/detectors/${d.name}`, { changes: { enabled: v } }))} />
        <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open}
          style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 8, border: 'none', background: 'transparent', padding: 0, cursor: 'pointer', textAlign: 'left' }}>
          <span style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text)' }}>{d.label}</span>
          <span style={muted}>{CADENCE[d.cadence]}</span>
          {d.changed.length > 0 && <Badge tone="warn">changed</Badge>}
          <span style={{ ...muted, marginLeft: 'auto' }}>{d.live.count} live</span>
          <Icon icon={ChevronRight} size={14} tone="muted" style={{ transform: open ? 'rotate(90deg)' : undefined, transition: 'transform 0.12s' }} />
        </button>
      </div>
      {open && (
        <div style={{ marginTop: 8 }}>
          <p style={{ ...muted, margin: '0 0 4px' }}>{d.description} Seen by people with <code>{d.audience}</code>.</p>
          <Field label={d.valued ? 'Weight (on its dollars a week)' : 'Lift (it has no dollars)'} changed={(d.valued ? d.weight !== d.default_weight : d.lift !== d.default_lift)}
            help={`Default ${d.valued ? d.default_weight : d.default_lift}. ${d.valued ? 'Score = 3 + weight × log₁₀(1 + $/week) ÷ 1.5.' : 'Score = 3 + lift.'}`}>
            <input className="n-input" inputMode="decimal" aria-label="Weight" value={weight} onChange={e => setWeight(e.target.value)} style={{ width: 70, textAlign: 'right' }} />
          </Field>
          <div style={{ padding: '8px 0', borderBottom: '1px solid var(--line)' }}>
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text)' }}>Shows in these team members&apos; new chats</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 4 }}>
              {MEMBERS.map(m => (
                <label key={m.id} style={{ fontSize: 'var(--fs-sm)', display: 'inline-flex', gap: 4 }}>
                  <input type="checkbox" checked={members.includes(m.id)}
                    onChange={e => setMembers(ms => (e.target.checked ? [...ms, m.id] : ms.filter(x => x !== m.id)))} />
                  {m.label}
                </label>
              ))}
            </div>
            <div style={muted}>Home shows every insight. Default: {d.default_members.length ? d.default_members.map(s => MEMBERS.find(m => m.id === s)?.label ?? s).join(', ') : 'Home only'}.</div>
          </div>
          {d.thresholds.map(t => (
            <Field key={t.name} label={t.label} changed={t.changed} help={`Default ${shown(t.kind, t.default)} (${shown(t.kind, t.min)} to ${shown(t.kind, t.max)}).`}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                {t.kind === 'dollars' && <span style={muted}>$</span>}
                {t.kind === 'times' && <span style={muted}>×</span>}
                <input className="n-input" inputMode="decimal" aria-label={t.label} value={thresholds[t.name] ?? ''}
                  onChange={e => setThresholds(x => ({ ...x, [t.name]: e.target.value }))} style={{ width: 80, textAlign: 'right' }} />
                {t.kind === 'percent' && <span style={muted}>%</span>}
                {(t.kind === 'days' || t.kind === 'hours') && <span style={muted}>{t.kind}</span>}
              </span>
            </Field>
          ))}
          {d.templates.map(t => (
            <TemplateEditor key={t.name} detector={d.name} t={t} value={templates[t.name] ?? ''}
              onChange={v => setTemplates(x => ({ ...x, [t.name]: v }))} />
          ))}
          <p style={{ ...muted, margin: '8px 0 0' }}>Thresholds and templates apply from each venue&apos;s next run; on/off, weight and members within a minute.</p>
          <Problems problems={problems} />
          <Actions dirty={Object.keys(changes).length > 0} busy={busy} changed={d.changed.length > 0} meta={d}
            onSave={() => send(() => call('PUT', `/detectors/${d.name}`, { changes }))}
            onReset={() => send(() => call('POST', '/reset', { key: `detector:${d.name}` }))}
            onUndo={() => send(() => call('POST', '/undo', { key: `detector:${d.name}` }))} />
        </div>
      )}
    </div>
  );
}
