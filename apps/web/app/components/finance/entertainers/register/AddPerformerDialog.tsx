'use client';

/**
 * "Add performer": name, email, phone and what they are, then Norm emails
 * them a personal onboarding link (D14: there is no form without a link).
 * The link is shown afterwards so a booker can also paste it into a text.
 */
import { useId, useState } from 'react';
import Dialog from '../../../ui/Dialog';
import Button from '../../../ui/Button';
import { entSend, errorText } from '../api';
import { validEmail } from '../logic';
import { CopyButton, DialogActions, Field, FieldGrid, Notice, Toggle, WriteError, inputStyle } from '../ui';
import type { InviteResult, Performer, PerformerType } from '../types';

const TYPE_LABEL: Record<PerformerType, string> = { solo: 'Solo performer', band: 'Band, duo or group', agent: 'Booking agent or agency' };

export default function AddPerformerDialog({ performerTypes, onCreated, onClose }: {
  performerTypes: PerformerType[];
  onCreated: (performer: Performer) => void;
  onClose: () => void;
}) {
  const ids = { name: useId(), email: useId(), phone: useId(), type: useId(), notes: useId() };
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [type, setType] = useState<PerformerType | ''>('');
  const [notes, setNotes] = useState('');
  const [sendInvite, setSendInvite] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ performer: Performer; invite: InviteResult | null } | null>(null);

  const save = async () => {
    if (!name.trim()) return setError('The performer needs a name.');
    if (sendInvite && !validEmail(email)) return setError('An email address is needed to send the onboarding link.');
    if (email && !validEmail(email)) return setError('That email address doesn’t look right.');
    setBusy(true);
    setError(null);
    try {
      const res = await entSend<{ performer: Performer; invite: InviteResult | null }>('POST', '/performers', {
        name: name.trim(),
        email: email.trim() || null,
        phone: phone.trim() || null,
        notes: notes.trim() || null,
        performer_type: type || null,
        send_invite: sendInvite,
      });
      setDone(res);
      onCreated(res.performer);
    } catch (e) {
      setError(errorText(e));
    }
    setBusy(false);
  };

  if (done) {
    return (
      <Dialog title={`${done.performer.name} added`} width={480} onClose={onClose}>
        {done.invite ? (
          <>
            <Notice tone={done.invite.sent ? 'ok' : 'warn'}>
              {done.invite.sent
                ? `The onboarding link was emailed to ${done.performer.email}.`
                : 'The link was made but the email didn’t go out (shadow mode sends only to the payroll contact).'}
            </Notice>
            {done.invite.url && (
              <div style={{ marginTop: 12 }}>
                <div className="n-label">Their personal link (also fine to text them)</div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <code style={{ flex: '1 1 240px', minWidth: 0, fontSize: 'var(--fs-xs)', overflowWrap: 'anywhere', padding: '6px 8px', background: 'var(--surface-alt)', borderRadius: 'var(--radius-sm)' }}>{done.invite.url}</code>
                  <CopyButton text={done.invite.url} />
                </div>
              </div>
            )}
          </>
        ) : (
          <Notice tone="info">No link was sent. Use &ldquo;Send onboarding link&rdquo; on their record when you&rsquo;re ready.</Notice>
        )}
        <DialogActions><Button variant="primary" onClick={onClose}>Done</Button></DialogActions>
      </Dialog>
    );
  }

  return (
    <Dialog title="Add performer" width={520} onClose={() => { if (!busy) onClose(); }}>
      <FieldGrid>
        <Field label="Name" htmlFor={ids.name} wide>
          <input id={ids.name} className="n-input" style={inputStyle} value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        </Field>
        <Field label="Email" htmlFor={ids.email}>
          <input id={ids.email} className="n-input" style={inputStyle} type="email" inputMode="email" autoComplete="off" value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Phone" htmlFor={ids.phone}>
          <input id={ids.phone} className="n-input" style={inputStyle} inputMode="tel" autoComplete="off" value={phone} onChange={(e) => setPhone(e.target.value)} />
        </Field>
        <Field label="What they are" htmlFor={ids.type} hint="They confirm this on the form; it just helps you find them.">
          <select id={ids.type} className="n-select" style={inputStyle} value={type} onChange={(e) => setType(e.target.value as PerformerType | '')}>
            <option value="">Not sure yet</option>
            {performerTypes.map((t) => <option key={t} value={t}>{TYPE_LABEL[t] ?? t}</option>)}
          </select>
        </Field>
        <Field label="Notes" htmlFor={ids.notes} wide>
          <textarea id={ids.notes} className="n-input" style={inputStyle} rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
        </Field>
      </FieldGrid>
      <div style={{ marginTop: 12 }}>
        <Toggle checked={sendInvite} onChange={setSendInvite}>Email them the onboarding link now</Toggle>
      </div>
      <WriteError message={error} />
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="primary" onClick={save} disabled={busy}>{busy ? 'Adding…' : sendInvite ? 'Add and send link' : 'Add'}</Button>
      </DialogActions>
    </Dialog>
  );
}
