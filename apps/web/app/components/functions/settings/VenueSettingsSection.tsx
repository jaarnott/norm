'use client';

/**
 * A venue's function settings: who approves minimum spends (and who may
 * override), the policies Norm and the client portal show, menu links, and
 * who is emailed about new enquiries and customer answers. Changing an
 * approver needs an owner or a current approver (`can_govern_approvals`,
 * enforced by the API).
 */
import { useId, useState } from 'react';
import { Plus, Trash2, X } from 'lucide-react';
import Button from '../../ui/Button';
import IconButton from '../../ui/IconButton';
import { fnSend } from '../shared/api';
import type { FnMember } from '../shared/meta';
import { errorText } from './errors';
import { moveRow } from './menuTemplate';
import type { MenuLink, VenueProfile, VenueSettings } from './types';
import { CheckboxRow, Field, FieldGrid, MoveButtons, Notice, Panel, SaveRow, Section, fullWidth, help, muted } from './ui';

interface Draft {
  approver_user_id: string;
  override_approver_user_id: string;
  policies: string;
  menu_links: MenuLink[];
  notify_emails: string[];
}

function draftOf(s: VenueSettings | null): Draft {
  return {
    approver_user_id: s?.approver_user_id ?? '',
    override_approver_user_id: s?.override_approver_user_id ?? '',
    policies: s?.policies ?? '',
    menu_links: (s?.menu_links ?? []).map((l) => ({ ...l })).sort((a, b) => a.sort - b.sort),
    notify_emails: [...(s?.notify_emails ?? [])],
  };
}

/** The PUT body for what changed (nulls clear). */
function changes(d: Draft, s: VenueSettings | null): Record<string, unknown> {
  const base = draftOf(s);
  const out: Record<string, unknown> = {};
  if (d.approver_user_id !== base.approver_user_id) out.approver_user_id = d.approver_user_id || null;
  if (d.override_approver_user_id !== base.override_approver_user_id) out.override_approver_user_id = d.override_approver_user_id || null;
  if (d.policies.trim() !== base.policies.trim()) out.policies = d.policies.trim() || null;
  const links = d.menu_links.map((l, i) => ({ ...l, sort: i }));
  if (JSON.stringify(links) !== JSON.stringify(base.menu_links.map((l, i) => ({ ...l, sort: i })))) out.menu_links = links;
  if (JSON.stringify(d.notify_emails) !== JSON.stringify(base.notify_emails)) out.notify_emails = d.notify_emails;
  return out;
}

