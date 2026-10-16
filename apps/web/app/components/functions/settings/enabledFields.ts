/**
 * The enabled-fields matrix: which event fields each booking type uses at a
 * venue (one flag per venue × booking type × field). The API stores only
 * overrides and reads a key that isn't set as ON (venue_profile.
 * enabled_fields_for), so a save sends just the switched-off fields.
 */
import type { EnabledFields } from './types';

export interface CatalogueEntry {
  section: string;
  key: string;
  label: string;
}

export interface FieldRow extends CatalogueEntry {
  /** 0 for a field, 1 for a sub-field (`details.av.in_house_mic` under `details.av`). */
  depth: number;
  parent: string | null;
}

export interface FieldGroup {
  section: string;
  title: string;
  rows: FieldRow[];
}

const SECTION_TITLES: Record<string, string> = {
  details: 'Details',
  beverages: 'Beverages',
  food: 'Food',
  timings: 'Timings',
};

/** The catalogue grouped by section, in catalogue order, sub-fields marked. */
export function fieldGroups(catalogue: CatalogueEntry[]): FieldGroup[] {
  const keys = new Set(catalogue.map((c) => c.key));
  const groups: FieldGroup[] = [];
  for (const entry of catalogue) {
    let group = groups.find((g) => g.section === entry.section);
    if (!group) {
      group = {
        section: entry.section,
        title: SECTION_TITLES[entry.section] ?? entry.section.charAt(0).toUpperCase() + entry.section.slice(1),
        rows: [],
      };
      groups.push(group);
    }
    const parts = entry.key.split('.');
    const parentKey = parts.length > 2 ? parts.slice(0, -1).join('.') : null;
    const parent = parentKey && keys.has(parentKey) ? parentKey : null;
    group.rows.push({ ...entry, depth: parent ? 1 : 0, parent });
  }
  return groups;
}

/** Every booking type × key as a boolean (missing = on). */
export function fullMatrix(stored: EnabledFields | null | undefined, bookingTypes: string[], keys: string[]): EnabledFields {
  const out: EnabledFields = {};
  for (const bt of bookingTypes) {
    const row = stored?.[bt] ?? {};
    out[bt] = {};
    for (const key of keys) out[bt][key] = row[key] !== false;
  }
  return out;
}

/** What to store: only the fields switched off (anything not stored is on). */
export function overridesOf(matrix: EnabledFields): EnabledFields {
  const out: EnabledFields = {};
  for (const [bt, row] of Object.entries(matrix)) {
    const off = Object.entries(row).filter(([, on]) => on === false);
    if (off.length) out[bt] = Object.fromEntries(off.map(([key]) => [key, false]));
  }
  return out;
}

export function sameMatrix(a: EnabledFields, b: EnabledFields): boolean {
  const bts = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const bt of bts) {
    const ra = a[bt] ?? {};
    const rb = b[bt] ?? {};
    const keys = new Set([...Object.keys(ra), ...Object.keys(rb)]);
    for (const k of keys) if ((ra[k] !== false) !== (rb[k] !== false)) return false;
  }
  return true;
}

/** Column headings short enough for a phone. */
export const SHORT_BOOKING_LABELS: Record<string, string> = {
  seated_dining: 'Seated',
  group_booking: 'Group',
  large_event_partial: 'Partial',
  large_event_exclusive: 'Exclusive',
};
