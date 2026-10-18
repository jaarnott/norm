'use client';

// One team member's catalogue prompts: what each says, when it shows, who can
// see it and what it needs — on/off, edit, add, and back to the default.
import { useState } from 'react';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Dialog from '../../ui/Dialog';
import { call } from './api';
import { Problems, Toggle, muted } from './bits';
import { promptId, when } from './format';
import type { PromptRow, SuggestionAdmin } from './types';

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
type Draft = Omit<PromptRow, 'can_undo' | 'updated_by' | 'updated_at' | 'custom' | 'changed'>;
const FIELDS: (keyof Draft)[] = ['member', 'app', 'text', 'tools', 'kind', 'permission', 'connection', 'days', 'part', 'month_start', 'multi_venue', 'weight', 'enabled'];

function PromptDialog({ start, isNew, admin, onClose, onSaved }: {
  start: Draft; isNew: boolean; admin: SuggestionAdmin; onClose: () => void; onSaved: () => void;
}) {
  const [d, setD] = useState<Draft>(start);
  const [tools, setTools] = useState(start.tools.join(', '));
  const [problems, setProblems] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setD(x => ({ ...x, [k]: v }));

  const save = async () => {
    const full: Draft = { ...d, tools: tools.split(',').map(t => t.trim()).filter(Boolean) };
    const changes = Object.fromEntries(FIELDS.filter(k => isNew || JSON.stringify(full[k]) !== JSON.stringify(start[k])).map(k => [k, full[k]]));
    setBusy(true);
    setProblems(null);
    // A new prompt's id comes from its words: reports.how_many_covers.
    const id = isNew ? promptId(d.member, full.text, new Set(admin.prompts.map(p => p.id))) : d.id;
    const res = await call('PUT', `/prompts/${encodeURIComponent(id)}`, { changes });
    setBusy(false);
    if (!res.ok) setProblems(res.problems ?? null);
    else onSaved();
  };

  const row = (label: string, control: React.ReactNode, help?: string) => (
    <label style={{ display: 'grid', gridTemplateColumns: '120px minmax(0, 1fr)', gap: 8, alignItems: 'center', padding: '6px 0' }}>
      <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{label}</span>
      <span>{control}{help && <span style={{ ...muted, display: 'block' }}>{help}</span>}</span>
    </label>
  );
  return (
    <Dialog title={isNew ? 'Add a prompt' : 'Edit a prompt'} width={560} onClose={onClose} closeButton>
      <div style={{ padding: '4px 20px 16px' }}>
        {row('Prompt', <textarea className="n-input" rows={2} value={d.text} onChange={e => set('text', e.target.value)} style={{ width: '100%' }} />, '{venue} is the person’s venue. One line on Home; under 80 characters reads best.')}
        {row('Kind', (
          <select className="n-input" value={d.kind} onChange={e => set('kind', e.target.value as Draft['kind'])}>
            <option value="regular">regular — something they ask often</option>
            <option value="discover">discover — something new to try</option>
          </select>
        ))}
        {row('Weight', <input className="n-input" type="number" step="0.1" min={0} max={5} value={d.weight} onChange={e => set('weight', Number(e.target.value))} style={{ width: 90 }} />, '1 is usual; up to 5.')}
        {row('Days', (
          <span style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {DAYS.map((name, i) => (
              <label key={name} style={{ fontSize: 'var(--fs-sm)', display: 'inline-flex', gap: 3 }}>
                <input type="checkbox" checked={!!d.days?.includes(i)} onChange={e => {
                  const days = new Set(d.days ?? []);
                  if (e.target.checked) days.add(i); else days.delete(i);
                  set('days', days.size ? [...days].sort() : null);
                }} />{name}
              </label>
            ))}
          </span>
        ), 'None ticked = any day.')}
        {row('Time of day', (
          <select className="n-input" value={d.part ?? ''} onChange={e => set('part', (e.target.value || null) as Draft['part'])}>
            <option value="">any time</option>
            <option value="morning">mornings</option>
            <option value="afternoon">afternoons</option>
            <option value="evening">evenings</option>
          </select>
        ))}
        {row('Also', (
          <span style={{ display: 'flex', flexWrap: 'wrap', gap: 12, fontSize: 'var(--fs-sm)' }}>
            <label><input type="checkbox" checked={d.month_start} onChange={e => set('month_start', e.target.checked)} /> first week of the month only</label>
            <label><input type="checkbox" checked={d.multi_venue} onChange={e => set('multi_venue', e.target.checked)} /> needs two or more venues</label>
          </span>
        ))}
        {row('Permission', (
          <select className="n-input" value={d.permission ?? ''} onChange={e => set('permission', e.target.value || null)}>
            <option value="">anyone</option>
            {admin.permissions.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        ))}
        {row('App', (
          <select className="n-input" value={d.app} onChange={e => set('app', e.target.value)}>
            {admin.apps.map(a => <option key={a.slug} value={a.slug}>{a.name}</option>)}
          </select>
        ), 'Shown only where this App is on.')}
        {row('Connection', <input className="n-input" value={d.connection ?? ''} placeholder="none" onChange={e => set('connection', e.target.value || null)} style={{ width: 160 }} />, 'Shown only when this connection works for the venue (e.g. loadedhub).')}
        {row('Tools', <input className="n-input" value={tools} onChange={e => setTools(e.target.value)} style={{ width: '100%' }} />, 'connector.action, comma-separated. Someone who has used one in 30 days asks it "often".')}
        <Problems problems={problems} />
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 12 }}>
          <Button size="sm" onClick={onClose}>Cancel</Button>
          <Button variant="primary" size="sm" onClick={save} disabled={busy || !d.text.trim()}>{busy ? 'Saving…' : 'Save'}</Button>
        </div>
      </div>
    </Dialog>
  );
}

