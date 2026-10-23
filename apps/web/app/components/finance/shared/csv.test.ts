import { describe, expect, it } from 'vitest';
import { csvCell, csvFilename, toCsv } from './csv';

describe('csvCell', () => {
  it('quotes commas, quotes and line breaks', () => {
    expect(csvCell('La Zeppa')).toBe('La Zeppa');
    expect(csvCell('Smith, Jane')).toBe('"Smith, Jane"');
    expect(csvCell('say "hi"')).toBe('"say ""hi"""');
    expect(csvCell('two\nlines')).toBe('"two\nlines"');
  });
  it('keeps numbers bare, including negatives, and defuses formulas', () => {
    expect(csvCell(-12.5)).toBe('-12.5');
    expect(csvCell('-3')).toBe('-3');
    expect(csvCell('=SUM(A1)')).toBe("'=SUM(A1)");
    expect(csvCell('+1 more')).toBe("'+1 more");
  });
  it('writes nothing for null and yes/no for booleans', () => {
    expect(csvCell(null)).toBe('');
    expect(csvCell(undefined)).toBe('');
    expect(csvCell(true)).toBe('yes');
  });
});

describe('toCsv', () => {
  it('joins with CRLF and a BOM', () => {
    expect(toCsv(['a', 'b'], [[1, 'x'], [2, 'y,z']])).toBe('﻿a,b\r\n1,x\r\n2,"y,z"\r\n');
  });
});

describe('csvFilename', () => {
  it('slugs the table name', () => {
    expect(csvFilename('Payrun WE 18 Oct 2026')).toBe('payrun-we-18-oct-2026.csv');
    expect(csvFilename('')).toBe('table.csv');
  });
});
