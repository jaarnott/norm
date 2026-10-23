/**
 * CSV of any grid (plan D21: no XLSX or PDF in v1 — a CSV download of every
 * table instead). Pure, so the quoting is tested; the download is the one
 * browser call at the end.
 */

export type CsvValue = string | number | boolean | null | undefined;

/** One cell: quoted when it holds a comma, a quote, or a line break; a
 *  leading =, +, -, @ is prefixed with a quote so a spreadsheet never runs
 *  it as a formula. */
export function csvCell(value: CsvValue): string {
  if (value === null || value === undefined) return '';
  let text = typeof value === 'boolean' ? (value ? 'yes' : 'no') : String(value);
  if (/^[=+\-@\t\r]/.test(text) && !/^-?\d+(\.\d+)?$/.test(text)) text = `'${text}`;
  if (/[",\r\n]/.test(text)) text = `"${text.replace(/"/g, '""')}"`;
  return text;
}

/** Rows of cells → CSV text with CRLF line ends and a UTF-8 BOM so Excel
 *  opens it as UTF-8. */
export function toCsv(header: CsvValue[], rows: CsvValue[][]): string {
  const lines = [header, ...rows].map((r) => r.map(csvCell).join(','));
  return `﻿${lines.join('\r\n')}\r\n`;
}

/** "Payrun WE 18 Oct 2026" → "payrun-we-18-oct-2026.csv". */
export function csvFilename(name: string): string {
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return `${slug || 'table'}.csv`;
}

/** Hand the text to the browser as a file. */
export function downloadText(filename: string, text: string, type = 'text/csv;charset=utf-8'): void {
  if (typeof document === 'undefined') return;
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
