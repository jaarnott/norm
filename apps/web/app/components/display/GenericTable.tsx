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
      {title && (
        <div style={{
          fontSize: '0.72rem',
          fontWeight: 600,
          textTransform: 'uppercase',
          letterSpacing: '0.06em',
          color: '#888',
          marginBottom: '0.4rem',
        }}>
          {title}
        </div>
      )}
      <div style={{ overflowX: 'auto' }}>
        <table style={{
          width: '100%',
          borderCollapse: 'collapse',
          fontSize: '0.82rem',
          lineHeight: 1.5,
        }}>
          <thead>
            <tr>
              {columns.map(col => (
                <th key={col.key} style={{
                  textAlign: (col.align || 'left') as 'left' | 'right' | 'center',
                  padding: '0.5rem 0.75rem',
                  borderBottom: '2px solid #e2e8f0',
                  fontWeight: 600,
                  color: '#555',
                  whiteSpace: 'nowrap',
                }}>
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, ri) => (
              <tr key={ri} style={{
                backgroundColor: ri % 2 === 1 ? '#f8f9fa' : 'transparent',
              }}>
                {columns.map(col => (
                  <td key={col.key} style={{
                    textAlign: (col.align || 'left') as 'left' | 'right' | 'center',
                    padding: '0.45rem 0.75rem',
                    borderBottom: '1px solid #eee',
                    color: '#333',
                  }}>
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