export default function PromptList({ member, admin, onSaved }: { member: string; admin: SuggestionAdmin; onSaved: () => void }) {
  const prompts = admin.prompts.filter(p => p.member === member || (member === 'home' && p.member === 'norm'));
  const [editing, setEditing] = useState<{ draft: Draft; isNew: boolean; row?: PromptRow } | null>(null);
  const [problems, setProblems] = useState<string[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const act = async (id: string, fn: () => Promise<{ ok: boolean; problems?: string[] }>) => {
    setBusy(id);
    setProblems(null);
    const res = await fn();
    setBusy(null);
    if (!res.ok) setProblems(res.problems ?? null);
    else onSaved();
  };
  const memberSlug = member === 'home' ? 'norm' : member;
  const addNew = () => {
    const taken = new Set(admin.prompts.map(p => p.id));
    const base = { ...admin.new_prompt, member: memberSlug, app: admin.prompts.find(p => p.member === memberSlug)?.app ?? 'norm-core' } as Draft;
    setEditing({ draft: { ...base, id: promptId(memberSlug, 'new prompt', taken), text: '' }, isNew: true });
  };

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8 }}>
        <h4 style={{ margin: '12px 0 4px', fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text)' }}>Prompts</h4>
        <Button size="xs" onClick={addNew}>Add a prompt</Button>
      </div>
      {prompts.length === 0 && <p style={muted}>No prompts for this member yet.</p>}
      {prompts.map(p => (
        <div key={p.id} style={{ display: 'grid', gridTemplateColumns: 'auto minmax(0, 1fr) auto', gap: 10, alignItems: 'start', padding: '8px 0', borderBottom: '1px solid var(--line)', opacity: p.enabled ? 1 : 0.55 }}>
          <Toggle on={p.enabled} label={`${p.text} on`} disabled={busy === p.id}
            onChange={v => act(p.id, () => call('PUT', `/prompts/${encodeURIComponent(p.id)}`, { changes: { enabled: v } }))} />
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text)' }}>
              {p.text}{' '}
              {p.kind === 'discover' && <Badge tone="info">new to try</Badge>}{' '}
              {p.custom && <Badge tone="accent">added</Badge>}{' '}
              {!p.custom && p.changed.length > 0 && <Badge tone="warn">changed</Badge>}
            </div>
            <div style={muted}>
              {when(p)} · {p.permission ?? 'anyone'} · {p.app}{p.connection ? ` + ${p.connection}` : ''} · weight {p.weight}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            <Button size="xs" variant="quiet" onClick={() => setEditing({ draft: Object.fromEntries(FIELDS.map(k => [k, p[k]]).concat([['id', p.id]])) as Draft, isNew: false, row: p })}>Edit</Button>
            {!p.custom && p.changed.length > 0 && (
              <Button size="xs" variant="quiet" disabled={busy === p.id} onClick={() => act(p.id, () => call('POST', '/reset', { key: `prompt:${p.id}` }))}>Use the default</Button>
            )}
            {p.custom && (
              <Button size="xs" variant="quiet" disabled={busy === p.id} onClick={() => act(p.id, () => call('DELETE', `/prompts/${encodeURIComponent(p.id)}`))}>Delete</Button>
            )}
            {p.can_undo && (
              <Button size="xs" variant="quiet" disabled={busy === p.id} onClick={() => act(p.id, () => call('POST', '/undo', { key: `prompt:${p.id}` }))}>Undo</Button>
            )}
          </div>
        </div>
      ))}
      <Problems problems={problems} />
      {editing && (
        <PromptDialog start={editing.draft} isNew={editing.isNew} admin={admin}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); onSaved(); }} />
      )}
    </div>
  );
}
