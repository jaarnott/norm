/**
 * DataTable's logic — sorting and the CSV rows — kept pure so it is tested
 * without a DOM. A column's `value` is what it sorts and exports by; its
 * `render` is only how it looks.
 */
import type { ReactNode } from 'react';
import type { CsvValue } from './csv';

export interface Column<T> {
  key: string;
  label: ReactNode;
  /** Numbers and money sit right. */
  align?: 'left' | 'right' | 'center';
  width?: number | string;
  /** The cell's value — sorted and exported by this. Defaults to `row[key]`. */
  value?: (row: T) => CsvValue;
  /** How the cell looks; defaults to the value as text. */
  render?: (row: T) => ReactNode;
  /** What the CSV carries when it differs from `value` (cents → dollars). */
  csv?: (row: T) => CsvValue;
  /** The column header the CSV carries when the label is not plain text. */
  csvLabel?: string;
  sortable?: boolean;
  /** Hidden on narrow screens; the CSV still carries it. */
  hideOnPhone?: boolean;
  /** Never wrap this cell. */
  nowrap?: boolean;
}

export type SortDir = 'asc' | 'desc';

export interface SortState {
  key: string;
  dir: SortDir;
}

export function columnValue<T>(col: Column<T>, row: T): CsvValue {
  if (col.value) return col.value(row);
  const v = (row as Record<string, unknown>)[col.key];
  if (v === null || v === undefined) return null;
  if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') return v;
  return String(v);
}

function compare(a: CsvValue, b: CsvValue): number {
  const aNone = a === null || a === undefined || a === '';
  const bNone = b === null || b === undefined || b === '';
  if (aNone && bNone) return 0;
  if (aNone) return 1; // blanks last either way
  if (bNone) return -1;
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  if (typeof a === 'boolean' && typeof b === 'boolean') return Number(a) - Number(b);
  return String(a).localeCompare(String(b), 'en', { numeric: true, sensitivity: 'base' });
}

/** Rows in the sort's order; a stable sort, blanks last, untouched when no sort. */
export function sortRows<T>(rows: T[], columns: Column<T>[], sort: SortState | null): T[] {
  if (!sort) return rows;
  const col = columns.find((c) => c.key === sort.key);
  if (!col) return rows;
  const keyed = rows.map((row, i) => ({ row, i, v: columnValue(col, row) }));
  keyed.sort((x, y) => {
    const c = compare(x.v, y.v);
    if (c !== 0) {
      // Blanks stay last whichever way the sort goes.
      const xNone = x.v === null || x.v === undefined || x.v === '';
      const yNone = y.v === null || y.v === undefined || y.v === '';
      if (xNone || yNone) return c;
      return sort.dir === 'asc' ? c : -c;
    }
    return x.i - y.i;
  });
  return keyed.map((k) => k.row);
}

/** The next sort after a click on a header: asc → desc → off. */
export function nextSort(current: SortState | null, key: string): SortState | null {
  if (!current || current.key !== key) return { key, dir: 'asc' };
  if (current.dir === 'asc') return { key, dir: 'desc' };
  return null;
}

/** Header and rows for the CSV, in the order shown. */
export function csvRows<T>(columns: Column<T>[], rows: T[]): { header: CsvValue[]; rows: CsvValue[][] } {
  const header = columns.map((c) => c.csvLabel ?? (typeof c.label === 'string' ? c.label : c.key));
  const body = rows.map((row) => columns.map((c) => (c.csv ? c.csv(row) : columnValue(c, row))));
  return { header, rows: body };
}
