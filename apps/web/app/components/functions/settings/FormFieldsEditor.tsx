'use client';

/**
 * A web form's fields, from the fixed set (name, email, phone, text, long
 * text, number, date, dates, time range, choice, tick boxes, section) — each
 * with its label, key, help, whether it's required, choices, and which
 * enquiry detail its answer fills when the form creates enquiries (live).
 * The whole list is saved at once, in order (PUT …/fields).
 */
import { useId, useState } from 'react';
import { ChevronDown, ChevronRight, Plus, Trash2 } from 'lucide-react';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import IconButton from '../../ui/IconButton';
import { fnSend } from '../shared/api';
import { errorText } from './errors';
import { FIELD_TYPES, MAPS_TO, choicesToText, fieldProblems, hasChoices, keyFromLabel, textToChoices, typeLabel } from './formFields';
import { moveRow, newUid } from './menuTemplate';
import type { FormField, FormFieldType, WebForm } from './types';
import { CheckboxRow, Field, FieldGrid, MoveButtons, Notice, SaveRow, fullWidth, mono, muted } from './ui';

interface Row {
  uid: string;
  key: string;
  label: string;
  type: FormFieldType;
  required: boolean;
  choices: string;
  min: string;
  max: string;
  placeholder: string;
  help_text: string;
  maps_to: string;
  /** A new field's key follows its label until edited by hand. */
  keyFollows: boolean;
}

function rowOf(f: FormField): Row {
  const range = !Array.isArray(f.options) && f.options ? f.options : null;
  return {
    uid: newUid(),
    key: f.key,
    label: f.label,
    type: f.type,
    required: f.required,
    choices: choicesToText(f.options),
    min: range?.min != null ? String(range.min) : '',
    max: range?.max != null ? String(range.max) : '',
    placeholder: f.placeholder ?? '',
    help_text: f.help_text ?? '',
    maps_to: f.maps_to ?? '',
    keyFollows: false,
  };
}

function fieldOf(r: Row): FormField {
  let options: FormField['options'] = null;
  if (hasChoices(r.type)) options = textToChoices(r.choices);
  if (r.type === 'number' && (r.min.trim() || r.max.trim())) {
    options = {
      ...(r.min.trim() ? { min: Number(r.min) } : {}),
      ...(r.max.trim() ? { max: Number(r.max) } : {}),
    };
  }
  return {
    key: r.key.trim(),
    label: r.label.trim(),
    type: r.type,
    required: r.type === 'section' ? false : r.required,
    options: options && Array.isArray(options) && options.length === 0 && r.type === 'checkbox' ? null : options,
    placeholder: r.placeholder.trim() || null,
    help_text: r.help_text.trim() || null,
    maps_to: r.maps_to || null,
  };
}

