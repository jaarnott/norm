/**
 * Norm Functions — the client portal's working copy and its autosave, as a
 * pure reducer (side effects live in PortalEvent).
 *
 * The client edits a DRAFT. Every edit marks its key DIRTY; the page saves
 * dirty keys a moment after the typing stops, one request at a time, each
 * carrying the version it was based on (`expected_version`, plus each
 * menu's own). Edits made while a save is in flight stay dirty and go in the
 * next one. A refused save never loops:
 *
 * - 409 (the venue or a colleague saved first) → `conflict`: saving stops,
 *   the page offers "Reload". Reloading merges three ways: a key the client
 *   changed keeps the client's value when the venue didn't touch it (still
 *   dirty, saved against the new version); a key both changed takes the
 *   venue's value and is reported, so nothing is overwritten silently.
 * - other 4xx (not allowed, not valid) → `rejected`: the change is undone
 *   and the message shown.
 * - offline / 5xx / 429 → `retry`: kept, and sent again.
 *
 * Draft values are what the inputs hold (strings for text, numbers and
 * times; booleans for toggles); `toApi` turns them into the API's types.
 */
import { FIELD_KINDS, type FieldKind } from './portalFields';
import type { ListKind, ListRow, PortalPayload, PortalMenu } from './types';

export type FieldValue = string | boolean;
export type ContactKey = 'name' | 'phone' | 'company';
export const CONTACT_KEYS: ContactKey[] = ['name', 'phone', 'company'];

export interface Draft {
  fields: Record<string, FieldValue>;
  contact: Record<ContactKey, string>;
  lists: Partial<Record<ListKind, ListRow[]>>;
  /** menu id → item id → quantity */
  quantities: Record<string, Record<string, number>>;
}

export interface Dirty {
  fields: string[];
  contact: ContactKey[];
  lists: ListKind[];
  /** menu id → item ids */
  menus: Record<string, string[]>;
}

export type SaveState = 'idle' | 'pending' | 'saving' | 'saved' | 'retry' | 'rejected' | 'conflict';

export interface PortalState {
  server: PortalPayload;
  draft: Draft;
  dirty: Dirty;
  /** What the request in flight carries (null when none is). */
  sending: Dirty | null;
  save: SaveState;
  error: string | null;
  /** field key → why its value can't be saved yet (it stays out of saves). */
  invalid: Record<string, string>;
  /** Labels of keys where a reload kept the venue's change over the client's. */
  overridden: string[];
}

export type Action =
  | { type: 'loaded'; payload: PortalPayload }
  | { type: 'field'; key: string; value: FieldValue }
  | { type: 'contact'; key: ContactKey; value: string }
  | { type: 'list'; kind: ListKind; rows: ListRow[] }
  | { type: 'quantity'; menuId: string; itemId: string; value: number }
  | { type: 'saveStart' }
  | { type: 'saveDone'; payload: PortalPayload }
  | { type: 'saveFailed'; outcome: 'retry' | 'rejected' | 'conflict'; message: string }
  | { type: 'refreshed'; payload: PortalPayload }
  | { type: 'dismissOverridden' };

// ── Values ────────────────────────────────────────────────────────────────

export function kindOf(key: string): FieldKind {
  return FIELD_KINDS[key] ?? 'text';
}

/** API value → what the input shows. */
export function toDraft(kind: FieldKind, value: unknown): FieldValue {
  switch (kind) {
    case 'bool':
      return value === true;
    case 'int':
      return typeof value === 'number' ? String(value) : '';
    case 'time':
      return typeof value === 'string' ? value.slice(0, 5) : '';
    case 'money': {
      if (typeof value !== 'number') return '';
      return value % 100 === 0 ? String(value / 100) : (value / 100).toFixed(2);
    }
    default:
      return typeof value === 'string' ? value : '';
  }
}

export type Converted = { ok: true; value: string | number | boolean | null } | { ok: false; error: string };

/** What the input holds → the API's value, or why it can't be sent. */
export function toApi(kind: FieldKind, value: FieldValue): Converted {
  if (kind === 'bool') return { ok: true, value: value === true };
  const text = typeof value === 'string' ? value.trim() : '';
  switch (kind) {
    case 'int':
      if (!text) return { ok: true, value: null };
      if (!/^\d+$/.test(text) || Number(text) > 1_000_000_000) return { ok: false, error: 'Enter a whole number.' };
      return { ok: true, value: Number(text) };
    case 'time':
      if (!text) return { ok: true, value: null };
      return /^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$/.test(text) ? { ok: true, value: text.slice(0, 5) } : { ok: false, error: 'Enter a time like 19:30.' };
    case 'money': {
      const clean = text.replace(/[$,\s]/g, '');
      if (!clean) return { ok: true, value: null };
      if (!/^\d+(\.\d{1,2})?$/.test(clean) || Number(clean) > 10_000_000) return { ok: false, error: 'Enter an amount like 150 or 150.50.' };
      return { ok: true, value: Math.round(Number(clean) * 100) };
    }
    case 'choice':
      return { ok: true, value: text || null };
    default:
      return { ok: true, value: typeof value === 'string' ? value : '' };
  }
}

