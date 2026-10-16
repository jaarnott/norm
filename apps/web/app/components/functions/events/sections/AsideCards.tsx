'use client';

/**
 * The event page's side column: who the client is (with a correction form —
 * it changes the contact record itself, so their other bookings see it), the
 * portal link's state, the enquiry it came from, linked mail threads, and the
 * activity: the timeline and the field-by-field changes log.
 */
import { useId, useState } from 'react';
import { ExternalLink, Mail, Pencil, Phone, Plus, UserMinus, UserPlus } from 'lucide-react';
import Badge from '../../../ui/Badge';
import { ConfirmDialog } from '../../../ui/Dialog';
import Button from '../../../ui/Button';
import Icon from '../../../ui/Icon';
import Tabs from '../../../ui/Tabs';
import { fnGet, fnSend } from '../../shared/api';
import { instantLabel, relativeLabel } from '../../shared/format';
import { CopyButton, currentPortalLink } from '../dialogs';
import { changeFieldLabel, changeValueText, humaniseDetail, linkSent, newestFirst, splitEmails, textOrNull } from '../logic';
import { contactLink, enquiryLink, threadLink } from '../nav';
import { Field, SectionCard, WriteError, inputStyle } from '../ui';
import { errorText, isStale, useLoad } from '../useLoad';
import type { EventChange, EventDetail, PortalPerson, TimelineItem } from '../types';
import type { SectionCtx } from './kit';

