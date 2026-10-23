import { describe, expect, it } from 'vitest';
import { csvRows, nextSort, sortRows, type Column } from './table';

interface Row { name: string; hours: number | null; cents: number }

const rows: Row[] = [
  { name: 'Smith, Jane', hours: 12.5, cents: 40000 },
  { name: 'Brown, Al', hours: null, cents: 1500 },
  { name: 'adams, zoe', hours: 8, cents: 20000 },
];

const columns: Column<Row>[] = [
  { key: 'name', label: 'Name' },
  { key: 'hours', label: 'Hours', align: 'right' },
  { key: 'cents', label: 'Pay', align: 'right', csv: (r) => r.cents / 100, csvLabel: 'Pay ($)' },
];

describe('sortRows', () => {
  it('sorts text case-insensitively and numbers numerically, blanks last', () => {
    expect(sortRows(rows, columns, { key: 'name', dir: 'asc' }).map((r) => r.name)).toEqual(['adams, zoe', 'Brown, Al', 'Smith, Jane']);
    expect(sortRows(rows, columns, { key: 'hours', dir: 'asc' }).map((r) => r.hours)).toEqual([8, 12.5, null]);
    expect(sortRows(rows, columns, { key: 'hours', dir: 'desc' }).map((r) => r.hours)).toEqual([12.5, 8, null]);
  });
  it('leaves the order alone without a sort', () => {
    expect(sortRows(rows, columns, null)).toBe(rows);
  });
});

describe('nextSort', () => {
  it('cycles ascending, descending, off', () => {
    expect(nextSort(null, 'name')).toEqual({ key: 'name', dir: 'asc' });
    expect(nextSort({ key: 'name', dir: 'asc' }, 'name')).toEqual({ key: 'name', dir: 'desc' });
    expect(nextSort({ key: 'name', dir: 'desc' }, 'name')).toBeNull();
    expect(nextSort({ key: 'name', dir: 'desc' }, 'hours')).toEqual({ key: 'hours', dir: 'asc' });
  });
});

describe('csvRows', () => {
  it('uses the csv override and label', () => {
    const out = csvRows(columns, rows.slice(0, 1));
    expect(out.header).toEqual(['Name', 'Hours', 'Pay ($)']);
    expect(out.rows).toEqual([['Smith, Jane', 12.5, 400]]);
  });
});
