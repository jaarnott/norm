'use client';

/**
 * A "pay each member separately" band: the members (each gets a personal
 * link and becomes their own performer), their invite state, and the fee
 * split finance sets — shares in percent that must add up to 100 (the API
 * takes basis points summing to 10,000). Anyone with venue access may edit
 * the members; only `payroll:write` may set the shares.
 */
import { useId, useState } from 'react';
import { Plus, Send, Trash2 } from 'lucide-react';
import Badge from '../../../ui/Badge';
import Button from '../../../ui/Button';
import IconButton from '../../../ui/IconButton';
import { entSend, errorText } from '../api';
import { when } from '../format';
import { SHARE_TOTAL_BP, bpToPercent, percentToBp, sharesTotal, validEmail } from '../logic';
import { Hint, Notice, Section, WriteError, inputStyle } from '../ui';
import type { BandMember } from '../types';

interface Draft {
  id?: string;
  name: string;
  email: string;
  phone: string;
}

export default function BandMembersSection({ performerId, members, canPay, onChanged }: {
  performerId: string;
  members: BandMember[];
  canPay: boolean;
  onChanged: (members: BandMember[]) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [shares, setShares] = useState<Record<string, string> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const baseId = useId();

  const startEdit = () => {
    setDrafts(members.map((m) => ({ id: m.id, name: m.name, email: m.email ?? '', phone: m.phone ?? '' })));
    setEditing(true);
    setError(null);
  };

  const saveMembers = async () => {
    const kept = drafts.filter((d) => d.name.trim());
    if (kept.some((d) => d.email && !validEmail(d.email))) return setError('One of the email addresses doesn’t look right.');
    if (kept.length > 20) return setError('A band can list up to 20 members.');
    setBusy(true);
    setError(null);
    try {
      const res = await entSend<{ members: BandMember[] }>('PUT', `/performers/${performerId}/members`, {
        members: kept.map((d) => ({ id: d.id, name: d.name.trim(), email: d.email.trim() || null, phone: d.phone.trim() || null })),
      });
      onChanged(res.members);
      setEditing(false);
    } catch (e) {
      setError(errorText(e));
    }
    setBusy(false);
  };

  const startShares = () => {
    setShares(Object.fromEntries(members.map((m) => [m.id, bpToPercent(m.share_bp)])));
    setError(null);
  };

  const sharesBp = shares ? Object.fromEntries(Object.entries(shares).map(([id, v]) => [id, percentToBp(v) ?? NaN])) : null;
  const sharesSum = sharesBp ? sharesTotal(sharesBp) : 0;
  const sharesOk = sharesBp !== null && Object.values(sharesBp).every((v) => Number.isFinite(v)) && sharesSum === SHARE_TOTAL_BP;

  const saveShares = async () => {
    if (!sharesBp || !sharesOk) return setError('Shares must add up to 100%.');
    setBusy(true);
    setError(null);
    try {
      await entSend('PUT', `/performers/${performerId}/members/shares`, { shares: sharesBp });
      onChanged(members.map((m) => ({ ...m, share_bp: sharesBp[m.id] })));
      setShares(null);
    } catch (e) {
      setError(errorText(e));
    }
    setBusy(false);
  };

  const resend = async (member: BandMember) => {
    setBusy(true);
    setNote(null);
    try {
      const res = await entSend<{ sent: boolean; url?: string }>('POST', `/performers/${performerId}/members/${member.id}/invite`);
      setNote(res.sent ? `A new link was emailed to ${member.name}.` : `A new link was made for ${member.name} but the email didn’t go out (shadow mode).`);
    } catch (e) {
      setNote(errorText(e));
    }
    setBusy(false);
  };

  const split = (members.length ? members.reduce((s, m) => s + m.share_bp, 0) : 0) === SHARE_TOTAL_BP;

  return (
    <Section
      title="Band members"
      badge={members.length ? <Badge tone={split ? 'ok' : 'warn'}>{split ? 'Shares add up' : 'Shares don’t add up to 100%'}</Badge> : null}
      actions={
        !editing && !shares ? (
          <>
            {canPay && members.length > 0 && <Button size="sm" onClick={startShares}>Set shares</Button>}
            <Button size="sm" onClick={startEdit}>{members.length ? 'Edit members' : 'Add members'}</Button>
          </>
        ) : null
      }
    >
      <Notice tone="info">Each member gets their own link and fills in the form for themselves — their own lane, bank account and declaration. The band isn&rsquo;t payable until every member is accepted.</Notice>

      {editing ? (
        <div style={{ marginTop: 12 }}>
          {drafts.map((d, i) => (
            <div key={d.id ?? `new-${i}`} style={{ display: 'grid', gridTemplateColumns: 'minmax(120px, 1fr) minmax(160px, 1.3fr) minmax(100px, 0.8fr) auto', gap: 8, alignItems: 'center', marginBottom: 8 }}>
              <input className="n-input" style={inputStyle} placeholder="Name" aria-label={`Member ${i + 1} name`} value={d.name} onChange={(e) => setDrafts((p) => p.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
              <input className="n-input" style={inputStyle} placeholder="Email" type="email" aria-label={`Member ${i + 1} email`} value={d.email} onChange={(e) => setDrafts((p) => p.map((x, j) => (j === i ? { ...x, email: e.target.value } : x)))} />
              <input className="n-input" style={inputStyle} placeholder="Phone" inputMode="tel" aria-label={`Member ${i + 1} phone`} value={d.phone} onChange={(e) => setDrafts((p) => p.map((x, j) => (j === i ? { ...x, phone: e.target.value } : x)))} />
              <IconButton icon={Trash2} label={`Remove ${d.name || 'member'}`} onClick={() => setDrafts((p) => p.filter((_, j) => j !== i))} />
            </div>
          ))}
          <Button size="sm" icon={Plus} onClick={() => setDrafts((p) => [...p, { name: '', email: '', phone: '' }])} disabled={drafts.length >= 20}>Add member</Button>
          <Hint style={{ marginTop: 6 }}>A new member gets an equal share and an onboarding link; a removed member keeps their past payments.</Hint>
          <WriteError message={error} />
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 12 }}>
            <Button onClick={() => setEditing(false)} disabled={busy}>Cancel</Button>
            <Button variant="primary" onClick={saveMembers} disabled={busy}>{busy ? 'Saving…' : 'Save members'}</Button>
          </div>
        </div>
      ) : shares ? (
        <div style={{ marginTop: 12 }}>
          {members.map((m) => (
            <div key={m.id} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
              <label htmlFor={`${baseId}-${m.id}`} style={{ flex: 1, minWidth: 0, fontSize: 'var(--fs-base)' }}>{m.name}</label>
              <span style={{ position: 'relative' }}>
                <input id={`${baseId}-${m.id}`} className="n-input" inputMode="decimal" value={shares[m.id] ?? ''} onChange={(e) => setShares((p) => ({ ...(p ?? {}), [m.id]: e.target.value }))} style={{ width: 96, paddingRight: 24, textAlign: 'right' }} />
                <span aria-hidden style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>%</span>
              </span>
            </div>
          ))}
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 'var(--fs-sm)', color: sharesOk ? 'var(--ok)' : 'var(--error)', fontWeight: 600 }}>
            <span>Total</span>
            <span>{Number.isFinite(sharesSum) ? bpToPercent(sharesSum) : '?'}%</span>
          </div>
          <WriteError message={error} />
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 12 }}>
            <Button onClick={() => setShares(null)} disabled={busy}>Cancel</Button>
            <Button variant="primary" onClick={saveShares} disabled={busy || !sharesOk}>{busy ? 'Saving…' : 'Save shares'}</Button>
          </div>
        </div>
      ) : members.length === 0 ? (
        <div style={{ marginTop: 12, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>No members listed yet.</div>
      ) : (
        <div style={{ marginTop: 12, overflowX: 'auto' }}>
          <table className="n-table" style={{ minWidth: 520 }}>
            <thead>
              <tr><th>Member</th><th>Contact</th><th className="num">Share</th><th>Setup</th><th aria-label="Actions" /></tr>
            </thead>
            <tbody>
              {members.map((m) => (
                <tr key={m.id}>
                  <td>{m.name}</td>
                  <td style={{ color: 'var(--text-soft)' }}>{[m.email, m.phone].filter(Boolean).join(' · ') || '—'}</td>
                  <td className="num">{bpToPercent(m.share_bp)}%</td>
                  <td>
                    {m.accepted ? <Badge tone="ok">Accepted</Badge> : m.invite_status === 'submitted' ? <Badge tone="accent">Submitted</Badge> : <Badge tone="info">Invited</Badge>}
                    {m.invite_sent_at && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginTop: 2 }}>link sent {when(m.invite_sent_at)}{m.reminders_sent ? `, ${m.reminders_sent} reminder${m.reminders_sent === 1 ? '' : 's'}` : ''}</div>}
                  </td>
                  <td style={{ textAlign: 'right' }}>
                    {!m.accepted && <Button size="sm" icon={Send} onClick={() => resend(m)} disabled={busy}>Resend link</Button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {note && <div style={{ marginTop: 8, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{note}</div>}
    </Section>
  );
}
