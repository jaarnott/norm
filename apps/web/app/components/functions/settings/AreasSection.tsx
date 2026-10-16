'use client';

/**
 * Venue areas: the bookable spaces, their capacities, which booking types use
 * them, whether a minimum spend there needs a manager's approval, notes for
 * quoting, and photos (used on proposals). Changing an area's approval rule
 * is for an owner or the venue's approver — the API says so (403) and the
 * checkbox is disabled for everyone else.
 */
import { useId, useState } from 'react';
import { ChevronDown, ChevronRight, ImageIcon, Plus, Trash2 } from 'lucide-react';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import IconButton from '../../ui/IconButton';
import PageState from '../../ui/PageState';
import { ConfirmDialog } from '../../ui/Dialog';
import { fnSend } from '../shared/api';
import type { FnOption } from '../shared/meta';
import { errorText } from './errors';
import { moveRow } from './menuTemplate';
import { uploadImage } from './upload';
import type { Area, AreaPhoto, FnFile } from './types';
import {
  AuthImage, CheckboxRow, Chip, Field, FieldGrid, MoveButtons, Notice, Panel, ReadOnlyNote, SaveRow, Section,
  UploadButton, UploadList, fullWidth, help, muted, useUploads,
} from './ui';

type AreasUpdate = (update: (areas: Area[]) => Area[]) => void;

interface Props {
  venueId: string;
  areas: Area[];
  setAreas: AreasUpdate;
  canWrite: boolean;
  canGovern: boolean;
  bookingTypes: (FnOption & { min_spend: boolean })[];
  onReload: () => void;
}

const SHORT: Record<string, string> = {
  seated_dining: 'Seated dining',
  group_booking: 'Group booking',
  large_event_partial: 'Partial exclusive',
  large_event_exclusive: 'Exclusive',
};

function capacityText(a: Area): string {
  return [
    a.capacity_seated ? `${a.capacity_seated} seated` : '',
    a.capacity_standing ? `${a.capacity_standing} standing` : '',
  ].filter(Boolean).join(' · ');
}