const line = { fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' } as const;

export function ContactCard({ ctx }: { ctx: SectionCtx }) {
  const ids = { name: useId(), phone: useId(), company: useId() };
  const c = ctx.detail.contact;
  const editing = ctx.editing === 'contact';
  const [draft, setDraft] = useState({ name: '', phone: '', company: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  if (!c) return null;

  const start = () => {
    setDraft({ name: c.name ?? '', phone: c.phone ?? '', company: c.company ?? '' });
    setError(null);
    ctx.setEditing('contact');
  };
  const save = async () => {
    const body: Record<string, string | null> = {};
    if (draft.name.trim() !== (c.name ?? '')) body.name = draft.name.trim();
    if ((textOrNull(draft.phone) ?? null) !== (c.phone ?? null)) body.phone = textOrNull(draft.phone);
    if ((textOrNull(draft.company) ?? null) !== (c.company ?? null)) body.company = textOrNull(draft.company);
    if (!Object.keys(body).length) {
      ctx.setEditing(null);
      return;
    }
    if (body.name === '') {
      setError(new Error('A contact needs a name.'));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const ok = await ctx.save(async (note) => fnSend<EventDetail>('PATCH', `/events/${ctx.detail.event.id}/contact`, { ...body, note }));
      if (ok) ctx.setEditing(null);
    } catch (e) {
      setError(e);
    }
    setBusy(false);
  };
  const otherEditing = ctx.editing !== null && !editing;

  return (
    <SectionCard
      title="Client"
      actions={!editing && (
        <Button size="sm" variant="quiet" icon={Pencil} onClick={start} disabled={!ctx.canWrite || otherEditing}
          title={ctx.lockReason ?? (otherEditing ? 'Finish the section you’re editing first' : 'Correct the contact’s details')}>
          Edit
        </Button>
      )}
      footer={editing ? (
        <>
          <Button onClick={() => ctx.setEditing(null)} disabled={busy}>Cancel</Button>
          <Button variant="primary" onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save'}</Button>
        </>
      ) : null}
    >
      {!editing ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <div style={{ fontWeight: 600 }}>
            {c.anonymised_at ? 'Anonymised contact' : c.name || '(no name)'}
          </div>
          {c.company && <div style={line}>{c.company}</div>}
          {(c.emails ?? []).map((e) => (
            <a key={e.email} href={`mailto:${e.email}`} style={{ ...line, color: 'var(--accent)', display: 'inline-flex', alignItems: 'center', gap: 6, overflowWrap: 'anywhere' }}>
              <Icon icon={Mail} size={14} />{e.email}{e.is_primary && (c.emails ?? []).length > 1 ? ' (main)' : ''}
            </a>
          ))}
          {c.phone && (
            <a href={`tel:${c.phone}`} style={{ ...line, color: 'var(--accent)', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <Icon icon={Phone} size={14} />{c.phone}
            </a>
          )}
          <div style={{ marginTop: 4 }}>
            <Button variant="link" style={{ fontSize: 'var(--fs-sm)' }} onClick={() => contactLink(c.id)}>Contact history</Button>
          </div>
          {ctx.detail.invoice_contact && ctx.detail.invoice_contact.id !== c.id && (
            <div style={{ ...line, marginTop: 6 }}>
              Invoice to: {[ctx.detail.invoice_contact.name, ctx.detail.invoice_contact.company, ctx.detail.invoice_contact.email].filter(Boolean).join(' · ')}
            </div>
          )}
        </div>
      ) : (
        <div style={{ display: 'grid', gap: 10 }}>
          <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>This corrects the contact itself, so their other enquiries and bookings show it too.</div>
          <Field label="Name" htmlFor={ids.name}><input id={ids.name} className="n-input" style={inputStyle} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></Field>
          <Field label="Phone" htmlFor={ids.phone}><input id={ids.phone} className="n-input" style={inputStyle} value={draft.phone} onChange={(e) => setDraft({ ...draft, phone: e.target.value })} /></Field>
          <Field label="Company" htmlFor={ids.company}><input id={ids.company} className="n-input" style={inputStyle} value={draft.company} onChange={(e) => setDraft({ ...draft, company: e.target.value })} /></Field>
        </div>
      )}
      <WriteError message={error ? errorText(error) : null} stale={isStale(error)} onReload={() => { setError(null); ctx.reload(); }} />
    </SectionCard>
  );
}

/** Where the client portal stands: never sent, or sent (when, last opened). */
/** One line of the access list: who they are, where they stand, and the one
 *  action that applies (remove, or let them back in). */
function PersonRow({ p, ctx, busy, onAllow, onRemove }: {
  p: PortalPerson;
  ctx: SectionCtx;
  busy: boolean;
  onAllow: () => void;
  onRemove: () => void;
}) {
  const state = p.locked_for_wrong_codes
    ? 'Locked out after too many wrong codes'
    : !p.allowed || p.removed_at
      ? `Removed${p.removed_at ? ` ${instantLabel(p.removed_at, ctx.timeZone)}` : ''}`
      : p.verified_at
        ? `Signed in ${instantLabel(p.verified_at, ctx.timeZone)}`
        : 'Hasn’t signed in yet';
  const blocked = p.locked_for_wrong_codes || !p.allowed || !!p.removed_at;
  return (
    <li style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0', borderBottom: '1px solid var(--line-soft)' }}>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ color: blocked ? 'var(--muted)' : 'var(--text)', overflowWrap: 'anywhere' }}>{p.email}</div>
        <div style={{ fontSize: 'var(--fs-xs)', color: p.locked_for_wrong_codes ? 'var(--warn)' : 'var(--muted)' }}>
          {state}{p.invited_by_email ? ` · invited by ${p.invited_by_email}` : ''}
        </div>
      </div>
      {blocked ? (
        <Button size="sm" variant="quiet" icon={UserPlus} onClick={onAllow} disabled={!ctx.canWrite || busy} title={ctx.lockReason ?? 'Let them request a code again'}>
          Allow again
        </Button>
      ) : (
        <Button size="sm" variant="quiet" icon={UserMinus} onClick={onRemove} disabled={!ctx.canWrite || busy} title={ctx.lockReason ?? 'They can’t get a code any more and are signed out'}>
          Remove
        </Button>
      )}
    </li>
  );
}

/**
 * Who may open the client portal (each signs in with a one-time code to
 * their address): remove someone (signs them out now), let a removed or
 * locked-out address back in, add someone. Nothing here sends an email.
 */
function PortalAccess({ ctx }: { ctx: SectionCtx }) {
  const addId = useId();
  const id = ctx.detail.event.id;
  const stamp = `${id}:${ctx.detail.portal_links.length}:${ctx.detail.changes.length}`;
  const access = useLoad(`portal-access:${stamp}`, () => fnGet<{ people: PortalPerson[] }>(`/events/${id}/portal-access`));
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [removing, setRemoving] = useState<string | null>(null);

  const act = async (call: () => Promise<{ people: PortalPerson[] }>) => {
    setBusy(true);
    setError(null);
    try {
      access.setData(await call());
      return true;
    } catch (e) {
      setError(e);
      return false;
    } finally {
      setBusy(false);
    }
  };
  const allow = (address: string) => act(() => fnSend<{ people: PortalPerson[] }>('POST', `/events/${id}/portal-access`, { email: address }));
  const add = async () => {
    const [address] = splitEmails(email);
    if (!address) return;
    if (await allow(address)) setEmail('');
  };
  const people = access.data?.people ?? [];

  return (
    <div style={{ marginTop: 6 }}>
      <div style={{ fontSize: 'var(--fs-xs)', fontWeight: 500, color: 'var(--muted)' }}>Who can open it</div>
      {access.loading && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>Loading…</div>}
      {access.error !== undefined && !access.data && <WriteError message={errorText(access.error)} />}
      {access.data && (people.length ? (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {people.map((p) => (
            <PersonRow key={p.email} p={p} ctx={ctx} busy={busy} onAllow={() => void allow(p.email)} onRemove={() => setRemoving(p.email)} />
          ))}
        </ul>
      ) : (
        <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>Nobody yet — sending or copying the link lets the client in.</div>
      ))}
      <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
        <input id={addId} aria-label="Add someone by email" className="n-input" type="email" placeholder="Add someone by email"
          value={email} onChange={(e) => setEmail(e.target.value)} disabled={!ctx.canWrite || busy}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void add(); } }}
          style={{ flex: 1, minWidth: 0, height: 30 }} />
        <Button size="sm" icon={Plus} onClick={() => void add()} disabled={!ctx.canWrite || busy || !email.trim()} title={ctx.lockReason ?? 'They can sign in with a code to this address — nothing is emailed'}>
          Add
        </Button>
      </div>
      <WriteError message={error ? errorText(error) : null} stale={isStale(error)} onReload={() => { setError(null); access.reload(); }} />
      {removing && (
        <ConfirmDialog
          title={`Remove ${removing}?`}
          confirmLabel="Remove"
          busyLabel="Removing…"
          danger
          onConfirm={async () => {
            // Errors show inside the dialog (it stays open to retry).
            access.setData(await fnSend<{ people: PortalPerson[] }>('DELETE', `/events/${id}/portal-access/${encodeURIComponent(removing)}`));
          }}
          onClose={() => setRemoving(null)}
        >
          They’re signed out of the client portal now and can’t get a sign-in code any more. You can allow them again later.
        </ConfirmDialog>
      )}
    </div>
  );
}

/** Where the client portal stands: never sent, or sent (when, last opened),
 *  who can open it, and revoking the link. */
export function PortalCard({ ctx, onSend, onCopyLink }: { ctx: SectionCtx; onSend: () => void; onCopyLink: () => void }) {
  const d = ctx.detail;
  const link = currentPortalLink(d.portal_links);
  const sentRows = d.changes.filter((c) => c.kind === 'link_sent');
  const lastSent = sentRows.length ? sentRows[sentRows.length - 1] : null;
  const sent = linkSent(d);
  const tz = ctx.timeZone;
  const [revoking, setRevoking] = useState(false);
  return (
    <SectionCard title="Client portal" badge={sent ? <Badge tone="info">Sent</Badge> : <Badge>Not sent</Badge>}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
        {!sent && <div>The client hasn’t been sent their event link yet. Send it so they can check the details and confirm each section.</div>}
        {lastSent && <div>Link sent {instantLabel(lastSent.occurred_at, tz)}{lastSent.actor ? ` by ${lastSent.actor}` : ''}.</div>}
        {link && (
          <>
            <div>{link.last_used_at ? `The client last opened it ${relativeLabel(link.last_used_at)}.` : 'The client hasn’t opened it yet.'}</div>
            {link.expires_at && <div style={{ color: 'var(--muted)' }}>Works until {instantLabel(link.expires_at, tz, true)}.</div>}
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              <CopyButton text={link.url} />
              <Button size="sm" variant="quiet" icon={ExternalLink} onClick={() => window.open(link.url, '_blank', 'noopener')}>Open</Button>
            </div>
          </>
        )}
        {sent && !link && <div style={{ color: 'var(--muted)' }}>No working Norm link yet (it was sent from Orbit, or revoked) — send or copy it to give the client one.</div>}
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <Button size="sm" onClick={onSend} disabled={!ctx.canWrite} title={ctx.lockReason ?? undefined}>{sent ? 'Send again' : 'Send to client'}</Button>
          {!link && (
            <Button size="sm" variant="quiet" onClick={onCopyLink} disabled={!ctx.canWrite} title={ctx.lockReason ?? 'Paste it into your own email'}>
              Copy client link
            </Button>
          )}
        </div>
        <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>No connected mailbox? Copy the link and email it yourself.</div>
        <PortalAccess ctx={ctx} />
        {link && (
          <div>
            <Button size="sm" variant="danger" onClick={() => setRevoking(true)} disabled={!ctx.canWrite} title={ctx.lockReason ?? 'Stop the current link working'}>
              Revoke link
            </Button>
          </div>
        )}
        {!ctx.canWrite && ctx.lockReason && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{ctx.lockReason}</div>}
      </div>
      {revoking && (
        <ConfirmDialog
          title="Revoke the client’s link?"
          confirmLabel="Revoke link"
          busyLabel="Revoking…"
          danger
          onConfirm={async () => {
            await fnSend('POST', `/events/${d.event.id}/portal-link/revoke`);
            const fresh = await fnGet<EventDetail>(`/events/${d.event.id}`);
            ctx.apply(fresh);
          }}
          onClose={() => setRevoking(false)}
        >
          The link the client has stops working straight away, for everyone, and anyone signed in is signed out. The people on
          the list stay allowed: the next time you send or copy the link, Norm issues a new one.
        </ConfirmDialog>
      )}
    </SectionCard>
  );
}

export function LinksCard({ ctx }: { ctx: SectionCtx }) {
  const d = ctx.detail;
  if (!d.enquiry && !d.threads.length) return null;
  return (
    <SectionCard title="Related">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: 'var(--fs-sm)' }}>
        {d.enquiry && (
          <div>
            <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-xs)' }}>Enquiry</div>
            <Button variant="link" style={{ fontSize: 'var(--fs-sm)', textAlign: 'left', whiteSpace: 'normal' }} onClick={() => enquiryLink(d.enquiry!.id)}>
              {d.enquiry.subject || 'Open the enquiry'}
            </Button>
          </div>
        )}
        {d.threads.length > 0 && (
          <div>
            <div style={{ color: 'var(--muted)', fontSize: 'var(--fs-xs)', marginBottom: 2 }}>Emails</div>
            <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
              {d.threads.map((t) => (
                <li key={t.link_id}>
                  <Button variant="link" style={{ fontSize: 'var(--fs-sm)', textAlign: 'left', whiteSpace: 'normal' }} onClick={() => threadLink(t.account_id, t.gmail_thread_id)}>
                    {t.subject || '(no subject)'}
                  </Button>
                  <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
                    {[t.last_from_name || t.last_from_email, t.last_message_at ? instantLabel(t.last_message_at, ctx.timeZone) : null, t.message_count ? `${t.message_count} messages` : null].filter(Boolean).join(' · ')}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </SectionCard>
  );
}

function ChangeRow({ ch, ctx }: { ch: EventChange; ctx: SectionCtx }) {
  const who = ch.actor_kind === 'client' ? `Client${ch.actor ? ` (${ch.actor})` : ''}` : ch.actor || (ch.actor_kind === 'import' ? 'Orbit' : 'Norm');
  const what = ch.kind === 'edited' ? `Edited${ch.section ? ` ${ch.section}` : ''}` : ch.kind === 'link_sent' ? 'Portal link sent' : ch.kind.charAt(0).toUpperCase() + ch.kind.slice(1);
  const entries = Object.entries(ch.changes ?? {});
  return (
    <li style={{ padding: '8px 0', borderBottom: '1px solid var(--line-soft)' }}>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', fontSize: 'var(--fs-sm)' }}>
        <span style={{ fontWeight: 600 }}>{what}</span>
        {ch.after_confirmation && <Badge tone="warn">After confirmation</Badge>}
        <span style={{ color: 'var(--muted)' }}>{who} · {instantLabel(ch.occurred_at, ctx.timeZone)}</span>
      </div>
      {entries.length > 0 && (
        <ul style={{ margin: '4px 0 0', paddingLeft: 16, fontSize: 'var(--fs-xs)', color: 'var(--text-soft)' }}>
          {entries.map(([k, v]) => {
            const [before, after] = Array.isArray(v) ? v : [null, v];
            return (
              <li key={k}>
                {changeFieldLabel(k)}: {changeValueText(k, before, ctx.currency)} → {changeValueText(k, after, ctx.currency)}
              </li>
            );
          })}
        </ul>
      )}
      {ch.note && <div style={{ marginTop: 4, fontSize: 'var(--fs-xs)', color: 'var(--text-soft)', fontStyle: 'italic' }}>“{ch.note}”</div>}
    </li>
  );
}

export function ActivityCard({ ctx }: { ctx: SectionCtx }) {
  const d = ctx.detail;
  const [tab, setTab] = useState<'timeline' | 'changes'>('timeline');
  // Refetch the timeline whenever the event changes (its version or log length).
  const stamp = `${d.event.id}:${d.event.version}:${d.changes.length}:${Object.values(d.sections).map((s) => s.occurred_at ?? '').join('|')}`;
  const timeline = useLoad(tab === 'timeline' ? `timeline:${stamp}` : null, () => fnGet<{ items: TimelineItem[] }>(`/events/${d.event.id}/timeline`));
  const changes = newestFirst(d.changes);

  return (
    <SectionCard title="Activity">
      <Tabs label="Activity" items={[{ id: 'timeline', label: 'Timeline' }, { id: 'changes', label: `Changes (${changes.length})` }]} value={tab} onChange={(id) => setTab(id as 'timeline' | 'changes')} />
      {tab === 'timeline' ? (
        timeline.error !== undefined ? <WriteError message={errorText(timeline.error)} /> :
          !timeline.data ? <div style={{ padding: '12px 0', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>Loading…</div> : (
            <ol style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              {timeline.data.items.map((it, i) => (
                <li key={`${it.at}-${i}`} style={{ padding: '8px 0', borderBottom: '1px solid var(--line-soft)' }}>
                  <div style={{ fontSize: 'var(--fs-sm)', fontWeight: 500 }}>{it.title}</div>
                  {it.detail && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-soft)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{humaniseDetail(it.detail)}</div>}
                  <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{[it.actor, instantLabel(it.at, ctx.timeZone, true)].filter(Boolean).join(' · ')}</div>
                </li>
              ))}
              {!timeline.data.items.length && <li style={{ padding: '12px 0', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>Nothing yet.</li>}
            </ol>
          )
      ) : (
        <ol style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {changes.map((ch) => <ChangeRow key={ch.id} ch={ch} ctx={ctx} />)}
          {!changes.length && <li style={{ padding: '12px 0', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>No changes logged.</li>}
        </ol>
      )}
    </SectionCard>
  );
}
