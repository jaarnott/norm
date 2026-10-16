'use client';

/**
 * Settings → Web forms: the venue's enquiry forms. Each opens into its
 * fields, settings, theme, embed snippets and submissions. Before Functions
 * goes live (shadow mode) the forms still work the way Orbit's did —
 * submissions are emailed to the venue; once live they also create
 * enquiries.
 */
import { useEffect, useId, useState } from 'react';
import { ExternalLink, Plus, Trash2 } from 'lucide-react';
import Badge from '../../ui/Badge';
import BackLink from '../../ui/BackLink';
import Button from '../../ui/Button';
import Dialog, { ConfirmDialog } from '../../ui/Dialog';
import PageState from '../../ui/PageState';
import Tabs from '../../ui/Tabs';
import { fnGet, fnSend } from '../shared/api';
import { relativeLabel } from '../shared/format';
import type { FnMeta } from '../shared/meta';
import { errorText } from './errors';
import FormFieldsEditor from './FormFieldsEditor';
import FormSettingsEditor from './FormSettingsEditor';
import FormSubmissions from './FormSubmissions';
import FormThemeEditor from './FormThemeEditor';
import type { WebForm } from './types';
import { CopyButton, Field, Notice, Panel, ReadOnlyNote, Section, fullWidth, help, mono, muted } from './ui';

type Sub = 'fields' | 'settings' | 'theme' | 'embed' | 'submissions';
const SUBS: { id: Sub; label: string }[] = [
  { id: 'fields', label: 'Fields' },
  { id: 'settings', label: 'Settings' },
  { id: 'theme', label: 'Theme' },
  { id: 'embed', label: 'Embed' },
  { id: 'submissions', label: 'Submissions' },
];

