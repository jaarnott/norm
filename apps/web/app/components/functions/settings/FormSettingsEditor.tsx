'use client';

/**
 * A web form's settings: name and address, whether it takes submissions,
 * where submissions are emailed and with what subject, the reply-to field,
 * the booking type it implies, the intro text, button label and thank-you
 * message. Only changed settings are sent (PUT /forms/{id}).
 */
import { useId, useState } from 'react';
import { fnSend } from '../shared/api';
import type { FnMeta } from '../shared/meta';
import { errorText } from './errors';
import type { WebForm } from './types';
import { CheckboxRow, Field, FieldGrid, SaveRow, fullWidth, help, mono } from './ui';

interface Draft {
  name: string;
  slug: string;
  active: boolean;
  submit_label: string;
  success_message: string;
  recipient_email: string;
  subject_template: string;
  reply_to_field_key: string;
  booking_type: string;
  honeypot_field: string;
  intro_html: string;
}

function draftOf(f: WebForm): Draft {
  return {
    name: f.name,
    slug: f.slug,
    active: f.active,
    submit_label: f.submit_label ?? '',
    success_message: f.success_message ?? '',
    recipient_email: f.recipient_email ?? '',
    subject_template: f.subject_template ?? '',
    reply_to_field_key: f.reply_to_field_key ?? '',
    booking_type: f.booking_type ?? '',
    honeypot_field: f.honeypot_field ?? '',
    intro_html: f.intro_html ?? '',
  };
}

/** The settings as sent: blank text clears (null), except where the API
 *  needs a value. */
function body(d: Draft): Record<string, unknown> {
  return {
    name: d.name.trim(),
    slug: d.slug.trim().toLowerCase(),
    active: d.active,
    submit_label: d.submit_label.trim() || null,
    success_message: d.success_message.trim() || null,
    recipient_email: d.recipient_email.trim() || null,
    subject_template: d.subject_template.trim() || null,
    reply_to_field_key: d.reply_to_field_key || null,
    booking_type: d.booking_type || null,
    honeypot_field: d.honeypot_field.trim() || null,
    intro_html: d.intro_html.trim() || null,
  };
}