/** A list row's times as "HH:MM" for the API (the payload carries "HH:MM:SS"). */
function rowForApi(row: ListRow): ListRow {
  const out: ListRow = {};
  for (const [k, v] of Object.entries(row)) {
    if (typeof v === 'string' && /(_time|^at_time)$/.test(k)) out[k] = v ? v.slice(0, 5) : null;
    else out[k] = v;
  }
  return out;
}

/** A row the client left empty (an "add" they never filled in) isn't sent. */
export function isBlankRow(kind: ListKind, row: ListRow): boolean {
  const has = (k: string) => typeof row[k] === 'string' && (row[k] as string).trim() !== '';
  switch (kind) {
    case 'timings':
      return !has('at_time') && !has('activity') && !has('label');
    case 'security':
      return !has('name') && !has('start_time') && !has('end_time') && !has('notes');
    default:
      return !has('kind'); // entertainment and A/V rows are choices of a kind
  }
}

/**
 * Whether a time belongs to the next day: the event runs past midnight and
 * the time falls after it (00:30 at a 19:00–01:00 event).
 */
export function afterMidnight(time: string | null | undefined, event: { start_time: string | null; ends_next_day: boolean }): boolean {
  if (!time || !event.start_time || !event.ends_next_day) return false;
  return time.slice(0, 5) < event.start_time.slice(0, 5);
}

/** A window ending at or before it starts finishes the next day (21:00–02:00). */
export function windowNextDay(start: string | null | undefined, end: string | null | undefined): boolean {
  return !!start && !!end && end.slice(0, 5) <= start.slice(0, 5);
}

// ── Draft ─────────────────────────────────────────────────────────────────

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));

export function draftFrom(payload: PortalPayload): Draft {
  const fields: Record<string, FieldValue> = {};
  for (const key of payload.editable) fields[key] = toDraft(kindOf(key), payload.event[key]);
  const quantities: Record<string, Record<string, number>> = {};
  for (const menu of payload.menus ?? []) {
    quantities[menu.id] = {};
    for (const item of menu.items) if (item.kind !== 'header') quantities[menu.id][item.id] = item.quantity ?? 0;
  }
  return {
    fields,
    contact: {
      name: payload.contact.name ?? '',
      phone: payload.contact.phone ?? '',
      company: payload.contact.company ?? '',
    },
    lists: clone(payload.lists ?? {}),
    quantities,
  };
}

export function emptyDirty(): Dirty {
  return { fields: [], contact: [], lists: [], menus: {} };
}

export function isClean(d: Dirty | null): boolean {
  return !d || (!d.fields.length && !d.contact.length && !d.lists.length && !Object.values(d.menus).some((ids) => ids.length));
}

const union = <T,>(a: T[], b: T[]): T[] => [...a, ...b.filter((x) => !a.includes(x))];

export function mergeDirty(a: Dirty | null, b: Dirty | null): Dirty {
  const out = emptyDirty();
  for (const d of [a, b]) {
    if (!d) continue;
    out.fields = union(out.fields, d.fields);
    out.contact = union(out.contact, d.contact);
    out.lists = union(out.lists, d.lists);
    for (const [menu, ids] of Object.entries(d.menus)) out.menus[menu] = union(out.menus[menu] ?? [], ids);
  }
  return out;
}

export function initialState(payload: PortalPayload): PortalState {
  return {
    server: payload,
    draft: draftFrom(payload),
    dirty: emptyDirty(),
    sending: null,
    save: 'idle',
    error: null,
    invalid: {},
    overridden: [],
  };
}

/** A fresh draft from the server, with the keys in `keep` taken from `draft`. */
function overlay(fresh: Draft, draft: Draft, keep: Dirty, payload: PortalPayload): Draft {
  const out = clone(fresh);
  for (const key of keep.fields) if (payload.editable.includes(key) && key in draft.fields) out.fields[key] = draft.fields[key];
  for (const key of keep.contact) out.contact[key] = draft.contact[key];
  for (const kind of keep.lists) if (payload.lists?.[kind] && draft.lists[kind]) out.lists[kind] = clone(draft.lists[kind]!);
  for (const [menu, ids] of Object.entries(keep.menus)) {
    if (!out.quantities[menu]) continue;
    for (const id of ids) if (id in out.quantities[menu] && draft.quantities[menu]?.[id] !== undefined) out.quantities[menu][id] = draft.quantities[menu][id];
  }
  return out;
}