const KINDS: { value: string; label: string }[] = [
  { value: '', label: 'Link' },
  { value: 'food', label: 'Menu' },
  { value: 'drinks', label: 'Drinks list' },
  { value: 'functions_pack', label: 'Functions pack' },
];

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function VenueSettingsSection({ venueId, settings, members, canWrite, canGovern, onSaved }: {
  venueId: string;
  settings: VenueSettings | null;
  members: FnMember[];
  canWrite: boolean;
  canGovern: boolean;
  onSaved: (profile: VenueProfile) => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => draftOf(settings));
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [emailInput, setEmailInput] = useState('');
  const [emailError, setEmailError] = useState<string | null>(null);
  const id = useId();

  const body = changes(draft, settings);
  const dirty = Object.keys(body).length > 0;
  const linkProblem = draft.menu_links.find((l) => !l.label.trim() || !/^https?:\/\//i.test(l.url.trim()));

  const edit = (p: Partial<Draft>) => {
    setDraft((d) => ({ ...d, ...p }));
    setSaved(false);
    setError(null);
  };

  // People who can act at this venue; a current approver who has lost access
  // still shows, so the field never silently reads "nobody".
  const eligible = members.filter((m) => m.venue_ids.includes(venueId));
  const options = (currentId: string, currentName: string | null | undefined) => {
    const list = eligible.map((m) => ({ id: m.id, name: m.name || m.email }));
    if (currentId && !list.some((m) => m.id === currentId)) list.unshift({ id: currentId, name: `${currentName ?? 'Someone'} (no access to this venue)` });
    return list;
  };

  const setLink = (i: number, p: Partial<MenuLink>) =>
    edit({ menu_links: draft.menu_links.map((l, j) => (j === i ? { ...l, ...p } : l)) });

  const addEmails = () => {
    const parts = emailInput.split(/[\s,;]+/).map((s) => s.trim().toLowerCase()).filter(Boolean);
    if (!parts.length) return;
    const bad = parts.filter((p) => !EMAIL.test(p));
    if (bad.length) {
      setEmailError(`${bad.join(', ')} doesn’t look like an email address.`);
      return;
    }
    edit({ notify_emails: Array.from(new Set([...draft.notify_emails, ...parts])) });
    setEmailInput('');
    setEmailError(null);
  };

  const save = async () => {
    if (linkProblem) {
      setError('Each menu link needs a label and a web address starting with https://.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const profile = await fnSend<VenueProfile>('PUT', `/venues/${venueId}/settings`, {
        ...(settings ? { version: settings.version } : {}),
        ...body,
      });
      setDraft(draftOf(profile.settings));
      setSaved(true);
      onSaved(profile);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  const approverOpts = options(draft.approver_user_id, settings?.approver_name);
  const overrideOpts = options(draft.override_approver_user_id, settings?.override_approver_name);

  return (
    <Section title="Function settings" meta="Approvals, policies, menu links and who hears about new enquiries">
      <Panel>
        <FieldGrid min={240}>
          <Field label="Minimum-spend approver" htmlFor={`${id}-ap`}
            hint="Approves options as “OK to offer” in areas that need approval.">
            <select id={`${id}-ap`} className="n-select" style={fullWidth} value={draft.approver_user_id}
              disabled={!canWrite || !canGovern} onChange={(e) => edit({ approver_user_id: e.target.value })}>
              <option value="">No approver set</option>
              {approverOpts.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
          </Field>
          <Field label="Override approver" htmlFor={`${id}-ov`}
            hint="Can approve an option outside the rules, with a reason.">
            <select id={`${id}-ov`} className="n-select" style={fullWidth} value={draft.override_approver_user_id}
              disabled={!canWrite || !canGovern} onChange={(e) => edit({ override_approver_user_id: e.target.value })}>
              <option value="">No override approver</option>
              {overrideOpts.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
          </Field>
        </FieldGrid>
        {!canGovern && (
          <p style={help}>Only an owner of the organisation or this venue’s current approver can change who approves.</p>
        )}
        {canGovern && eligible.length === 0 && (
          <p style={help}>Nobody has access to this venue yet — give people access in Norm’s Settings → Users first.</p>
        )}

        <div style={{ marginTop: 16 }}>
          <Field label="Policies" htmlFor={`${id}-pol`}
            hint="Booking rules, deposits, extras and anything else staff and Norm should know. Customers see it on their event page.">
            <textarea id={`${id}-pol`} className="n-input" rows={10} style={{ ...fullWidth, minHeight: 160 }} value={draft.policies}
              disabled={!canWrite} onChange={(e) => edit({ policies: e.target.value })} />
          </Field>
        </div>

        <div style={{ marginTop: 16 }}>
          <span className="n-label">Menu links</span>
          <p style={{ ...help, marginTop: 0, marginBottom: 8 }}>Menus and function packs to share with customers (web addresses only).</p>
          {draft.menu_links.length === 0 && <p style={muted}>No links yet.</p>}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {draft.menu_links.map((l, i) => (
              <div key={i} style={{ border: '1px solid var(--line)', borderRadius: 'var(--radius)', padding: 10, background: 'var(--surface)' }}>
                <FieldGrid min={180}>
                  <Field label="Label" htmlFor={`${id}-l${i}-label`}>
                    <input id={`${id}-l${i}-label`} className="n-input" style={fullWidth} value={l.label} disabled={!canWrite}
                      onChange={(e) => setLink(i, { label: e.target.value })} placeholder="Dinner menu" />
                  </Field>
                  <Field label="Kind" htmlFor={`${id}-l${i}-kind`}>
                    <select id={`${id}-l${i}-kind`} className="n-select" style={fullWidth} value={l.kind ?? ''} disabled={!canWrite}
                      onChange={(e) => setLink(i, { kind: e.target.value || null })}>
                      {KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
                      {l.kind && !KINDS.some((k) => k.value === l.kind) && <option value={l.kind}>{l.kind}</option>}
                    </select>
                  </Field>
                </FieldGrid>
                <div style={{ marginTop: 10 }}>
                  <Field label="Web address" htmlFor={`${id}-l${i}-url`}>
                    <input id={`${id}-l${i}-url`} className="n-input" style={fullWidth} value={l.url} disabled={!canWrite} inputMode="url"
                      onChange={(e) => setLink(i, { url: e.target.value })} placeholder="https://…" />
                  </Field>
                </div>
                <div style={{ marginTop: 10 }}>
                  <Field label="Note for staff (optional)" htmlFor={`${id}-l${i}-desc`}>
                    <input id={`${id}-l${i}-desc`} className="n-input" style={fullWidth} value={l.description ?? ''} disabled={!canWrite}
                      onChange={(e) => setLink(i, { description: e.target.value || null })} placeholder="When to send it" />
                  </Field>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
                  <CheckboxRow checked={l.active} onChange={(on) => setLink(i, { active: on })} disabled={!canWrite} label="Show to customers" />
                  {canWrite && (
                    <span style={{ marginLeft: 'auto', display: 'inline-flex' }}>
                      <MoveButtons index={i} count={draft.menu_links.length} what={l.label || 'link'}
                        onMove={(d) => edit({ menu_links: moveRow(draft.menu_links, i, d) })} />
                      <IconButton icon={Trash2} iconSize={16} label={`Remove ${l.label || 'link'}`}
                        onClick={() => edit({ menu_links: draft.menu_links.filter((_, j) => j !== i) })} />
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
          {canWrite && (
            <Button size="sm" variant="quiet" icon={Plus} style={{ marginTop: 8 }}
              onClick={() => edit({ menu_links: [...draft.menu_links, { label: '', url: '', kind: 'food', description: null, active: true, sort: draft.menu_links.length }] })}>
              Add link
            </Button>
          )}
        </div>

        <div style={{ marginTop: 16 }}>
          <label className="n-label" htmlFor={`${id}-email`}>Notify by email</label>
          <p style={{ ...help, marginTop: 0, marginBottom: 8 }}>
            Told about new web enquiries, customer confirmations and edits after confirming. Blank = the venue’s mailbox.
          </p>
          {draft.notify_emails.length > 0 && (
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
              {draft.notify_emails.map((em) => (
                <span key={em} className="n-badge" style={{ fontSize: 'var(--fs-sm)', fontWeight: 500, padding: '2px 4px 2px 10px' }}>
                  {em}
                  {canWrite && (
                    <button type="button" aria-label={`Remove ${em}`} onClick={() => edit({ notify_emails: draft.notify_emails.filter((x) => x !== em) })}
                      style={{ display: 'inline-flex', border: 'none', background: 'none', padding: 2, cursor: 'pointer', color: 'var(--muted)' }}>
                      <X size={14} strokeWidth={1.75} aria-hidden />
                    </button>
                  )}
                </span>
              ))}
            </div>
          )}
          {canWrite && (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <input id={`${id}-email`} className="n-input" style={{ flex: '1 1 220px', minWidth: 0 }} value={emailInput} inputMode="email"
                placeholder="name@venue.co.nz" onChange={(e) => { setEmailInput(e.target.value); setEmailError(null); }}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addEmails(); } }} />
              <Button size="md" onClick={addEmails} disabled={!emailInput.trim()}>Add</Button>
            </div>
          )}
          {emailError && <Notice tone="error" style={{ marginTop: 8 }}>{emailError}</Notice>}
        </div>

        <SaveRow canWrite={canWrite} dirty={dirty} saving={saving} saved={saved} error={error} onSave={save}
          onDiscard={() => { setDraft(draftOf(settings)); setError(null); }} label="Save settings" />
      </Panel>
    </Section>
  );
}
