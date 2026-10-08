'use client';

import type { DisplayBlockProps } from './DisplayBlockRenderer';

interface ColumnDef {
  key: string;
  label: string;
  align?: 'left' | 'right' | 'center';
}

// UUID pattern for filtering out internal ID columns
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function isInternalId(key: string, value: unknown): boolean {
  if (typeof value === 'string' && UUID_RE.test(value)) return true;
  if (key.endsWith('Id') && typeof value === 'string' && UUID_RE.test(value)) return true;
  return false;
}

function autoColumns(rows: Record<string, unknown>[]): ColumnDef[] {
  if (rows.length === 0) return [];
  const first = rows[0];
  return Object.keys(first)
    .filter(key => !rows.every(row => isInternalId(key, row[key])))
    .map(key => ({
      key,
      label: key
        .replace(/([A-Z])/g, ' $1')
        .replace(/[_-]/g, ' ')
        .replace(/^\w/, c => c.toUpperCase())
        .trim(),
    }));
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

export default function GenericTable({ data, props }: DisplayBlockProps) {
  // Extract rows — data might be the array directly or nested under a key
  let rows: Record<string, unknown>[] = [];
  if (Array.isArray(data)) {
    rows = data;
  } else if (data && typeof data === 'object') {
    // Find the first array value in the data object
    for (const val of Object.values(data)) {
      if (Array.isArray(val) && val.length > 0 && typeof val[0] === 'object') {
        rows = val;
        break;
      }
    }
  }

  if (rows.length === 0) return null;

  const columns: ColumnDef[] = (props?.columns as ColumnDef[]) || autoColumns(rows);
  const title = props?.title as string | undefined;

  return (
    <div style={{ marginBottom: '0.75rem' }}>
      {title && <div className="n-eyebrow" style={{ marginBottom: 6 }}>{title}</div>}
      {/* White "paper" with its own edge: it reads on the cream conversation
          and on Claude's background alike. */}
      <div className="n-card" style={{ overflowX: 'auto' }}>
        <table className="n-table">
          <thead>
            <tr>
              {columns.map(col => (
                <th key={col.key} scope="col" style={{ textAlign: (col.align || 'left') as 'left' | 'right' | 'center' }}>
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, ri) => (
              <tr key={ri}>
                {columns.map(col => (
                  <td key={col.key} style={{ textAlign: (col.align || 'left') as 'left' | 'right' | 'center', color: 'var(--text)' }}>
                    {formatCell(row[col.key])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
