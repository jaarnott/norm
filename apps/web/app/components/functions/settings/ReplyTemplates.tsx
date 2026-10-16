'use client';

/**
 * Reply templates: the canned first replies and follow-ups staff start an
 * email from, laid out by booking type × stage. A template is for this venue
 * or every venue (owner-only, the API enforces it), for one booking type or
 * any.
 */
import { Fragment, useEffect, useId, useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Dialog, { ConfirmDialog } from '../../ui/Dialog';
import PageState from '../../ui/PageState';
import { useBreakpoint } from '../../../hooks/useBreakpoint';
import { fnGet, fnSend } from '../shared/api';
import type { FnMeta } from '../shared/meta';
import { errorText } from './errors';
import RichTextEditor from './RichTextEditor';
import { cleanHtml, plainText } from './richText';
import type { ReplyStage, ReplyTemplate } from './types';
import { CheckboxRow, Field, FieldGrid, Notice, Panel, ReadOnlyNote, Section, fullWidth, help, muted } from './ui';

const STAGES: { id: ReplyStage; label: string }[] = [
  { id: 'first_reply', label: 'First reply' },
  { id: 'follow_up', label: 'Follow-up' },
];

/** Placeholders the imported templates use — offered in the editor. */
const PLACEHOLDERS = ['{{client_name}}', '{{sender_name}}', '{{quote_link}}', '[DATE]', '[TIME]', '[AREA]', '[SPEND]'];

type Editing = { template: ReplyTemplate | null; booking_type: string | null; stage: ReplyStage };

export default function ReplyTemplates({ venueId, meta, canWrite }: { venueId: string; meta: FnMeta; canWrite: boolean }) {
  const [tick, setTick] = useState(0);
  const [state, setState] = useState<{ key: string; rows: ReplyTemplate[] | null; error: string | null } | null>(null);
  const [editing, setEditing] = useState<Editing | null>(null);
  const { isMobile } = useBreakpoint();
  const key = `${venueId}:${tick}`;

  useEffect(() => {
    let alive = true;
    fnGet<{ rows: ReplyTemplate[] }>('/reply-templates', { venue_id: venueId, include_inactive: true }).then(
      (r) => alive && setState({ key, rows: r.rows, error: null }),
      (e) => alive && setState({ key, rows: null, error: errorText(e) }),
    );
    return () => {
      alive = false;
    };
  }, [venueId, key]);

  const rows = state?.rows;
  if (state?.error && !rows) return <PageState kind="error" title="Couldn’t load the reply templates" detail={state.error} />;
  if (!rows) return <PageState kind="loading" title="Loading reply templates…" />;

  const types: { value: string | null; label: string }[] = [
    { value: null, label: 'Any booking type' },
    ...meta.booking_types.map((b) => ({ value: b.value as string | null, label: b.label })),
  ];
  const cell = (bt: string | null, stage: ReplyStage) =>
    rows
      .filter((r) => r.booking_type === bt && r.stage === stage)
      .sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name));

  const replace = (t: ReplyTemplate) =>
    setState((s) => (s && s.rows ? { ...s, rows: s.rows.some((r) => r.id === t.id) ? s.rows.map((r) => (r.id === t.id ? t : r)) : [...s.rows, t] } : s));

  const list = (bt: string | null, stage: ReplyStage) => {
    const items = cell(bt, stage);
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
        {items.map((t) => (
          <button
            key={t.id}
            type="button"
            className="n-row"
            onClick={() => setEditing({ template: t, booking_type: t.booking_type, stage: t.stage })}
            style={{ display: 'block', padding: '6px 8px', border: '1px solid var(--line)', background: 'var(--bg)', opacity: t.active ? 1 : 0.7 }}
          >
            <span style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 500, minWidth: 0 }}>{t.name}</span>
              {!t.active && <Badge>Off</Badge>}
              {t.venue_id === null && <Badge tone="info">All venues</Badge>}
            </span>
            <span style={{ display: 'block', marginTop: 2, ...muted, fontSize: 'var(--fs-xs)', fontWeight: 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {t.subject || plainText(t.body_html) || 'No text yet'}
            </span>
          </button>
        ))}
        {items.length === 0 && <span style={{ ...muted, fontSize: 'var(--fs-xs)', padding: '4px 0' }}>None</span>}
        {canWrite && (
          <Button size="sm" variant="quiet" icon={Plus} style={{ alignSelf: 'flex-start' }}
            onClick={() => setEditing({ template: null, booking_type: bt, stage })}>
            Add
          </Button>
        )}
      </div>
    );
  };

  return (
    <Section
      title="Reply templates"
      meta={`${rows.filter((r) => r.active).length} in use · by booking type and stage`}
      actions={canWrite ? undefined : <ReadOnlyNote />}
    >
      {isMobile ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {types.map((t) => (
            <Panel key={t.value ?? 'any'}>
              <div style={{ fontWeight: 600, marginBottom: 8 }}>{t.label}</div>
              {STAGES.map((s) => (
                <div key={s.id} style={{ marginTop: 8 }}>
                  <div className="n-eyebrow" style={{ marginBottom: 6 }}>{s.label}</div>
                  {list(t.value, s.id)}
                </div>
              ))}
            </Panel>
          ))}
        </div>
      ) : (
        <div className="n-card" style={{ display: 'grid', gridTemplateColumns: 'minmax(140px, 200px) minmax(0, 1fr) minmax(0, 1fr)', overflow: 'hidden' }}>
          <div style={headCell} />
          {STAGES.map((s) => <div key={s.id} style={headCell}>{s.label}</div>)}
          {types.map((t) => (
            <Fragment key={t.value ?? 'any'}>
              <div style={{ ...bodyCell, fontWeight: 600 }}>{t.label}</div>
              {STAGES.map((s) => <div key={s.id} style={bodyCell}>{list(t.value, s.id)}</div>)}
            </Fragment>
          ))}
        </div>
      )}
      <p style={{ ...help, marginTop: 10 }}>
        A template for “Any booking type” or “All venues” shows wherever it applies. Only owners can add or change all-venue templates.
      </p>
      {editing && (
        <ReplyEditor
          key={editing.template?.id ?? `new-${editing.booking_type}-${editing.stage}`}
          editing={editing}
          venueId={venueId}
          meta={meta}
          canWrite={canWrite}
          nextSort={cell(editing.booking_type, editing.stage).length}
          onClose={() => setEditing(null)}
          onSaved={(t) => { replace(t); setEditing({ template: t, booking_type: t.booking_type, stage: t.stage }); }}
          onDeleted={() => { setEditing(null); setTick((n) => n + 1); }}
        />
      )}
    </Section>
  );
}