export default function FormFieldsEditor({ form, canWrite, onSaved }: {
  form: WebForm;
  canWrite: boolean;
  onSaved: (f: WebForm) => void;
}) {
  const [rows, setRows] = useState<Row[]>(() => (form.fields ?? []).map(rowOf));
  const [open, setOpen] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const edit = (next: Row[]) => {
    setRows(next);
    setTouched(true);
    setSaved(false);
    setError(null);
  };
  const setRow = (uid: string, p: Partial<Row>) =>
    edit(rows.map((r) => {
      if (r.uid !== uid) return r;
      const next = { ...r, ...p };
      if ('label' in p && r.keyFollows) next.key = keyFromLabel(next.label, rows.filter((x) => x.uid !== uid).map((x) => x.key));
      if ('key' in p) next.keyFollows = false;
      return next;
    }));

  const fields = rows.map(fieldOf);
  const problems = fieldProblems(fields);

  const add = () => {
    const label = 'New question';
    const row: Row = {
      uid: newUid(), key: keyFromLabel(label, rows.map((r) => r.key)), label, type: 'text', required: false,
      choices: '', min: '', max: '', placeholder: '', help_text: '', maps_to: '', keyFollows: true,
    };
    edit([...rows, row]);
    setOpen(row.uid);
  };

  const save = async () => {
    if (problems.length) {
      setError(problems[0]);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const next = await fnSend<WebForm>('PUT', `/forms/${form.id}/fields`, { fields, expected_version: form.version });
      setRows((next.fields ?? []).map(rowOf));
      setTouched(false);
      setSaved(true);
      onSaved(next);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <div className="n-card" style={{ overflow: 'hidden' }}>
        {rows.length === 0 && <p style={{ ...muted, padding: 16, margin: 0 }}>No fields yet.</p>}
        {rows.map((r, i) => (
          <FieldRow
            key={r.uid}
            row={r}
            index={i}
            count={rows.length}
            open={open === r.uid}
            canWrite={canWrite}
            onToggle={() => setOpen(open === r.uid ? null : r.uid)}
            onChange={(p) => setRow(r.uid, p)}
            onMove={(d) => edit(moveRow(rows, i, d))}
            onRemove={() => edit(rows.filter((x) => x.uid !== r.uid))}
          />
        ))}
      </div>
      {canWrite && <Button size="sm" icon={Plus} onClick={add} style={{ marginTop: 10 }} disabled={rows.length >= 60}>Add field</Button>}
      {touched && problems.length > 0 && (
        <Notice tone="warn" style={{ marginTop: 10 }}>{problems.map((p) => <div key={p}>{p}</div>)}</Notice>
      )}
      <SaveRow canWrite={canWrite} dirty={touched} saving={saving} saved={saved} error={error} onSave={save}
        onDiscard={() => { setRows((form.fields ?? []).map(rowOf)); setTouched(false); setError(null); }} label="Save fields" />
    </div>
  );
}

function FieldRow({ row, index, count, open, canWrite, onToggle, onChange, onMove, onRemove }: {
  row: Row;
  index: number;
  count: number;
  open: boolean;
  canWrite: boolean;
  onToggle: () => void;
  onChange: (p: Partial<Row>) => void;
  onMove: (d: -1 | 1) => void;
  onRemove: () => void;
}) {
  const id = useId();
  const isSection = row.type === 'section';
  return (
    <div style={{ borderTop: index === 0 ? 'none' : '1px solid var(--line)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 8px 6px 4px', background: isSection ? 'var(--surface-alt)' : open ? 'var(--surface)' : undefined }}>
        <IconButton icon={open ? ChevronDown : ChevronRight} iconSize={16} label={`${open ? 'Close' : 'Edit'} ${row.label || 'field'}`} aria-expanded={open} onClick={onToggle} />
        <button type="button" onClick={onToggle}
          style={{ flex: 1, minWidth: 0, textAlign: 'left', background: 'none', border: 'none', padding: '2px 0', font: 'inherit', color: 'inherit', cursor: 'pointer' }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <span style={{ fontWeight: isSection ? 600 : 500 }}>{row.label || 'Untitled'}</span>
            {row.required && !isSection && <Badge tone="accent">Required</Badge>}
          </span>
          <span style={{ display: 'block', marginTop: 2, ...muted, fontSize: 'var(--fs-xs)' }}>
            {typeLabel(row.type)} · <code style={mono}>{row.key || 'section'}</code>
            {row.maps_to ? ` · fills ${MAPS_TO.find((m) => m.value === row.maps_to)?.label ?? row.maps_to}` : ''}
          </span>
        </button>
        {canWrite && (
          <span style={{ display: 'inline-flex' }}>
            <MoveButtons index={index} count={count} onMove={onMove} what={row.label || 'field'} />
            <IconButton icon={Trash2} iconSize={16} label={`Remove ${row.label || 'field'}`} onClick={onRemove} />
          </span>
        )}
      </div>
      {open && (
        <div style={{ padding: '10px 12px 14px', background: 'var(--surface)', borderTop: '1px solid var(--line-soft)' }}>
          <FieldGrid min={200}>
            <Field label="Label" htmlFor={`${id}-label`}>
              <input id={`${id}-label`} className="n-input" style={fullWidth} value={row.label} disabled={!canWrite}
                onChange={(e) => onChange({ label: e.target.value })} />
            </Field>
            <Field label="Type" htmlFor={`${id}-type`}>
              <select id={`${id}-type`} className="n-select" style={fullWidth} value={row.type} disabled={!canWrite}
                onChange={(e) => onChange({ type: e.target.value as FormFieldType })}>
                {FIELD_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </Field>
            <Field label="Key" htmlFor={`${id}-key`} hint="Lowercase letters, numbers and _ — used in the email subject and the data.">
              <input id={`${id}-key`} className="n-input" style={{ ...fullWidth, ...mono }} value={row.key} disabled={!canWrite}
                placeholder={isSection ? 'Filled in for you' : ''} onChange={(e) => onChange({ key: e.target.value.toLowerCase() })} />
            </Field>
          </FieldGrid>
          {!isSection && (
            <>
              <div style={{ marginTop: 12 }}>
                <FieldGrid min={200}>
                  <Field label="Help text" htmlFor={`${id}-help`}>
                    <input id={`${id}-help`} className="n-input" style={fullWidth} value={row.help_text} disabled={!canWrite}
                      onChange={(e) => onChange({ help_text: e.target.value })} />
                  </Field>
                  <Field label="Placeholder" htmlFor={`${id}-ph`}>
                    <input id={`${id}-ph`} className="n-input" style={fullWidth} value={row.placeholder} disabled={!canWrite}
                      onChange={(e) => onChange({ placeholder: e.target.value })} />
                  </Field>
                  <Field label="Fills on the enquiry" htmlFor={`${id}-maps`} hint="When the form creates enquiries (live).">
                    <select id={`${id}-maps`} className="n-select" style={fullWidth} value={row.maps_to} disabled={!canWrite}
                      onChange={(e) => onChange({ maps_to: e.target.value })}>
                      <option value="">Nothing — kept in the notes</option>
                      {MAPS_TO.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
                    </select>
                  </Field>
                </FieldGrid>
              </div>
              {hasChoices(row.type) && (
                <div style={{ marginTop: 12 }}>
                  <Field label="Choices" htmlFor={`${id}-choices`}
                    hint={row.type === 'checkbox'
                      ? 'One per line. Leave empty for a single tick box (yes / no). “value | Label” stores a different value.'
                      : 'One per line. “value | Label” stores a different value — for booking type, use the booking-type keys as values.'}>
                    <textarea id={`${id}-choices`} className="n-input" rows={4} style={fullWidth} value={row.choices} disabled={!canWrite}
                      onChange={(e) => onChange({ choices: e.target.value })} />
                  </Field>
                </div>
              )}
              {row.type === 'number' && (
                <div style={{ marginTop: 12, maxWidth: 320 }}>
                  <FieldGrid min={120}>
                    <Field label="Lowest" htmlFor={`${id}-min`}>
                      <input id={`${id}-min`} className="n-input" style={fullWidth} inputMode="numeric" value={row.min} disabled={!canWrite}
                        onChange={(e) => onChange({ min: e.target.value.replace(/[^0-9.-]/g, '') })} />
                    </Field>
                    <Field label="Highest" htmlFor={`${id}-max`}>
                      <input id={`${id}-max`} className="n-input" style={fullWidth} inputMode="numeric" value={row.max} disabled={!canWrite}
                        onChange={(e) => onChange({ max: e.target.value.replace(/[^0-9.-]/g, '') })} />
                    </Field>
                  </FieldGrid>
                </div>
              )}
              <div style={{ marginTop: 12 }}>
                <CheckboxRow checked={row.required} onChange={(on) => onChange({ required: on })} disabled={!canWrite} label="Required" />
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