export default function FormSettingsEditor({ form, meta, canWrite, onSaved }: {
  form: WebForm;
  meta: FnMeta;
  canWrite: boolean;
  onSaved: (f: WebForm) => void;
}) {
  const id = useId();
  const [draft, setDraft] = useState<Draft>(() => draftOf(form));
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const now = body(draft);
  const base = body(draftOf(form));
  const changed = Object.fromEntries(Object.entries(now).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(base[k])));
  const dirty = Object.keys(changed).length > 0;
  const emailFields = (form.fields ?? []).filter((f) => f.type === 'email');
  const slugChanged = draft.slug.trim().toLowerCase() !== form.slug;
  const formAddress = form.embed.url.replace(/\/f\/[^/]*$/, '/f/');

  const edit = (p: Partial<Draft>) => {
    setDraft((d) => ({ ...d, ...p }));
    setSaved(false);
    setError(null);
  };

  const save = async () => {
    if (!now.name) {
      setError('Give the form a name.');
      return;
    }
    if (!/^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/.test(String(now.slug))) {
      setError('The address must be lowercase letters, numbers and dashes (up to 64).');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const next = await fnSend<WebForm>('PUT', `/forms/${form.id}`, { expected_version: form.version, ...changed });
      setDraft(draftOf(next));
      setSaved(true);
      onSaved(next);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="n-card" style={{ padding: 16 }}>
      <FieldGrid min={240}>
        <Field label="Form name" htmlFor={`${id}-name`}>
          <input id={`${id}-name`} className="n-input" style={fullWidth} value={draft.name} disabled={!canWrite}
            onChange={(e) => edit({ name: e.target.value })} />
        </Field>
        <Field label="Address" htmlFor={`${id}-slug`}
          hint={slugChanged ? 'Changing the address breaks embeds and links that use the old one.' : `${formAddress}${draft.slug}`}>
          <input id={`${id}-slug`} className="n-input" style={{ ...fullWidth, ...mono }} value={draft.slug} disabled={!canWrite}
            onChange={(e) => edit({ slug: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '-') })} />
        </Field>
      </FieldGrid>
      <div style={{ marginTop: 12 }}>
        <CheckboxRow checked={draft.active} onChange={(on) => edit({ active: on })} disabled={!canWrite}
          label="Taking submissions" description="Switched off, the form shows as unavailable wherever it’s embedded." />
      </div>

      <h3 style={{ margin: '20px 0 8px', fontSize: 'var(--fs-base)', fontWeight: 600 }}>Where submissions go</h3>
      <FieldGrid min={240}>
        <Field label="Email submissions to" htmlFor={`${id}-to`} hint="Blank sends them to the venue’s mailbox.">
          <input id={`${id}-to`} className="n-input" style={fullWidth} inputMode="email" value={draft.recipient_email} disabled={!canWrite}
            onChange={(e) => edit({ recipient_email: e.target.value })} placeholder="functions@venue.co.nz" />
        </Field>
        <Field label="Reply-to" htmlFor={`${id}-rt`} hint="Replying to the email goes to the address in this field.">
          <select id={`${id}-rt`} className="n-select" style={fullWidth} value={draft.reply_to_field_key} disabled={!canWrite}
            onChange={(e) => edit({ reply_to_field_key: e.target.value })}>
            <option value="">None</option>
            {emailFields.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
          </select>
        </Field>
      </FieldGrid>
      <div style={{ marginTop: 12 }}>
        <Field label="Email subject" htmlFor={`${id}-subj`}
          hint={<>Use a field’s key in braces, e.g. <code style={mono}>{'{{name}}'}</code>, plus <code style={mono}>{'{{form_name}}'}</code> and <code style={mono}>{'{{date}}'}</code>. A time stamp is added so each lead is its own email thread.</>}>
          <input id={`${id}-subj`} className="n-input" style={fullWidth} value={draft.subject_template} disabled={!canWrite}
            onChange={(e) => edit({ subject_template: e.target.value })} placeholder="Website enquiry — {{name}}" />
        </Field>
      </div>
      <div style={{ marginTop: 12, maxWidth: 360 }}>
        <Field label="Booking type" htmlFor={`${id}-bt`} hint="Set this when every submission is the same kind of booking.">
          <select id={`${id}-bt`} className="n-select" style={fullWidth} value={draft.booking_type} disabled={!canWrite}
            onChange={(e) => edit({ booking_type: e.target.value })}>
            <option value="">Not set (ask in the form)</option>
            {meta.booking_types.map((b) => <option key={b.value} value={b.value}>{b.label}</option>)}
          </select>
        </Field>
      </div>

      <h3 style={{ margin: '20px 0 8px', fontSize: 'var(--fs-base)', fontWeight: 600 }}>What the customer sees</h3>
      <Field label="Intro" htmlFor={`${id}-intro`} hint="Shown above the fields. Simple HTML is fine (links, bold) — it’s cleaned when saved.">
        <textarea id={`${id}-intro`} className="n-input" rows={3} style={fullWidth} value={draft.intro_html} disabled={!canWrite}
          onChange={(e) => edit({ intro_html: e.target.value })} />
      </Field>
      <div style={{ marginTop: 12 }}>
        <FieldGrid min={220}>
          <Field label="Button label" htmlFor={`${id}-btn`}>
            <input id={`${id}-btn`} className="n-input" style={fullWidth} value={draft.submit_label} disabled={!canWrite}
              onChange={(e) => edit({ submit_label: e.target.value })} placeholder="Send enquiry" maxLength={60} />
          </Field>
          <Field label="Thank-you message" htmlFor={`${id}-ok`}>
            <input id={`${id}-ok`} className="n-input" style={fullWidth} value={draft.success_message} disabled={!canWrite}
              onChange={(e) => edit({ success_message: e.target.value })} placeholder="Thanks — we’ll be in touch." />
          </Field>
        </FieldGrid>
      </div>

      <details style={{ marginTop: 16 }}>
        <summary style={{ cursor: 'pointer', fontSize: 'var(--fs-sm)', color: 'var(--accent)' }}>Spam protection</summary>
        <div style={{ marginTop: 10, maxWidth: 360 }}>
          <Field label="Hidden trap field name" htmlFor={`${id}-hp`}>
            <input id={`${id}-hp`} className="n-input" style={{ ...fullWidth, ...mono }} value={draft.honeypot_field} disabled={!canWrite}
              onChange={(e) => edit({ honeypot_field: e.target.value })} placeholder="website" />
          </Field>
          <p style={help}>A field people never see; bots fill it in. It mustn’t match one of the form’s keys.</p>
        </div>
      </details>

      <SaveRow canWrite={canWrite} dirty={dirty} saving={saving} saved={saved} error={error} onSave={save}
        onDiscard={() => { setDraft(draftOf(form)); setError(null); }} label="Save settings" />
    </div>
  );
}