const headCell = { padding: '10px 12px', background: 'var(--surface-alt)', borderBottom: '1px solid var(--line-strong)', fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--text-soft)' };
const bodyCell = { padding: '10px 12px', borderBottom: '1px solid var(--line)', minWidth: 0 };

function ReplyEditor({ editing, venueId, meta, canWrite, nextSort, onClose, onSaved, onDeleted }: {
  editing: Editing;
  venueId: string;
  meta: FnMeta;
  canWrite: boolean;
  nextSort: number;
  onClose: () => void;
  onSaved: (t: ReplyTemplate) => void;
  onDeleted: () => void;
}) {
  const t = editing.template;
  const isOwner = meta.user.is_owner;
  // An all-venue template is the owner's to change.
  const locked = !canWrite || (t?.venue_id === null && !isOwner);
  const id = useId();
  const [name, setName] = useState(t?.name ?? '');
  const [subject, setSubject] = useState(t?.subject ?? '');
  const [bookingType, setBookingType] = useState<string>(editing.booking_type ?? '');
  const [stage, setStage] = useState<ReplyStage>(editing.stage);
  const [allVenues, setAllVenues] = useState(t ? t.venue_id === null : false);
  const [active, setActive] = useState(t?.active ?? true);
  const [order, setOrder] = useState(String(t?.sort_order ?? nextSort));
  const [body, setBody] = useState(t?.body_html ?? '');
  const [bodyTouched, setBodyTouched] = useState(false);
  const [touched, setTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const touch = () => {
    setTouched(true);
    setSaved(false);
    setError(null);
  };

  const save = async () => {
    if (!name.trim()) {
      setError('Give the template a name.');
      return;
    }
    setSaving(true);
    setError(null);
    const fields: Record<string, unknown> = {
      venue_id: allVenues ? null : venueId,
      booking_type: bookingType || null,
      stage,
      name: name.trim(),
      subject: subject.trim() || null,
      active,
      sort_order: Number(order) || 0,
    };
    if (!t || bodyTouched) fields.body_html = cleanHtml(body);
    try {
      let result: ReplyTemplate;
      if (t) {
        // Send only what changed: an unchanged all-venue scope must not be re-sent by a non-owner.
        const changed = Object.fromEntries(Object.entries(fields).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify((t as unknown as Record<string, unknown>)[k])));
        result = await fnSend<ReplyTemplate>('PATCH', `/reply-templates/${t.id}`, { version: t.version, ...changed });
      } else {
        result = await fnSend<ReplyTemplate>('POST', '/reply-templates', fields);
      }
      setTouched(false);
      setBodyTouched(false);
      setSaved(true);
      onSaved(result);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  const dirty = !t || touched || bodyTouched;
  return (
    <Dialog title={t ? t.name : 'New reply template'} width={760} onClose={() => { if (!saving && !confirmDelete) onClose(); }} closeButton>
      {t?.venue_id === null && !isOwner && (
        <Notice tone="info" style={{ marginBottom: 12 }}>This template is shared by every venue — only an owner can change it.</Notice>
      )}
      <FieldGrid min={220}>
        <Field label="Name" htmlFor={`${id}-name`}>
          <input id={`${id}-name`} className="n-input" style={fullWidth} value={name} disabled={locked}
            onChange={(e) => { setName(e.target.value); touch(); }} placeholder="Set menu — first reply" />
        </Field>
        <Field label="Subject (optional)" htmlFor={`${id}-subject`}>
          <input id={`${id}-subject`} className="n-input" style={fullWidth} value={subject} disabled={locked}
            onChange={(e) => { setSubject(e.target.value); touch(); }} placeholder="Optional" />
        </Field>
      </FieldGrid>
      <div style={{ marginTop: 12 }}>
        <FieldGrid min={160}>
          <Field label="Booking type" htmlFor={`${id}-bt`}>
            <select id={`${id}-bt`} className="n-select" style={fullWidth} value={bookingType} disabled={locked}
              onChange={(e) => { setBookingType(e.target.value); touch(); }}>
              <option value="">Any booking type</option>
              {meta.booking_types.map((b) => <option key={b.value} value={b.value}>{b.label}</option>)}
            </select>
          </Field>
          <Field label="Stage" htmlFor={`${id}-stage`}>
            <select id={`${id}-stage`} className="n-select" style={fullWidth} value={stage} disabled={locked}
              onChange={(e) => { setStage(e.target.value as ReplyStage); touch(); }}>
              {STAGES.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
            </select>
          </Field>
          <Field label="Venues" htmlFor={`${id}-scope`} hint={!isOwner ? 'Only owners can share a template with every venue.' : undefined}>
            <select id={`${id}-scope`} className="n-select" style={fullWidth} value={allVenues ? 'all' : 'venue'} disabled={locked || !isOwner}
              onChange={(e) => { setAllVenues(e.target.value === 'all'); touch(); }}>
              <option value="venue">This venue</option>
              <option value="all">All venues</option>
            </select>
          </Field>
          <Field label="Order" htmlFor={`${id}-order`} hint="Lower comes first.">
            <input id={`${id}-order`} className="n-input" style={fullWidth} inputMode="numeric" value={order} disabled={locked}
              onChange={(e) => { setOrder(e.target.value.replace(/[^0-9]/g, '')); touch(); }} />
          </Field>
        </FieldGrid>
      </div>
      <div style={{ marginTop: 12 }}>
        <span id={`${id}-body`} className="n-label">Message</span>
        <RichTextEditor
          html={body}
          resetKey={t?.id ?? 'new'}
          labelledBy={`${id}-body`}
          disabled={locked}
          placeholders={PLACEHOLDERS}
          onChange={(h) => { setBody(h); setBodyTouched(true); setSaved(false); setError(null); }}
        />
        <p style={help}>
          Placeholders such as {'{{client_name}}'} stay in the text for the person (or Norm) writing the reply to fill; square-bracket notes like [DATE] are reminders to replace.
        </p>
      </div>
      <div style={{ marginTop: 12 }}>
        <CheckboxRow checked={active} onChange={(on) => { setActive(on); touch(); }} disabled={locked} label="In use" description="Templates that are off aren’t offered when writing a reply." />
      </div>
      {error && <Notice tone="error" style={{ marginTop: 12 }}>{error}</Notice>}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 20, flexWrap: 'wrap' }}>
        {t && !locked && (
          <Button variant="quiet" icon={Trash2} onClick={() => setConfirmDelete(true)}>Delete</Button>
        )}
        {saved && !dirty && <span role="status" style={muted}>Saved</span>}
        <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 8 }}>
          <Button onClick={onClose} disabled={saving}>{locked ? 'Close' : 'Cancel'}</Button>
          {!locked && (
            <Button variant="primary" onClick={save} disabled={!dirty || saving}>{saving ? 'Saving…' : t ? 'Save template' : 'Add template'}</Button>
          )}
          {!canWrite && <ReadOnlyNote />}
        </span>
      </div>
      {confirmDelete && t && (
        <ConfirmDialog
          title={`Delete ${t.name}?`}
          confirmLabel="Delete template"
          busyLabel="Deleting…"
          danger
          onClose={() => setConfirmDelete(false)}
          onConfirm={async () => {
            try {
              await fnSend('DELETE', `/reply-templates/${t.id}`);
            } catch (e) {
              throw new Error(errorText(e));
            }
            onDeleted();
          }}
        >
          <p style={{ margin: 0 }}>It’s removed for good. To keep it but stop it being offered, untick “In use” instead.</p>
        </ConfirmDialog>
      )}
    </Dialog>
  );
}