export default function FormsTab({ meta, venueId, venueTz, initialFormId, onFormChange }: {
  meta: FnMeta;
  venueId: string;
  venueTz: string;
  initialFormId?: string | null;
  onFormChange?: (formId: string | null) => void;
}) {
  const [tick, setTick] = useState(0);
  const [state, setState] = useState<{ key: string; forms: WebForm[] | null; error: string | null } | null>(null);
  const [open, setOpenState] = useState<string | null>(initialFormId ?? null);
  const [creating, setCreating] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);
  const key = `${venueId}:${tick}`;
  const canWrite = meta.can_write;

  useEffect(() => {
    let alive = true;
    fnGet<{ forms: WebForm[] }>('/forms', { venue_id: venueId }).then(
      (r) => alive && setState({ key, forms: r.forms, error: null }),
      (e) => alive && setState({ key, forms: null, error: errorText(e) }),
    );
    return () => {
      alive = false;
    };
  }, [venueId, key]);

  const setOpen = (formId: string | null) => {
    setOpenState(formId);
    onFormChange?.(formId);
  };

  if (open) {
    return (
      <FormEditor
        key={open}
        formId={open}
        summary={state?.forms?.find((f) => f.id === open) ?? null}
        meta={meta}
        venueTz={venueTz}
        canWrite={canWrite}
        onBack={() => { setOpen(null); setTick((n) => n + 1); }}
        onDeleted={(message) => { setOpen(null); setFlash(message); setTick((n) => n + 1); }}
      />
    );
  }

  const forms = state?.forms;
  if (state?.error && !forms) return <PageState kind="error" title="Couldn’t load the web forms" detail={state.error} />;
  if (!forms) return <PageState kind="loading" title="Loading web forms…" />;

  return (
    <Section
      title="Web forms"
      meta={meta.mode === 'live'
        ? 'Enquiry forms for the venue’s website — each submission creates an enquiry and emails the venue.'
        : 'Enquiry forms for the venue’s website — while Orbit is live, submissions are emailed to the venue.'}
      actions={canWrite ? <Button size="sm" variant="primary" icon={Plus} onClick={() => setCreating(true)}>New form</Button> : <ReadOnlyNote />}
    >
      {flash && <Notice tone="info" style={{ marginBottom: 10 }}>{flash}</Notice>}
      {forms.length === 0 ? (
        <Panel><PageState kind="empty" title="No web forms at this venue" detail={canWrite ? 'Create one and embed it on the venue’s website.' : undefined} /></Panel>
      ) : (
        <div className="n-card" style={{ overflowX: 'auto' }}>
          <table className="n-table">
            <thead>
              <tr>
                <th>Form</th>
                <th>Status</th>
                <th className="num">Submissions</th>
                <th>Last received</th>
              </tr>
            </thead>
            <tbody>
              {forms.map((f) => (
                <tr key={f.id} onClick={() => setOpen(f.id)} style={{ cursor: 'pointer' }}>
                  <td style={{ minWidth: 180 }}>
                    <button type="button" onClick={(e) => { e.stopPropagation(); setOpen(f.id); }}
                      style={{ padding: 0, border: 'none', background: 'none', font: 'inherit', fontWeight: 600, color: 'var(--text)', cursor: 'pointer', textAlign: 'left' }}>
                      {f.name}
                    </button>
                    <div style={{ ...muted, ...mono, fontSize: 'var(--fs-xs)', marginTop: 2 }}>/f/{f.slug}</div>
                  </td>
                  <td>{f.active ? <Badge tone="ok">Taking submissions</Badge> : <Badge>Off</Badge>}</td>
                  <td className="num">{f.submissions ?? 0}</td>
                  <td style={{ whiteSpace: 'nowrap', color: 'var(--text-soft)' }}>{f.last_submitted_at ? relativeLabel(f.last_submitted_at) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {creating && (
        <NewFormDialog
          venueId={venueId}
          onClose={() => setCreating(false)}
          onCreated={(f) => { setCreating(false); setOpen(f.id); }}
        />
      )}
    </Section>
  );
}

function NewFormDialog({ venueId, onClose, onCreated }: { venueId: string; onClose: () => void; onCreated: (f: WebForm) => void }) {
  const id = useId();
  const [name, setName] = useState('Website enquiry');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const create = async () => {
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      onCreated(await fnSend<WebForm>('POST', '/forms', { venue_id: venueId, name: name.trim() }));
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  return (
    <Dialog title="New web form" width={460} onClose={() => { if (!busy) onClose(); }}>
      <Field label="Form name" htmlFor={`${id}-name`} hint="It starts with name, email, phone, date, guests and a message — change them next.">
        <input id={`${id}-name`} className="n-input" style={fullWidth} value={name} autoFocus onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') create(); }} />
      </Field>
      {error && <Notice tone="error" style={{ marginTop: 12 }}>{error}</Notice>}
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 20 }}>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="primary" onClick={create} disabled={busy || !name.trim()}>{busy ? 'Creating…' : 'Create form'}</Button>
      </div>
    </Dialog>
  );
}

function FormEditor({ formId, summary, meta, venueTz, canWrite, onBack, onDeleted }: {
  formId: string;
  /** The list's row for this form — it carries the submission counts. */
  summary: WebForm | null;
  meta: FnMeta;
  venueTz: string;
  canWrite: boolean;
  onBack: () => void;
  onDeleted: (message: string) => void;
}) {
  const [state, setState] = useState<{ form: WebForm | null; error: string | null } | null>(null);
  const [sub, setSub] = useState<Sub>('fields');
  const [seen, setSeen] = useState<Set<Sub>>(() => new Set<Sub>(['fields']));
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    let alive = true;
    fnGet<WebForm>(`/forms/${formId}`).then(
      (form) => alive && setState({ form: { ...form, submissions: summary?.submissions, last_submitted_at: summary?.last_submitted_at }, error: null }),
      (e) => alive && setState({ form: null, error: errorText(e) }),
    );
    return () => {
      alive = false;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps -- the counts are read once, with the form
  }, [formId]);

  const form = state?.form;
  const back = <div style={{ marginBottom: 10 }}><BackLink label="Web forms" onClick={onBack} /></div>;
  if (state?.error) return <>{back}<PageState kind="error" title="Couldn’t open this form" detail={state.error} /></>;
  if (!form) return <>{back}<PageState kind="loading" title="Opening the form…" /></>;

  const onSaved = (next: WebForm) => setState({ form: { ...form, ...next }, error: null });
  const choose = (id: string) => {
    setSub(id as Sub);
    setSeen((s) => new Set(s).add(id as Sub));
  };

  return (
    <div style={{ maxWidth: 1100 }}>
      {back}
      <Section
        title={form.name}
        meta={<span style={{ display: 'inline-flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {form.active ? <Badge tone="ok">Taking submissions</Badge> : <Badge>Off</Badge>}
          <span style={mono}>/f/{form.slug}</span>
        </span>}
        actions={
          <>
            <Button size="sm" icon={ExternalLink} onClick={() => window.open(form.embed.url, '_blank', 'noopener')}>Open form</Button>
            {canWrite && <Button size="sm" variant="danger" icon={Trash2} onClick={() => setConfirmDelete(true)}>Delete</Button>}
          </>
        }
      >
        <div style={{ marginBottom: 14 }}>
          <Tabs items={SUBS} value={sub} onChange={choose} label="Form sections" />
        </div>
        {/* Every opened section stays mounted (hidden), so unsaved edits
            survive a look at another section. */}
        <div hidden={sub !== 'fields'}><FormFieldsEditor form={form} canWrite={canWrite} onSaved={onSaved} /></div>
        {seen.has('settings') && <div hidden={sub !== 'settings'}><FormSettingsEditor form={form} meta={meta} canWrite={canWrite} onSaved={onSaved} /></div>}
        {seen.has('theme') && <div hidden={sub !== 'theme'}><FormThemeEditor form={form} canWrite={canWrite} onSaved={onSaved} /></div>}
        {sub === 'embed' && <FormEmbed form={form} />}
        {seen.has('submissions') && <div hidden={sub !== 'submissions'}><FormSubmissions form={form} timeZone={venueTz} /></div>}
      </Section>
      {confirmDelete && (
        <ConfirmDialog
          title={`Delete ${form.name}?`}
          confirmLabel="Delete form"
          busyLabel="Deleting…"
          danger
          onClose={() => setConfirmDelete(false)}
          onConfirm={async () => {
            let r: { deleted: boolean };
            try {
              r = await fnSend<{ deleted: boolean; active: boolean }>('DELETE', `/forms/${form.id}`);
            } catch (e) {
              throw new Error(errorText(e));
            }
            onDeleted(r.deleted
              ? `${form.name} was deleted.`
              : `${form.name} has submissions, so it was switched off instead — its submissions are kept.`);
          }}
        >
          <p style={{ margin: 0 }}>
            A form that has received submissions is switched off instead, so they’re kept. Remove it from the website too.
          </p>
        </ConfirmDialog>
      )}
    </div>
  );
}

function Snippet({ title, detail, code }: { title: string; detail: string; code: string }) {
  return (
    <Panel>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
        <div style={{ minWidth: 0, flex: '1 1 260px' }}>
          <div style={{ fontWeight: 600 }}>{title}</div>
          <p style={{ ...help, marginTop: 2 }}>{detail}</p>
        </div>
        <CopyButton text={code} />
      </div>
      <pre style={{
        margin: '10px 0 0', padding: '10px 12px', borderRadius: 'var(--radius)', background: 'var(--surface-alt)', color: 'var(--text)',
        ...mono, whiteSpace: 'pre-wrap', wordBreak: 'break-all', overflowWrap: 'anywhere',
      }}>{code}</pre>
    </Panel>
  );
}

function FormEmbed({ form }: { form: WebForm }) {
  const e = form.embed;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Snippet
        title="Script (recommended)"
        detail="Paste where the form should appear. It draws the form in the website’s own page and always shows your latest fields and theme."
        code={e.script}
      />
      <Snippet
        title="Lead tracking — every page of the website"
        detail="Paste once into the site-wide header (Squarespace: Settings → Advanced → Code injection → Header). It remembers which ad or campaign brought someone in, even when they reach the form later."
        code={e.tracker}
      />
      <Snippet
        title="iframe (fallback)"
        detail="For website builders that don’t allow scripts. The form sits in a frame at a fixed height."
        code={e.iframe}
      />
      <Snippet title="Form page" detail="A page of its own — link to it from emails or social posts." code={e.url} />
    </div>
  );
}
