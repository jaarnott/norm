'use client';

/**
 * Organisations — platform admins only (Oct 2026).
 *
 * Every organisation with its venues and people. Admins create and delete
 * organisations, add and remove venues, and add, remove or change people.
 * Creating an organisation or a venue here never makes the admin a member:
 * they manage its shape, they don't get into its data.
 */

import { useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, Plus, Trash2 } from 'lucide-react';
import { apiFetch } from '../../lib/api';
import AddressSearch from './AddressSearch';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import { ConfirmDialog } from '../ui/Dialog';
import Icon from '../ui/Icon';
import IconButton from '../ui/IconButton';
import PageState from '../ui/PageState';

interface OrgVenue { id: string; name: string; location: string | null; timezone: string | null }
interface Person {
  user_id: string; email: string; full_name: string | null; is_active: boolean; platform_admin: boolean;
  role_id?: string | null; role?: string | null; venue_ids?: string[];
}
interface Org {
  id: string; name: string; slug: string; currency: string; created_at: string | null;
  venues: OrgVenue[]; members: Person[];
}
interface RoleOption { id: string; name: string; display_name: string }
interface Overview { organizations: Org[]; unassigned_users: Person[]; roles: RoleOption[] }

type Pending =
  | { kind: 'org'; org: Org }
  | { kind: 'venue'; org: Org; venue: OrgVenue }
  | { kind: 'member'; org: Org; person: Person };

/** Throws the server's own reason, so a confirm dialog can show it. */
async function must(call: Promise<Response>, failed: string): Promise<Response> {
  let res: Response;
  try { res = await call; } catch { throw new Error(`${failed} — check your connection.`); }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(typeof body?.detail === 'string' ? body.detail : `${failed} (error ${res.status})`);
  }
  return res;
}

const box: React.CSSProperties = { border: '1px solid var(--line)', borderRadius: 'var(--radius)', backgroundColor: 'var(--bg)' };
const row: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' };
const small: React.CSSProperties = { fontSize: 'var(--fs-sm)', color: 'var(--muted)' };
const addressInput: React.CSSProperties = {
  width: '100%', minHeight: 34, padding: '6px 10px', boxSizing: 'border-box', border: '1px solid var(--field)',
  borderRadius: 'var(--radius)', backgroundColor: 'var(--bg)', color: 'var(--text)', fontFamily: 'inherit', fontSize: 'var(--fs-base)',
};

