'use client';

import { useMemo, useState } from 'react';
import { apiFetch } from '../../lib/api';
import type { DisplayBlockProps } from './DisplayBlockRenderer';

interface Fix {
  id: string;
  type: 'link_po' | 'unit';
  reference?: string;
  summary: string;
  po_number?: string;
  description?: string;
  current_unit?: string;
  proposed_unit?: string;
}

interface FixResult {
  id: string;
  ok: boolean;
  message: string;
}

const BADGE: Record<string, { label: string; bg: string; fg: string }> = {
  link_po: { label: 'Link PO', bg: '#eaf2fb', fg: '#2f6bbf' },
  unit: { label: 'Unit + variant', bg: '#f3eefb', fg: '#7a52b3' },
};

export default function InvoiceFixesCard({ data, props }: DisplayBlockProps) {
  const fixes = useMemo(() => (data.fixes as Fix[]) || [], [data.fixes]);
  const venueId =
    (props?.activeVenueId as string) ||
    (data.venue_id as string) ||
    (data.venueId as string) ||
    undefined;

  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(fixes.map((f) => f.id)),
  );
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<Record<string, FixResult>>({});
  const [error, setError] = useState<string | null>(null);

  if (fixes.length === 0) return null;

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const done = Object.keys(results).length > 0;
  const appliedOk = Object.values(results).filter((r) => r.ok).length;

  const apply = async () => {
    setLoading(true);
    setError(null);
    try {
      const chosen = fixes.filter((f) => selected.has(f.id));
      const res = await apiFetch('/api/invoice-fixes/apply', {
        method: 'POST',
        body: JSON.stringify({ venue_id: venueId, fixes: chosen }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({ detail: `Error ${res.status}` }));
        throw new Error(b.detail || `Error ${res.status}`);
      }
      const body = (await res.json()) as { results: FixResult[] };
      const map: Record<string, FixResult> = {};
      for (const r of body.results) map[r.id] = r;
      setResults(map);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to apply fixes');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        border: '1px solid #e2ddd7',
        borderRadius: 10,
        backgroundColor: '#faf8f5',
        padding: '0.85rem 1rem',
        marginTop: '0.5rem',
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: '0.6rem',
        }}
      >
        <span style={{ fontSize: '0.82rem', fontWeight: 600, color: '#5a4a3a' }}>
          Proposed fixes
        </span>
        <span style={{ fontSize: '0.68rem', color: '#a08060' }}>
          {done
            ? `${appliedOk}/${fixes.length} applied`
            : `${selected.size} of ${fixes.length} selected`}
        </span>
      </div>

      {fixes.map((f) => {
        const r = results[f.id];
        const badge = BADGE[f.type] || { label: f.type, bg: '#eee', fg: '#666' };
        return (
          <label
            key={f.id}
            style={{
              display: 'flex',
              gap: '0.6rem',
              alignItems: 'flex-start',
              backgroundColor: '#fff',
              border: '1px solid #f0ebe5',
              borderRadius: 8,
              padding: '0.55rem 0.7rem',
              marginBottom: '0.4rem',
              cursor: done ? 'default' : 'pointer',
            }}
          >
            {!done && (
              <input
                type="checkbox"
                checked={selected.has(f.id)}
                onChange={() => toggle(f.id)}
                style={{ marginTop: 3 }}
              />
            )}
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: '0.78rem', color: '#333', marginBottom: 3 }}>
                {f.summary}
              </div>
              <span
                style={{
                  fontSize: '0.6rem',
                  fontWeight: 600,
                  padding: '1px 6px',
                  borderRadius: 3,
                  backgroundColor: badge.bg,
                  color: badge.fg,
                }}
              >
                {badge.label}
              </span>
            </div>
            {r && (
              <span
                style={{
                  fontSize: '0.68rem',
                  fontWeight: 600,
                  color: r.ok ? '#2e7d4f' : '#c0392b',
                  maxWidth: 220,
                  textAlign: 'right',
                }}
                title={r.message}
              >
                {r.ok ? `✓ ${r.message}` : `✗ ${r.message}`}
              </span>
            )}
          </label>
        );
      })}

      {error && (
        <div style={{ fontSize: '0.72rem', color: '#c0392b', margin: '0.3rem 0' }}>
          {error}
        </div>
      )}

      {done ? (
        <div style={{ fontSize: '0.72rem', color: '#888', marginTop: '0.4rem' }}>
          Re-run &ldquo;review the outstanding invoices&rdquo; to receive any that now
          pass.
        </div>
      ) : (
        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            marginTop: '0.5rem',
          }}
        >
          <button
            onClick={apply}
            disabled={loading || selected.size === 0}
            style={{
              padding: '0.35rem 1.1rem',
              fontSize: '0.75rem',
              fontWeight: 500,
              border: 'none',
              borderRadius: 6,
              cursor: loading || selected.size === 0 ? 'not-allowed' : 'pointer',
              backgroundColor: '#a08060',
              color: '#fff',
              fontFamily: 'inherit',
              opacity: loading || selected.size === 0 ? 0.6 : 1,
            }}
          >
            {loading ? 'Applying…' : `Apply ${selected.size} fix${selected.size === 1 ? '' : 'es'}`}
          </button>
        </div>
      )}
    </div>
  );
}