/** Drop dirty keys the latest payload no longer lets the client change. */
function stillEditable(d: Dirty, payload: PortalPayload): Dirty {
  const menus: Record<string, string[]> = {};
  for (const menu of payload.menus ?? []) {
    const ids = (d.menus[menu.id] ?? []).filter((id) => menu.items.some((i) => i.id === id && i.kind !== 'header'));
    if (ids.length) menus[menu.id] = ids;
  }
  return {
    fields: d.fields.filter((k) => payload.editable.includes(k)),
    contact: d.contact,
    lists: d.lists.filter((k) => !!payload.lists?.[k]),
    menus,
  };
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

function itemQty(menus: PortalMenu[] | undefined, menuId: string, itemId: string): number | undefined {
  return menus?.find((m) => m.id === menuId)?.items.find((i) => i.id === itemId)?.quantity;
}

/**
 * New server truth (`theirs`) arriving while the client has unsaved edits
 * made against `base`. Keeps each edit the venue didn't also change.
 */
export function merge3(base: PortalPayload, theirs: PortalPayload, draft: Draft, dirty: Dirty) {
  const pending = stillEditable(dirty, theirs);
  const keep = emptyDirty();
  const overridden: string[] = [];
  for (const key of pending.fields) {
    const kind = kindOf(key);
    if (same(toDraft(kind, base.event[key]), toDraft(kind, theirs.event[key]))) keep.fields.push(key);
    else overridden.push(key);
  }
  for (const key of pending.contact) {
    if ((base.contact[key] ?? '') === (theirs.contact[key] ?? '')) keep.contact.push(key);
    else overridden.push(`contact.${key}`);
  }
  for (const kind of pending.lists) {
    if (same(base.lists?.[kind] ?? [], theirs.lists?.[kind] ?? [])) keep.lists.push(kind);
    else overridden.push(kind);
  }
  for (const [menu, ids] of Object.entries(pending.menus)) {
    for (const id of ids) {
      if (itemQty(base.menus, menu, id) === itemQty(theirs.menus, menu, id)) (keep.menus[menu] ??= []).push(id);
      else overridden.push(`menu:${menu}:${id}`);
    }
  }
  return { draft: overlay(draftFrom(theirs), draft, keep, theirs), dirty: keep, overridden };
}

// ── Saving ────────────────────────────────────────────────────────────────

export interface Changes {
  expected_version: number;
  fields?: Record<string, string | number | boolean | null>;
  contact?: Partial<Record<ContactKey, string>>;
  lists?: Partial<Record<ListKind, ListRow[]>>;
  menus?: { id: string; expected_version: number; quantities: Record<string, number> }[];
}

/** The PUT body for the dirty keys (invalid fields are never dirty). */
export function buildChanges(state: Pick<PortalState, 'server' | 'draft'>, dirty: Dirty): Changes {
  const { server, draft } = state;
  const body: Changes = { expected_version: server.event.version };
  for (const key of dirty.fields) {
    const converted = toApi(kindOf(key), draft.fields[key]);
    if (!converted.ok) continue;
    (body.fields ??= {})[key] = converted.value;
  }
  for (const key of dirty.contact) (body.contact ??= {})[key] = draft.contact[key];
  for (const kind of dirty.lists) {
    const rows = (draft.lists[kind] ?? []).filter((r) => !isBlankRow(kind, r)).map(rowForApi);
    (body.lists ??= {})[kind] = rows;
  }
  for (const [menuId, ids] of Object.entries(dirty.menus)) {
    const menu = server.menus?.find((m) => m.id === menuId);
    if (!menu || !ids.length) continue;
    const quantities: Record<string, number> = {};
    for (const id of ids) quantities[id] = Math.max(0, Math.round(draft.quantities[menuId]?.[id] ?? 0));
    (body.menus ??= []).push({ id: menuId, expected_version: menu.version, quantities });
  }
  return body;
}

/** One menu's value from the draft's quantities (money.menu_food_cents). */
export function menuValueCents(menu: PortalMenu, quantities: Record<string, number> | undefined): number {
  let total = menu.is_set_menu ? (menu.price_per_person_cents ?? 0) * (menu.guest_count ?? 0) : 0;
  for (const item of menu.items) {
    if (item.kind === 'header' || item.unit_price_cents === null) continue;
    total += item.unit_price_cents * (quantities?.[item.id] ?? item.quantity ?? 0);
  }
  return total;
}

/** Readable names for dirty / overridden keys ("Guests", "Run sheet", "Canapés: Arancini"). */
export function describeKeys(keys: string[], payload: PortalPayload, labels: { field: (k: string) => string; list: (k: ListKind) => string }): string[] {
  return keys.map((key) => {
    if (key.startsWith('menu:')) {
      const [, menuId, itemId] = key.split(':');
      const menu = payload.menus?.find((m) => m.id === menuId);
      const item = menu?.items.find((i) => i.id === itemId);
      return menu && item ? `${menu.name}: ${item.name}` : 'A menu quantity';
    }
    if (['timings', 'security', 'entertainment', 'av'].includes(key)) return labels.list(key as ListKind);
    return labels.field(key);
  });
}

/** The dirty keys as describeKeys input. */
export function dirtyKeys(d: Dirty): string[] {
  return [
    ...d.fields,
    ...d.contact.map((k) => `contact.${k}`),
    ...d.lists,
    ...Object.entries(d.menus).flatMap(([menu, ids]) => ids.map((id) => `menu:${menu}:${id}`)),
  ];
}

// ── Reducer ───────────────────────────────────────────────────────────────

function edited(state: PortalState, draft: Draft, dirty: Dirty, invalid = state.invalid): PortalState {
  if (state.save === 'conflict') return { ...state, draft, dirty, invalid };
  const save: SaveState = state.sending ? 'saving' : !isClean(dirty) ? 'pending' : state.save === 'saved' ? 'saved' : 'idle';
  return { ...state, draft, dirty, invalid, save, error: null };
}

/** The keys a fresh draft must not reset: unsaved edits, and values still being corrected. */
function keepKeys(dirty: Dirty, invalid: Record<string, string>): Dirty {
  return { ...dirty, fields: union(dirty.fields, Object.keys(invalid)) };
}

export function reducer(state: PortalState, action: Action): PortalState {
  switch (action.type) {
    case 'loaded':
      return initialState(action.payload);

    case 'field': {
      const draft = { ...state.draft, fields: { ...state.draft.fields, [action.key]: action.value } };
      const converted = toApi(kindOf(action.key), action.value);
      const invalid = { ...state.invalid };
      const dirty = { ...state.dirty, fields: state.dirty.fields.filter((k) => k !== action.key) };
      if (converted.ok) {
        delete invalid[action.key];
        dirty.fields.push(action.key);
      } else {
        invalid[action.key] = converted.error;
      }
      return edited(state, draft, dirty, invalid);
    }

    case 'contact': {
      const draft = { ...state.draft, contact: { ...state.draft.contact, [action.key]: action.value } };
      return edited(state, draft, { ...state.dirty, contact: union(state.dirty.contact, [action.key]) });
    }

    case 'list': {
      const draft = { ...state.draft, lists: { ...state.draft.lists, [action.kind]: action.rows } };
      return edited(state, draft, { ...state.dirty, lists: union(state.dirty.lists, [action.kind]) });
    }

    case 'quantity': {
      const value = Math.max(0, Math.min(100_000, Math.round(action.value || 0)));
      const quantities = { ...state.draft.quantities, [action.menuId]: { ...state.draft.quantities[action.menuId], [action.itemId]: value } };
      const menus = { ...state.dirty.menus, [action.menuId]: union(state.dirty.menus[action.menuId] ?? [], [action.itemId]) };
      return edited(state, { ...state.draft, quantities }, { ...state.dirty, menus });
    }

    case 'saveStart':
      return { ...state, sending: state.dirty, dirty: emptyDirty(), save: 'saving', error: null };

    case 'saveDone': {
      // The response already holds what we sent; keys edited since stay ours.
      const dirty = stillEditable(state.dirty, action.payload);
      return {
        ...state,
        server: action.payload,
        draft: overlay(draftFrom(action.payload), state.draft, keepKeys(dirty, state.invalid), action.payload),
        dirty,
        sending: null,
        save: isClean(dirty) ? 'saved' : 'pending',
        error: null,
      };
    }

    case 'saveFailed': {
      if (action.outcome === 'rejected') {
        // Undo what was refused; keep anything typed since.
        return {
          ...state,
          draft: overlay(draftFrom(state.server), state.draft, keepKeys(state.dirty, state.invalid), state.server),
          sending: null,
          save: 'rejected',
          error: action.message,
        };
      }
      return { ...state, dirty: mergeDirty(state.sending, state.dirty), sending: null, save: action.outcome, error: action.message };
    }

    case 'refreshed': {
      const pending = mergeDirty(state.sending, state.dirty);
      const merged = merge3(state.server, action.payload, state.draft, pending);
      return {
        ...state,
        server: action.payload,
        draft: merged.draft,
        dirty: merged.dirty,
        sending: null,
        // A half-typed value is dropped for the fresh one (and its message with it).
        invalid: {},
        save: isClean(merged.dirty) ? 'idle' : 'pending',
        error: null,
        overridden: merged.overridden,
      };
    }

    case 'dismissOverridden':
      return { ...state, overridden: [] };

    default:
      return state;
  }
}