export default function AreasSection({ venueId, areas, setAreas, canWrite, canGovern, bookingTypes, onReload }: Props) {
  const [open, setOpen] = useState<string | null>(null);
  const [showRetired, setShowRetired] = useState(false);
  const [moving, setMoving] = useState(false);
  const [moveError, setMoveError] = useState<string | null>(null);
  const toggleId = useId();

  const sorted = [...areas].sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name));
  const retired = sorted.filter((a) => !a.active).length;
  const visible = showRetired ? sorted : sorted.filter((a) => a.active);
  const needApproval = sorted.filter((a) => a.active && a.requires_approval).length;

  /** Reorder within what's shown; hidden (retired) areas keep their slots. */
  const move = async (index: number, delta: -1 | 1) => {
    const nextVisible = moveRow(visible, index, delta);
    if (nextVisible === visible) return;
    const shownIds = new Set(visible.map((a) => a.id));
    let k = 0;
    const order = sorted.map((a) => (shownIds.has(a.id) ? nextVisible[k++] : a));
    setMoving(true);
    setMoveError(null);
    try {
      for (let i = 0; i < order.length; i += 1) {
        const a = order[i];
        if (a.sort_order === i) continue;
        const saved = await fnSend<Area>('PATCH', `/areas/${a.id}`, { version: a.version, sort_order: i });
        setAreas((list) => list.map((x) => (x.id === saved.id ? { ...saved, photos: saved.photos ?? x.photos } : x)));
      }
    } catch (e) {
      setMoveError(errorText(e));
      onReload();
    } finally {
      setMoving(false);
    }
  };

  const typeLabel = (t: string) => SHORT[t] ?? bookingTypes.find((b) => b.value === t)?.label ?? t;

  return (
    <Section
      title="Areas"
      meta={`${sorted.length - retired} bookable · ${needApproval} need minimum-spend approval`}
      actions={canWrite ? (
        <Button size="sm" icon={Plus} onClick={() => setOpen('new')} disabled={open === 'new'}>Add area</Button>
      ) : <ReadOnlyNote />}
    >
      {retired > 0 && (
        <div style={{ marginBottom: 8 }}>
          <CheckboxRow id={toggleId} checked={showRetired} onChange={setShowRetired} label={`Show retired areas (${retired})`} />
        </div>
      )}
      {moveError && <Notice tone="error" style={{ marginBottom: 8 }}>{moveError}</Notice>}
      {open === 'new' && (
        <div style={{ marginBottom: 10 }}>
          <AreaEditor
            area={null}
            venueId={venueId}
            canWrite={canWrite}
            canGovern={canGovern}
            bookingTypes={bookingTypes}
            nextSort={sorted.length ? Math.max(...sorted.map((a) => a.sort_order)) + 1 : 0}
            onSaved={(a) => { setAreas((list) => [...list, a]); setOpen(a.id); }}
            onCancel={() => setOpen(null)}
            onDeleted={() => setOpen(null)}
            setAreas={setAreas}
          />
        </div>
      )}
      {visible.length === 0 && open !== 'new' ? (
        <Panel><PageState kind="empty" title="No areas yet" detail="Add the spaces this venue books out — the deck, a private room, the whole venue." /></Panel>
      ) : (
        <div className="n-card" style={{ overflow: 'hidden' }}>
          {visible.map((a, i) => {
            const expanded = open === a.id;
            const caps = capacityText(a);
            return (
              <div key={a.id} style={{ borderTop: i === 0 ? 'none' : '1px solid var(--line)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '8px 8px 8px 4px', background: expanded ? 'var(--surface)' : undefined }}>
                  <IconButton
                    icon={expanded ? ChevronDown : ChevronRight}
                    iconSize={16}
                    label={`${expanded ? 'Close' : 'Open'} ${a.name}`}
                    aria-expanded={expanded}
                    onClick={() => setOpen(expanded ? null : a.id)}
                  />
                  <button
                    type="button"
                    onClick={() => setOpen(expanded ? null : a.id)}
                    style={{ flex: 1, minWidth: 0, textAlign: 'left', background: 'none', border: 'none', padding: '2px 0', cursor: 'pointer', font: 'inherit', color: 'inherit' }}
                  >
                    <span style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <span style={{ fontWeight: 600, color: a.active ? 'var(--text)' : 'var(--muted)' }}>{a.name}</span>
                      {!a.active && <Badge>Retired</Badge>}
                      {a.active && (a.requires_approval ? <Badge tone="warn">Needs approval</Badge> : <Badge>No approval</Badge>)}
                    </span>
                    <span style={{ display: 'block', marginTop: 2, ...muted }}>
                      {[caps, a.booking_types.length ? a.booking_types.map(typeLabel).join(', ') : 'All booking types']
                        .filter(Boolean).join(' · ')}
                      {(a.photos?.length ?? 0) > 0 && (
                        <span style={{ marginLeft: 8, display: 'inline-flex', alignItems: 'center', gap: 3 }}>
                          <Icon icon={ImageIcon} size={12} tone="muted" /> {a.photos?.length}
                        </span>
                      )}
                    </span>
                  </button>
                  {canWrite && (
                    <MoveButtons index={i} count={visible.length} onMove={(d) => move(i, d)} disabled={moving} what={a.name} />
                  )}
                </div>
                {expanded && (
                  <div style={{ padding: '4px 12px 14px', background: 'var(--surface)', borderTop: '1px solid var(--line-soft)' }}>
                    <AreaEditor
                      key={a.id}
                      area={a}
                      venueId={venueId}
                      canWrite={canWrite}
                      canGovern={canGovern}
                      bookingTypes={bookingTypes}
                      nextSort={a.sort_order}
                      onSaved={(saved) => setAreas((list) => list.map((x) => (x.id === saved.id ? saved : x)))}
                      onCancel={() => setOpen(null)}
                      onDeleted={(result) => {
                        setOpen(null);
                        if (result === 'deleted') setAreas((list) => list.filter((x) => x.id !== a.id));
                        else onReload();
                      }}
                      setAreas={setAreas}
                      flat
                    />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </Section>
  );
}

interface Draft {
  name: string;
  description: string;
  capacity_seated: string;
  capacity_standing: string;
  booking_types: string[];
  requires_approval: boolean;
  min_spend_notes: string;
  active: boolean;
}

function draftOf(a: Area | null): Draft {
  return {
    name: a?.name ?? '',
    description: a?.description ?? '',
    capacity_seated: a?.capacity_seated != null ? String(a.capacity_seated) : '',
    capacity_standing: a?.capacity_standing != null ? String(a.capacity_standing) : '',
    booking_types: [...(a?.booking_types ?? [])],
    requires_approval: a?.requires_approval ?? true,
    min_spend_notes: a?.min_spend_notes ?? '',
    active: a?.active ?? true,
  };
}

function toBody(d: Draft) {
  const num = (s: string) => (s.trim() === '' ? null : Math.max(0, Math.round(Number(s))));
  return {
    name: d.name.trim(),
    description: d.description.trim() || null,
    capacity_seated: num(d.capacity_seated),
    capacity_standing: num(d.capacity_standing),
    booking_types: d.booking_types,
    requires_approval: d.requires_approval,
    min_spend_notes: d.min_spend_notes.trim() || null,
    active: d.active,
  };
}

function AreaEditor({ area, venueId, canWrite, canGovern, bookingTypes, nextSort, onSaved, onCancel, onDeleted, setAreas, flat }: {
  area: Area | null;
  venueId: string;
  canWrite: boolean;
  canGovern: boolean;
  bookingTypes: (FnOption & { min_spend: boolean })[];
  nextSort: number;
  onSaved: (a: Area) => void;
  onCancel: () => void;
  onDeleted: (result: 'deleted' | 'retired') => void;
  setAreas: AreasUpdate;
  flat?: boolean;
}) {
  const [draft, setDraft] = useState<Draft>(() => draftOf(area));
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const id = useId();

  const base = toBody(draftOf(area));
  const body = toBody(draft);
  const changed = Object.fromEntries(
    Object.entries(body).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(base[k as keyof typeof base])),
  );
  const dirty = area ? Object.keys(changed).length > 0 : !!body.name;
  const badNumber = [draft.capacity_seated, draft.capacity_standing].some((s) => s.trim() !== '' && !(Number(s) >= 0));

  const edit = (p: Partial<Draft>) => {
    setDraft((d) => ({ ...d, ...p }));
    setSaved(false);
    setError(null);
  };

  const save = async () => {
    if (!body.name) {
      setError('Give the area a name.');
      return;
    }
    if (badNumber) {
      setError('Capacities are whole numbers (or leave them blank).');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const result = area
        ? await fnSend<Area>('PATCH', `/areas/${area.id}`, { version: area.version, ...changed })
        : await fnSend<Area>('POST', `/venues/${venueId}/areas`, { ...body, sort_order: nextSort });
      setSaved(true);
      setDraft(draftOf(result));
      onSaved({ ...result, photos: result.photos ?? area?.photos ?? [] });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  const toggleType = (t: string) =>
    edit({ booking_types: draft.booking_types.includes(t) ? draft.booking_types.filter((x) => x !== t) : [...draft.booking_types, t] });

  const form = (
    <>
      <FieldGrid min={220}>
        <Field label="Name" htmlFor={`${id}-name`}>
          <input id={`${id}-name`} className="n-input" style={fullWidth} value={draft.name} disabled={!canWrite}
            onChange={(e) => edit({ name: e.target.value })} placeholder="e.g. Sundeck" />
        </Field>
        <Field label="Seated" htmlFor={`${id}-seated`}>
          <input id={`${id}-seated`} className="n-input" style={fullWidth} inputMode="numeric" value={draft.capacity_seated} disabled={!canWrite}
            onChange={(e) => edit({ capacity_seated: e.target.value.replace(/[^0-9]/g, '') })} placeholder="—" />
        </Field>
        <Field label="Standing" htmlFor={`${id}-standing`}>
          <input id={`${id}-standing`} className="n-input" style={fullWidth} inputMode="numeric" value={draft.capacity_standing} disabled={!canWrite}
            onChange={(e) => edit({ capacity_standing: e.target.value.replace(/[^0-9]/g, '') })} placeholder="—" />
        </Field>
      </FieldGrid>
      <div style={{ marginTop: 14 }}>
        <span className="n-label">Booking types</span>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {bookingTypes.map((b) => (
            <Chip key={b.value} on={draft.booking_types.includes(b.value)} onClick={() => toggleType(b.value)} disabled={!canWrite}>
              {b.label}
            </Chip>
          ))}
        </div>
        <p style={help}>None picked means the area can be offered for every booking type.</p>
      </div>
      <div style={{ marginTop: 14 }}>
        <Field label="Description" htmlFor={`${id}-desc`} hint="Shown on proposals and used by Norm when replying to enquiries about this space.">
          <textarea id={`${id}-desc`} className="n-input" rows={3} style={fullWidth} value={draft.description} disabled={!canWrite}
            onChange={(e) => edit({ description: e.target.value })} />
        </Field>
      </div>
      <div style={{ marginTop: 14 }}>
        <Field label="Minimum-spend notes" htmlFor={`${id}-ms`} hint="For staff quoting this space, e.g. “$2,000 Fri/Sat, $1,200 midweek”.">
          <input id={`${id}-ms`} className="n-input" style={fullWidth} value={draft.min_spend_notes} disabled={!canWrite}
            onChange={(e) => edit({ min_spend_notes: e.target.value })} />
        </Field>
      </div>
      <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
        <CheckboxRow
          checked={draft.requires_approval}
          onChange={(on) => edit({ requires_approval: on })}
          disabled={!canWrite || !canGovern}
          label="Minimum spends here need a manager’s approval"
          description={canGovern
            ? 'Approval means “OK to offer” — the customer can only confirm an approved option.'
            : 'Only an owner or this venue’s minimum-spend approver can change this.'}
        />
        {area && (
          <CheckboxRow
            checked={draft.active}
            onChange={(on) => edit({ active: on })}
            disabled={!canWrite}
            label="Bookable"
            description="Untick to retire the area: it stays on past bookings but isn’t offered."
          />
        )}
      </div>
      <SaveRow
        canWrite={canWrite}
        dirty={dirty}
        saving={saving}
        saved={saved}
        error={error}
        onSave={save}
        label={area ? 'Save area' : 'Add area'}
        extra={
          <>
            {!area && <Button variant="quiet" onClick={onCancel}>Cancel</Button>}
            {area && canWrite && (
              <Button variant="quiet" icon={Trash2} onClick={() => setConfirmDelete(true)} style={{ marginLeft: 'auto' }}>
                Delete area
              </Button>
            )}
          </>
        }
      />
      {area && <AreaPhotos area={area} canWrite={canWrite} setAreas={setAreas} />}
      {confirmDelete && area && (
        <ConfirmDialog
          title={`Delete ${area.name}?`}
          confirmLabel="Delete area"
          busyLabel="Deleting…"
          danger
          onClose={() => setConfirmDelete(false)}
          onConfirm={async () => {
            try {
              const r = await fnSend<{ result: 'deleted' | 'retired' }>('DELETE', `/areas/${area.id}`);
              onDeleted(r.result);
            } catch (e) {
              throw new Error(errorText(e));
            }
          }}
        >
          <p style={{ margin: 0 }}>
            If any options or bookings use this area it’s retired instead, so their history keeps it.
          </p>
        </ConfirmDialog>
      )}
    </>
  );
  return flat ? form : <Panel>{form}</Panel>;
}

function AreaPhotos({ area, canWrite, setAreas }: { area: Area; canWrite: boolean; setAreas: AreasUpdate }) {
  const photos = [...(area.photos ?? [])].sort((a, b) => a.sort_order - b.sort_order);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const setPhotos = (update: (p: AreaPhoto[]) => AreaPhoto[]) =>
    setAreas((list) => list.map((x) => (x.id === area.id ? { ...x, photos: update(x.photos ?? []) } : x)));

  const uploads = useUploads(async (file, onProgress) => {
    const stored = await uploadImage<FnFile>(file, onProgress);
    const photo = await fnSend<AreaPhoto>('POST', `/areas/${area.id}/photos`, { file_id: stored.id });
    setPhotos((p) => [...p, photo]);
  });

  const reorder = async (index: number, delta: -1 | 1) => {
    const next = moveRow(photos, index, delta);
    if (next === photos) return;
    setBusy(true);
    setError(null);
    try {
      const r = await fnSend<{ rows: AreaPhoto[] }>('PUT', `/areas/${area.id}/photos/order`, { photo_ids: next.map((p) => p.id) });
      setPhotos(() => r.rows);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (photo: AreaPhoto) => {
    setBusy(true);
    setError(null);
    try {
      await fnSend('DELETE', `/area-photos/${photo.id}`);
      setPhotos((p) => p.filter((x) => x.id !== photo.id));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const saveCaption = async (photo: AreaPhoto, caption: string) => {
    if ((photo.caption ?? '') === caption.trim()) return;
    setError(null);
    try {
      const saved = await fnSend<AreaPhoto>('PATCH', `/area-photos/${photo.id}`, { caption: caption.trim() || null });
      setPhotos((p) => p.map((x) => (x.id === saved.id ? saved : x)));
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <div style={{ marginTop: 18, paddingTop: 14, borderTop: '1px solid var(--line-soft)' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
        <span className="n-eyebrow">Photos</span>
        {canWrite && <UploadButton label="Add photos" onFiles={uploads.start} />}
      </div>
      {photos.length === 0 && !uploads.items.length && (
        <p style={{ ...help, marginTop: 6 }}>No photos yet. They appear with the space on proposals.</p>
      )}
      {photos.length > 0 && (
        <ul style={{ listStyle: 'none', margin: '10px 0 0', padding: 0, display: 'flex', flexWrap: 'wrap', gap: 12 }}>
          {photos.map((p, i) => (
            <li key={p.id} style={{ width: 150 }}>
              <AuthImage fileId={p.file_id} alt={p.caption || `${area.name} photo ${i + 1}`} width={150} height={100} />
              <input
                className="n-input"
                aria-label={`Caption for photo ${i + 1}`}
                defaultValue={p.caption ?? ''}
                placeholder="Caption"
                disabled={!canWrite}
                onBlur={(e) => saveCaption(p, e.target.value)}
                style={{ ...fullWidth, marginTop: 6, height: 30, fontSize: 'var(--fs-sm)' }}
              />
              {canWrite && (
                <div style={{ display: 'flex', alignItems: 'center', marginTop: 2 }}>
                  <MoveButtons index={i} count={photos.length} onMove={(d) => reorder(i, d)} disabled={busy} what={`photo ${i + 1}`} axis="horizontal" />
                  <IconButton icon={Trash2} iconSize={16} label={`Remove photo ${i + 1}`} disabled={busy} onClick={() => remove(p)} style={{ marginLeft: 'auto' }} />
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      <UploadList items={uploads.items} onDismiss={uploads.dismiss} />
      {error && <Notice tone="error" style={{ marginTop: 8 }}>{error}</Notice>}
    </div>
  );
}
