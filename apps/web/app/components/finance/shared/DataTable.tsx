'use client';

/**
 * The table every finance page uses: sortable headers, a sticky header row,
 * sideways scroll inside the table (never the page — the phone rule), an
 * optional totals row, and a CSV download of exactly what is shown (plan
 * D21). Rows can be clickable (a term's rows, a person's shifts).
 */
import { useMemo, useState, type ReactNode } from 'react';
import { ArrowDown, ArrowUp, ArrowUpDown, Download } from 'lucide-react';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import PageState from '../../ui/PageState';
import { useBreakpoint } from '../../../hooks/useBreakpoint';
import { csvFilename, downloadText, toCsv } from './csv';
import { columnValue, csvRows, nextSort, sortRows, type Column, type SortState } from './table';

export type { Column } from './table';

export default function DataTable<T>({ columns, rows, rowKey, csvName, initialSort, emptyTitle = 'Nothing here.', emptyDetail, footer, onRowClick, selectedKey, maxHeight, dense = false, caption, toolbar, testId, rowTone }: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  /** Shows the CSV button, named after this ("Payrun WE 18 Oct 2026"). */
  csvName?: string;
  initialSort?: SortState | null;
  emptyTitle?: string;
  emptyDetail?: ReactNode;
  /** A totals row: cells in column order (null = blank). */
  footer?: ReactNode[];
  onRowClick?: (row: T) => void;
  selectedKey?: string | null;
  /** Scroll inside the table past this height (the page keeps its length). */
  maxHeight?: number | string;
  dense?: boolean;
  caption?: string;
  /** Extra controls beside the CSV button (a filter chip row). */
  toolbar?: ReactNode;
  testId?: string;
  /** A tint for a row that needs a look. */
  rowTone?: (row: T) => 'warn' | 'error' | 'muted' | undefined;
}) {
  const [sort, setSort] = useState<SortState | null>(initialSort ?? null);
  const { isMobile } = useBreakpoint();
  const shown = useMemo(() => columns.filter((c) => !(isMobile && c.hideOnPhone)), [columns, isMobile]);
  const sorted = useMemo(() => sortRows(rows, columns, sort), [rows, columns, sort]);

  const download = () => {
    const { header, rows: body } = csvRows(columns, sorted);
    downloadText(csvFilename(csvName || 'table'), toCsv(header, body));
  };

  const pad = dense ? '6px 10px' : undefined;
  const hasToolbar = !!csvName || !!toolbar;

  return (
    <div style={{ minWidth: 0 }} data-testid={testId}>
      {hasToolbar && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', minWidth: 0 }}>{toolbar}</div>
          {csvName && rows.length > 0 && (
            <Button size="xs" variant="quiet" icon={Download} onClick={download} title="Download this table as a CSV file">CSV</Button>
          )}
        </div>
      )}
      {rows.length === 0 ? (
        <PageState kind="empty" title={emptyTitle} detail={emptyDetail} />
      ) : (
        <div style={{ overflowX: 'auto', overflowY: maxHeight ? 'auto' : undefined, maxHeight, WebkitOverflowScrolling: 'touch', borderRadius: 'var(--radius)' }}>
          <table className="n-table" style={{ width: '100%', minWidth: shown.length > 4 ? 560 : undefined }}>
            {caption && <caption style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>{caption}</caption>}
            <thead>
              <tr>
                {shown.map((c) => {
                  const sortable = c.sortable !== false;
                  const active = sort?.key === c.key;
                  const align = c.align ?? 'left';
                  const content = (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, justifyContent: align === 'right' ? 'flex-end' : undefined }}>
                      {c.label}
                      {sortable && (
                        <Icon icon={active ? (sort!.dir === 'asc' ? ArrowUp : ArrowDown) : ArrowUpDown} size="meta" tone={active ? 'strong' : 'muted'} />
                      )}
                    </span>
                  );
                  return (
                    <th
                      key={c.key}
                      className={align === 'right' ? 'num' : undefined}
                      style={{ position: 'sticky', top: 0, zIndex: 1, width: c.width, textAlign: align, padding: pad }}
                      aria-sort={active ? (sort!.dir === 'asc' ? 'ascending' : 'descending') : undefined}
                    >
                      {sortable ? (
                        <button
                          type="button"
                          onClick={() => setSort((s) => nextSort(s, c.key))}
                          style={{ background: 'none', border: 'none', padding: 0, font: 'inherit', color: 'inherit', cursor: 'pointer', width: '100%', textAlign: align }}
                        >
                          {content}
                        </button>
                      ) : content}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {sorted.map((row) => {
                const key = rowKey(row);
                const selected = selectedKey === key;
                const tone = rowTone?.(row);
                const bg = selected ? 'var(--selected)' : tone === 'warn' ? 'var(--warn-bg)' : tone === 'error' ? 'var(--error-bg)' : undefined;
                return (
                  <tr
                    key={key}
                    onClick={onRowClick ? () => onRowClick(row) : undefined}
                    style={{ cursor: onRowClick ? 'pointer' : undefined, opacity: tone === 'muted' ? 0.6 : undefined }}
                    aria-selected={onRowClick ? selected : undefined}
                  >
                    {shown.map((c) => {
                      const v = c.render ? c.render(row) : columnValue(c, row);
                      return (
                        <td key={c.key} className={c.align === 'right' ? 'num' : undefined} style={{ textAlign: c.align, padding: pad, background: bg, whiteSpace: c.nowrap ? 'nowrap' : undefined }}>
                          {v === null || v === undefined || v === '' ? <span style={{ color: 'var(--muted)' }}>—</span> : typeof v === 'boolean' ? (v ? 'Yes' : 'No') : v}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
            {footer && (
              <tfoot>
                <tr>
                  {shown.map((c, i) => {
                    const idx = columns.indexOf(c);
                    return (
                      <td key={c.key} className={c.align === 'right' ? 'num' : undefined} style={{ textAlign: c.align, padding: pad, fontWeight: 600, borderTop: '1px solid var(--line-strong)', borderBottom: 'none', background: 'var(--surface)' }}>
                        {footer[idx] ?? (i === 0 ? 'Total' : null)}
                      </td>
                    );
                  })}
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      )}
    </div>
  );
}
