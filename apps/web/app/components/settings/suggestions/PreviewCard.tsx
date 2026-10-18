'use client';

// "What would this person see?" — the suggestions they'd get now, and why
// every other candidate wasn't picked. Reads only; nothing counts as shown.
import { useEffect, useState } from 'react';
import { apiFetch } from '../../../lib/api';
import { AGENTS } from '../../layout/Sidebar';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import { call } from './api';
import { Problems, muted, sectionTitle } from './bits';
import type { Candidate, Preview } from './types';

interface Person { id: string; email: string; name: string; venues: { id: string; name: string }[] }

const memberName = (slug: string) => AGENTS.find(a => a.id === slug)?.label ?? (slug === 'norm' ? 'Norm' : slug);

function Row({ c, picked }: { c: Candidate; picked?: boolean }) {
  return (
    <tr style={{ borderBottom: '1px solid var(--line)', verticalAlign: 'top' }}>
      <td style={{ padding: '6px 8px 6px 0', fontSize: 'var(--fs-sm)', color: picked ? 'var(--text)' : 'var(--text-soft)' }}>
        {c.text}
        {c.venue_name && <span style={{ ...muted, marginLeft: 6 }}>{c.venue_name}</span>}
      </td>
      <td style={{ padding: '6px 8px', whiteSpace: 'nowrap' }}><Badge tone={c.kind === 'insight' ? 'accent' : 'neutral'}>{c.kind}</Badge></td>
      <td style={{ padding: '6px 8px', ...muted, whiteSpace: 'nowrap' }}>{memberName(c.member)}</td>
      <td style={{ padding: '6px 8px', ...muted, textAlign: 'right' }}>{c.score ?? '—'}</td>
      <td style={{ padding: '6px 0 6px 8px', ...muted }}>{picked ? 'Shown' : c.reason}</td>
    </tr>
  );
}

export default function PreviewCard() {
  const [q, setQ] = useState('');
  const [people, setPeople] = useState<Person[]>([]);
  const [person, setPerson] = useState<Person | null>(null);
  const [venueId, setVenueId] = useState('');
  const [member, setMember] = useState('home');
  const [phone, setPhone] = useState(false);
  const [result, setResult] = useState<Preview | null>(null);
  const [problems, setProblems] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [running, setRunning] = useState<string | null>(null);

  useEffect(() => {
    if (person) return;
    const t = setTimeout(() => {
      apiFetch(`/api/admin/suggestions/people?q=${encodeURIComponent(q)}`)
        .then(r => (r.ok ? r.json() : { people: [] }))
        .then(d => setPeople(d.people ?? []))
        .catch(() => setPeople([]));
    }, 250);
    return () => clearTimeout(t);
  }, [q, person]);

  const preview = async () => {
    if (!person) return;
    setBusy(true);
    setProblems(null);
    const res = await call<Preview>('POST', '/preview', { user_id: person.id, venue_id: venueId || null, member: member === 'home' ? null : member, limit: phone ? 2 : 4 });
    setBusy(false);
    if (res.ok && res.data) setResult(res.data);
    else setProblems(res.problems ?? null);
  };

  const runNow = async () => {
    if (!venueId) return;
    setRunning('Running the insight check — a minute or two, reading Loaded…');
    try {
      const res = await apiFetch('/api/admin/insights/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ venue_ids: [venueId], cadences: ['daily', 'weekly', 'roster'], rerun: true }),
      });
      const d = await res.json().catch(() => null);
      const ran = d?.ran?.[0];
      setRunning(ran ? (ran.ok ? `Done: ${ran.found} found.` : `It failed: ${ran.error}`) : d?.off ? 'Insights are switched off.' : 'Nothing ran.');
      if (ran?.ok) preview();
    } catch {
      setRunning('Norm couldn’t be reached.');
    }
  };

  return (
    <section className="n-card" style={{ padding: '16px 18px', marginBottom: 16 }} aria-labelledby="sugg-preview">
      <h3 id="sugg-preview" style={sectionTitle}>What would someone see?</h3>
      <p style={{ ...muted, margin: '4px 0 10px' }}>Their suggestions right now, and why everything else wasn&apos;t picked. Nothing is recorded as shown.</p>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
        {person ? (
          <span style={{ fontSize: 'var(--fs-sm)' }}>
            {person.name || person.email} <span style={muted}>{person.email}</span>{' '}
            <button type="button" className="n-btn n-btn--link" onClick={() => { setPerson(null); setResult(null); }}>change</button>
          </span>
        ) : (
          <input className="n-input" placeholder="Find a person — name or email" aria-label="Find a person" value={q} onChange={e => setQ(e.target.value)} style={{ minWidth: 260 }} />
        )}
        {person && (
          <>
            <select className="n-input" aria-label="Venue" value={venueId} onChange={e => setVenueId(e.target.value)}>
              <option value="">Their first venue</option>
              {person.venues.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}
            </select>
            <select className="n-input" aria-label="Where" value={member} onChange={e => setMember(e.target.value)}>
              {AGENTS.map(a => <option key={a.id} value={a.id}>{a.id === 'home' ? 'Home' : `${a.label}'s new chat`}</option>)}
            </select>
            <label style={{ ...muted, display: 'inline-flex', gap: 4, alignItems: 'center' }}>
              <input type="checkbox" checked={phone} onChange={e => setPhone(e.target.checked)} /> on a phone
            </label>
            <Button variant="primary" size="sm" onClick={preview} disabled={busy}>{busy ? 'Looking…' : 'Preview'}</Button>
            {venueId && <Button size="sm" onClick={runNow} disabled={!!running && running.startsWith('Running')}>Run insights now</Button>}
          </>
        )}
      </div>
      {!person && people.length > 0 && (
        <div style={{ marginTop: 6, maxHeight: 180, overflowY: 'auto', border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
          {people.map(p => (
            <button key={p.id} type="button" onClick={() => { setPerson(p); setVenueId(p.venues[0]?.id ?? ''); }}
              style={{ display: 'block', width: '100%', textAlign: 'left', padding: '6px 10px', border: 'none', background: 'transparent', cursor: 'pointer', fontSize: 'var(--fs-sm)' }}>
              {p.name || p.email} <span style={muted}>{p.email} · {p.venues.length} venue{p.venues.length === 1 ? '' : 's'}</span>
            </button>
          ))}
        </div>
      )}
      {running && <p style={{ ...muted, margin: '8px 0 0' }}>{running}</p>}
      <Problems problems={problems} />
      {result && (
        <div style={{ marginTop: 12, overflowX: 'auto' }}>
          <p style={{ ...muted, margin: '0 0 6px' }}>{result.venue ?? 'No venue'} · their time {result.local_time}</p>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ ...muted, textAlign: 'left' }}>
                <th style={{ fontWeight: 500, padding: '0 8px 4px 0' }}>Suggestion</th>
                <th style={{ fontWeight: 500, padding: '0 8px 4px' }}>Kind</th>
                <th style={{ fontWeight: 500, padding: '0 8px 4px' }}>Member</th>
                <th style={{ fontWeight: 500, padding: '0 8px 4px', textAlign: 'right' }}>Score</th>
                <th style={{ fontWeight: 500, padding: '0 0 4px 8px' }}>Why</th>
              </tr>
            </thead>
            <tbody>
              {result.picked.map(c => <Row key={c.id} c={c} picked />)}
              {result.others.map((c, i) => <Row key={`${c.id}-${i}`} c={c} />)}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