export default function OrganisationsPanel() {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [pending, setPending] = useState<Pending | null>(null);
  const [newOrg, setNewOrg] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await must(apiFetch('/api/admin/organizations'), 'Couldn’t load organisations');
      setData(await res.json());
      setError(null);
    } catch (e) { setError((e as Error).message); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const run = async (call: Promise<Response>, failed: string, done?: string) => {
    setBusy(true); setNotice(null);
    try { await must(call, failed); if (done) setNotice(done); await load(); }
    catch (e) { setNotice((e as Error).message); }
    finally { setBusy(false); }
  };

  const createOrg = async () => {
    if (!newOrg.trim()) return;
    await run(
      apiFetch('/api/admin/organizations', { method: 'POST', body: JSON.stringify({ name: newOrg.trim() }) }),
      'The organisation wasn’t created', `Created ${newOrg.trim()}. Add its venues and invite its owner below.`,
    );
    setNewOrg('');
  };

  const confirmPending = async () => {
    if (!pending) return;
    if (pending.kind === 'org') {
      await must(apiFetch(`/api/admin/organizations/${pending.org.id}`, { method: 'DELETE' }), 'Not deleted');
    } else if (pending.kind === 'venue') {
      await must(apiFetch(`/api/venues/${pending.venue.id}`, { method: 'DELETE' }), 'Not deleted');
    } else {
      await must(apiFetch(`/api/organizations/${pending.org.id}/members/${pending.person.user_id}`, { method: 'DELETE' }), 'Not removed');
    }
    await load();
  };

  if (error && !data) return <PageState kind="error" title="Couldn’t load organisations" detail={error} />;
  if (!data) return <PageState kind="loading" title="Loading organisations…" />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <h2 style={{ margin: '0 0 4px', fontSize: 'var(--fs-lg)', fontWeight: 600 }}>Organisations</h2>
        <div style={small}>
          Every organisation on Norm, with its venues and people. Only platform admins see this page.
          Creating an organisation or venue here doesn’t make you a member of it.
        </div>
      </div>

      <div style={{ ...box, padding: 12 }}>
        <div style={row}>
          <input
            className="n-input" style={{ flex: '1 1 240px' }} placeholder="New organisation name"
            value={newOrg} onChange={e => setNewOrg(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') createOrg(); }}
            data-testid="new-org-name"
          />
          <Button variant="primary" icon={Plus} onClick={createOrg} disabled={busy || !newOrg.trim()}>Create organisation</Button>
        </div>
      </div>

      {notice && <div role="status" style={{ ...box, padding: '8px 12px', fontSize: 'var(--fs-sm)' }}>{notice}</div>}

      {data.organizations.map(org => (
        <OrgCard
          key={org.id}
          org={org}
          roles={data.roles}
          open={!!open[org.id]}
          onToggle={() => setOpen(o => ({ ...o, [org.id]: !o[org.id] }))}
          busy={busy}
          run={run}
          onAsk={setPending}
        />
      ))}

      {data.unassigned_users.length > 0 && (
        <Unassigned people={data.unassigned_users} orgs={data.organizations} roles={data.roles} busy={busy} run={run} />
      )}

      {pending && (
        <ConfirmDialog
          title={pending.kind === 'org' ? `Delete ${pending.org.name}?` : pending.kind === 'venue' ? `Delete ${pending.venue.name}?` : `Remove ${pending.person.email} from ${pending.org.name}?`}
          confirmLabel={pending.kind === 'member' ? 'Remove' : 'Delete'}
          busyLabel={pending.kind === 'member' ? 'Removing…' : 'Deleting…'}
          danger
          onConfirm={confirmPending}
          onClose={() => setPending(null)}
        >
          <p style={{ margin: 0 }}>
            {pending.kind === 'org' && 'Its people lose access and keep their accounts. An organisation with venues or billing history can’t be deleted; Norm will say why.'}
            {pending.kind === 'venue' && 'Everyone loses access to this venue and its connections are removed. Its history is kept without the venue.'}
            {pending.kind === 'member' && 'They lose access to this organisation’s venues straight away. Their account and their other organisations are untouched.'}
          </p>
        </ConfirmDialog>
      )}
    </div>
  );
}

function OrgCard({ org, roles, open, onToggle, busy, run, onAsk }: {
  org: Org; roles: RoleOption[]; open: boolean; onToggle: () => void; busy: boolean;
  run: (call: Promise<Response>, failed: string, done?: string) => Promise<void>;
  onAsk: (p: Pending) => void;
}) {
  const [name, setName] = useState(org.name);
  const [venueName, setVenueName] = useState('');
  const [venueAddress, setVenueAddress] = useState('');
  const [venueTz, setVenueTz] = useState('');
  const [email, setEmail] = useState('');
  const defaultRole = roles.find(r => r.name === 'team_member')?.id || roles[0]?.id || '';
  const [roleId, setRoleId] = useState(defaultRole);
  const [inviteVenues, setInviteVenues] = useState<string[]>([]);

  const saveName = () => {
    if (name.trim() && name.trim() !== org.name) {
      run(apiFetch(`/api/organizations/${org.id}`, { method: 'PUT', body: JSON.stringify({ name: name.trim() }) }), 'Not renamed', `Renamed to ${name.trim()}.`);
    }
  };
  const addVenue = async () => {
    if (!venueName.trim()) return;
    await run(
      apiFetch(`/api/organizations/${org.id}/venues`, {
        method: 'POST',
        body: JSON.stringify({ name: venueName.trim(), location: venueAddress || null, timezone: venueTz || null }),
      }),
      'The venue wasn’t added', `Added ${venueName.trim()} to ${org.name}.`,
    );
    setVenueName(''); setVenueAddress(''); setVenueTz('');
  };
  const invite = async () => {
    if (!email.trim()) return;
    await run(
      apiFetch('/api/auth/invite', {
        method: 'POST',
        body: JSON.stringify({ email: email.trim(), org_id: org.id, role_id: roleId, venue_ids: inviteVenues }),
      }),
      'Not added', `${email.trim()} was added to ${org.name} and emailed.`,
    );
    setEmail(''); setInviteVenues([]);
  };
  const setMember = (person: Person, body: { role_id?: string; venue_ids?: string[] }) =>
    run(apiFetch(`/api/admin/organizations/${org.id}/members/${person.user_id}`, { method: 'PUT', body: JSON.stringify(body) }), 'Not changed');

  return (
    <div style={box} data-testid={`org-${org.slug}`}>
      <div style={{ ...row, padding: 12, cursor: 'pointer' }} onClick={onToggle}>
        <Icon icon={open ? ChevronDown : ChevronRight} size={16} />
        <span style={{ fontWeight: 600, flex: 1, minWidth: 0 }}>{org.name}</span>
        <span style={small}>{org.venues.length} {org.venues.length === 1 ? 'venue' : 'venues'} · {org.members.length} {org.members.length === 1 ? 'person' : 'people'} · {org.currency}</span>
      </div>
      {open && (
        <div style={{ padding: '0 12px 12px', display: 'flex', flexDirection: 'column', gap: 16, borderTop: '1px solid var(--line)' }}>
          <div style={{ ...row, marginTop: 12 }}>
            <input className="n-input" style={{ flex: '1 1 240px' }} value={name} onChange={e => setName(e.target.value)} aria-label="Organisation name" />
            <Button onClick={saveName} disabled={busy || !name.trim() || name.trim() === org.name}>Rename</Button>
            <Button variant="danger" icon={Trash2} onClick={() => onAsk({ kind: 'org', org })} disabled={busy}>Delete organisation</Button>
          </div>

          <section>
            <h3 style={{ margin: '0 0 8px', fontSize: 'var(--fs-base)', fontWeight: 600 }}>Venues</h3>
            {org.venues.length === 0 && <div style={small}>No venues yet.</div>}
            {org.venues.map(v => (
              <div key={v.id} style={{ ...row, padding: '6px 0', borderBottom: '1px solid var(--line)' }}>
                <span style={{ flex: 1, minWidth: 0 }}>{v.name}</span>
                <span style={small}>{v.timezone || 'no timezone'}</span>
                <IconButton icon={Trash2} label={`Delete ${v.name}`} onClick={() => onAsk({ kind: 'venue', org, venue: v })} />
              </div>
            ))}
            <div style={{ ...row, marginTop: 8 }}>
              <input className="n-input" style={{ flex: '1 1 160px' }} placeholder="Venue name" value={venueName} onChange={e => setVenueName(e.target.value)} />
              <div style={{ flex: '2 1 220px', minWidth: 0 }}>
                <AddressSearch value={venueAddress} onChange={setVenueAddress}
                  onSelect={sel => { setVenueAddress(sel.address); if (sel.timezone) setVenueTz(sel.timezone); }}
                  placeholder="Address (sets the timezone)" inputStyle={addressInput} />
              </div>
              <input className="n-input" style={{ flex: '1 1 140px' }} placeholder="Timezone" value={venueTz} onChange={e => setVenueTz(e.target.value)} />
              <Button icon={Plus} onClick={addVenue} disabled={busy || !venueName.trim()}>Add venue</Button>
            </div>
          </section>

          <section>
            <h3 style={{ margin: '0 0 8px', fontSize: 'var(--fs-base)', fontWeight: 600 }}>People</h3>
            {org.members.length === 0 && <div style={small}>Nobody yet. Invite the owner below.</div>}
            {org.members.map(p => (
              <div key={p.user_id} style={{ padding: '8px 0', borderBottom: '1px solid var(--line)', display: 'flex', flexDirection: 'column', gap: 6 }}>
                <div style={row}>
                  <span style={{ flex: 1, minWidth: 0 }}>
                    {p.email}{p.full_name ? <span style={small}> · {p.full_name}</span> : null}
                  </span>
                  {!p.is_active && <Badge tone="warn">Invited</Badge>}
                  {p.platform_admin && <Badge tone="neutral">Platform admin</Badge>}
                  <select className="n-input" value={p.role_id || ''} aria-label={`Role for ${p.email}`}
                    onChange={e => setMember(p, { role_id: e.target.value })} disabled={busy}>
                    {!p.role_id && <option value="">No role</option>}
                    {roles.map(r => <option key={r.id} value={r.id}>{r.display_name}</option>)}
                  </select>
                  <IconButton icon={Trash2} label={`Remove ${p.email}`} onClick={() => onAsk({ kind: 'member', org, person: p })} />
                </div>
                {org.venues.length > 0 && (
                  <div style={{ ...row, ...small }}>
                    Venues:
                    {org.venues.map(v => {
                      const has = (p.venue_ids || []).includes(v.id);
                      return (
                        <label key={v.id} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                          <input type="checkbox" checked={has} disabled={busy}
                            onChange={() => setMember(p, { venue_ids: has ? (p.venue_ids || []).filter(x => x !== v.id) : [...(p.venue_ids || []), v.id] })} />
                          {v.name}
                        </label>
                      );
                    })}
                  </div>
                )}
              </div>
            ))}
            <div style={{ ...row, marginTop: 8 }}>
              <input className="n-input" style={{ flex: '2 1 220px' }} type="email" placeholder="Email to add" value={email} onChange={e => setEmail(e.target.value)} />
              <select className="n-input" value={roleId} onChange={e => setRoleId(e.target.value)} aria-label="Role">
                {roles.map(r => <option key={r.id} value={r.id}>{r.display_name}</option>)}
              </select>
              <Button icon={Plus} onClick={invite} disabled={busy || !email.trim()}>Add person</Button>
            </div>
            {org.venues.length > 0 && (
              <div style={{ ...row, ...small, marginTop: 6 }}>
                With venues:
                {org.venues.map(v => (
                  <label key={v.id} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                    <input type="checkbox" checked={inviteVenues.includes(v.id)}
                      onChange={() => setInviteVenues(s => s.includes(v.id) ? s.filter(x => x !== v.id) : [...s, v.id])} />
                    {v.name}
                  </label>
                ))}
              </div>
            )}
            <div style={{ ...small, marginTop: 6 }}>
              Someone new gets an invitation to set a password. Someone with an account already is told they were added.
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

function Unassigned({ people, orgs, roles, busy, run }: {
  people: Person[]; orgs: Org[]; roles: RoleOption[]; busy: boolean;
  run: (call: Promise<Response>, failed: string, done?: string) => Promise<void>;
}) {
  const [orgFor, setOrgFor] = useState<Record<string, string>>({});
  const roleId = roles.find(r => r.name === 'team_member')?.id || roles[0]?.id || '';
  return (
    <div style={{ ...box, padding: 12 }}>
      <h3 style={{ margin: '0 0 4px', fontSize: 'var(--fs-base)', fontWeight: 600 }}>Accounts in no organisation</h3>
      <div style={{ ...small, marginBottom: 8 }}>
        Old accounts or sign-ups that never finished. They can’t see any venue until they belong to an organisation.
      </div>
      {people.map(p => (
        <div key={p.user_id} style={{ ...row, padding: '6px 0', borderBottom: '1px solid var(--line)' }}>
          <span style={{ flex: 1, minWidth: 0 }}>{p.email}{p.full_name ? <span style={small}> · {p.full_name}</span> : null}</span>
          {p.platform_admin && <Badge tone="neutral">Platform admin</Badge>}
          <select className="n-input" value={orgFor[p.user_id] || ''} onChange={e => setOrgFor(s => ({ ...s, [p.user_id]: e.target.value }))} aria-label={`Organisation for ${p.email}`}>
            <option value="">Add to…</option>
            {orgs.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
          <Button disabled={busy || !orgFor[p.user_id]}
            onClick={() => run(apiFetch('/api/auth/invite', { method: 'POST', body: JSON.stringify({ email: p.email, org_id: orgFor[p.user_id], role_id: roleId, venue_ids: [] }) }), 'Not added', `${p.email} added as a team member. Set their venues on the organisation.`)}>
            Add
          </Button>
        </div>
      ))}
    </div>
  );
}
