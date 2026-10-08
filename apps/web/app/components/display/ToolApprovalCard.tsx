'use client';

import { useState } from 'react';
import { CircleAlert, TriangleAlert } from 'lucide-react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import Button from '../ui/Button';
import Badge, { type BadgeTone } from '../ui/Badge';
import { getStoredUser } from '../../lib/api';
import {
  allowable,
  approveParams,
  cardLabel,
  declineParams,
  groupRows,
  rowState,
  type ApprovalRow,
  type Tone,
} from './approvalCard';

/** Rows a group shows before "Show all". */
const FIRST_ROWS = 8;

const BADGE_TONE: Record<Tone, BadgeTone> = {
  pending: 'accent',
  good: 'ok',
  bad: 'error',
  warn: 'warn',
  muted: 'neutral',
};

function StateBadge({ label, tone }: { label: string; tone: Tone }) {
  return <Badge tone={BADGE_TONE[tone]}>{label}</Badge>;
}

const LINE = '1px solid var(--line-soft)';

function Row({ row, cardStatus, pending, ticked, onTick, isAdmin }: {
  row: ApprovalRow;
  cardStatus: string;
  pending: boolean;
  ticked: boolean;
  onTick: () => void;
  isAdmin: boolean;
}) {
  const [details, setDetails] = useState(false);
  const p = row.preview;
  const state = rowState(row, cardStatus);
  const changes = p?.changes ?? [];

  return (
    <div style={{
      display: 'flex', gap: '0.6rem', alignItems: 'flex-start',
      padding: '0.6rem 0.9rem', borderTop: LINE,
      opacity: !pending && row.status === 'rejected' ? 0.6 : 1,
    }}>
      {pending && (
        <input
          type="checkbox"
          checked={ticked}
          onChange={onTick}
          aria-label={`Approve ${p?.target || row.summary}`}
          style={{ marginTop: 3, accentColor: 'var(--accent)', cursor: 'pointer' }}
        />
      )}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.4rem', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 'var(--fs-base)', fontWeight: 600, color: 'var(--text)' }}>
            {p?.target || row.summary}
          </span>
          {p?.venue && <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{p.venue}</span>}
          {state && <span style={{ marginLeft: 'auto' }}><StateBadge {...state} /></span>}
        </div>

        {changes.length > 0 && (
          <div style={{
            display: 'grid', gridTemplateColumns: 'minmax(90px, max-content) 1fr',
            columnGap: '0.75rem', rowGap: '0.2rem', marginTop: '0.35rem', fontSize: 'var(--fs-sm)',
          }}>
            {changes.map((c, i) => (
              <div key={i} style={{ display: 'contents' }}>
                <span style={{ color: 'var(--muted)', overflowWrap: 'anywhere' }}>{c.field}</span>
                <span style={{ color: 'var(--text)', overflowWrap: 'anywhere' }}>
                  {c.before !== undefined && (
                    <>
                      <span style={{ color: 'var(--muted)', textDecoration: 'line-through' }}>{c.before}</span>
                      <span aria-hidden style={{ color: 'var(--icon)', margin: '0 0.3rem' }}>→</span>
                    </>
                  )}
                  <span style={{ fontWeight: 500 }}>{c.after}</span>
                </span>
              </div>
            ))}
          </div>
        )}
        {p?.more_changes ? (
          <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginTop: '0.2rem' }}>
            and {p.more_changes} more change{p.more_changes === 1 ? '' : 's'}
          </div>
        ) : null}
        {p?.note && (
          <div style={{
            fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', marginTop: '0.3rem', fontStyle: 'italic',
            display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden',
          }}>
            {p.note}
          </div>
        )}
        {(p?.warnings ?? []).map((w, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 5, fontSize: 'var(--fs-sm)', color: 'var(--warn)', marginTop: '0.25rem' }}>
            <TriangleAlert size={13} aria-hidden style={{ flex: '0 0 auto', marginTop: 2 }} />
            <span>{w}</span>
          </div>
        ))}
        {row.outcome_note && row.outcome !== 'done' && (
          <div style={{ fontSize: 'var(--fs-sm)', color: row.outcome === 'failed' ? 'var(--error)' : 'var(--warn)', marginTop: '0.25rem' }}>
            {row.outcome_note}
          </div>
        )}

        {isAdmin && row.input_params && (
          <>
            <button
              onClick={() => setDetails(d => !d)}
              style={{
                background: 'none', border: 'none', cursor: 'pointer',
                fontSize: 'var(--fs-xs)', color: 'var(--muted)', padding: '0.3rem 0 0',
              }}
            >
              {details ? 'Hide technical details' : `Technical details · ${row.connector_name}.${row.action}`}
            </button>
            {details && (
              <pre style={{
                fontSize: 'var(--fs-xs)', fontFamily: 'var(--font-mono)', color: 'var(--text-soft)', backgroundColor: 'var(--surface)',
                padding: '0.5rem', borderRadius: 'var(--radius-sm)', marginTop: '0.3rem',
                overflow: 'auto', maxHeight: 250, lineHeight: 1.4,
                whiteSpace: 'pre-wrap', wordBreak: 'break-word', border: LINE,
              }}>
                {JSON.stringify(row.input_params, null, 2)}
              </pre>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default function ToolApprovalCard({ data, onAction }: DisplayBlockProps) {
  const rows = (data.tool_calls as ApprovalRow[]) || [];
  const status = (data.status as string) || 'pending';
  const threadId = data.thread_id as string;
  const pending = status === 'pending';
  const isAdmin = getStoredUser()?.role === 'admin';

  const [ticked, setTicked] = useState<Set<string>>(() => new Set(rows.map(r => r.id)));
  const [always, setAlways] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [declining, setDeclining] = useState(false);
  const [reason, setReason] = useState('');
  const [loading, setLoading] = useState(false);

  const groups = groupRows(rows);
  const count = rows.filter(r => ticked.has(r.id)).length;

  const toggle = (ids: string[], on: boolean) => {
    setTicked(prev => {
      const next = new Set(prev);
      ids.forEach(id => (on ? next.add(id) : next.delete(id)));
      return next;
    });
  };

  const approve = async () => {
    setLoading(true);
    await onAction?.({ connector_name: '_system', action: 'tool_approve', params: approveParams(threadId, rows, ticked, always) });
    setLoading(false);
  };

  const decline = async () => {
    setLoading(true);
    await onAction?.({ connector_name: '_system', action: 'tool_reject', params: declineParams(threadId, reason) });
    setLoading(false);
  };

  const label = cardLabel(status, rows, data.status_note as string | undefined);
  const tone: Tone = pending ? 'pending' : label === 'Declined' || label.startsWith('Not done') ? 'muted' : 'good';

  return (
    <div style={{
      border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)',
      backgroundColor: 'var(--bg)', marginTop: '0.5rem', overflow: 'hidden',
    }}>
      {pending ? (
        // The "needs you" band: tan, so a waiting card is found at a glance.
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', backgroundColor: 'var(--accent-soft)', color: 'var(--accent)' }}>
          <CircleAlert size={16} aria-hidden style={{ flex: '0 0 auto' }} />
          <span style={{ fontSize: 'var(--fs-sm)', fontWeight: 600 }}>{label}</span>
          <span style={{ marginLeft: 'auto', fontSize: 'var(--fs-xs)' }}>
            {rows.length} change{rows.length === 1 ? '' : 's'}
          </span>
        </div>
      ) : (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px' }}>
          <StateBadge label={label} tone={tone} />
          <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
            {rows.length} change{rows.length === 1 ? '' : 's'}
          </span>
        </div>
      )}

      {groups.map(group => {
        const ids = group.rows.map(r => r.id);
        const all = ids.every(id => ticked.has(id));
        const open = expanded.has(group.title);
        const shown = open ? group.rows : group.rows.slice(0, FIRST_ROWS);
        return (
          <div key={group.title} style={{ borderTop: LINE }}>
            <div style={{
              display: 'flex', alignItems: 'center', gap: '0.5rem',
              padding: '0.5rem 0.9rem', fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--text-soft)',
            }}>
              {pending && group.rows.length > 1 && (
                <input
                  type="checkbox"
                  checked={all}
                  onChange={() => toggle(ids, !all)}
                  aria-label={`Select all: ${group.title}`}
                  style={{ accentColor: 'var(--accent)', cursor: 'pointer' }}
                />
              )}
              <span>{group.title}</span>
              {group.rows.length > 1 && <span style={{ fontWeight: 400, color: 'var(--muted)' }}>{group.rows.length}</span>}
            </div>
            {shown.map(row => (
              <Row
                key={row.id}
                row={row}
                cardStatus={status}
                pending={pending}
                ticked={ticked.has(row.id)}
                onTick={() => toggle([row.id], !ticked.has(row.id))}
                isAdmin={isAdmin}
              />
            ))}
            {group.rows.length > FIRST_ROWS && (
              <button
                onClick={() => setExpanded(prev => {
                  const next = new Set(prev);
                  if (open) next.delete(group.title); else next.add(group.title);
                  return next;
                })}
                style={{
                  width: '100%', background: 'none', border: 'none', borderTop: LINE,
                  padding: '0.5rem', fontSize: 'var(--fs-sm)', fontWeight: 500, color: 'var(--accent)', cursor: 'pointer',
                }}
              >
                {open ? 'Show fewer' : `Show all ${group.rows.length}`}
              </button>
            )}
          </div>
        );
      })}

      {pending && !declining && allowable(rows).length > 0 && (
        <div style={{ padding: '0.65rem 0.9rem 0', borderTop: LINE }}>
          {allowable(rows).map(t => (
            <label key={t.key} style={{ display: 'flex', gap: '0.5rem', alignItems: 'baseline', fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', cursor: 'pointer', marginBottom: '0.3rem' }}>
              <input
                type="checkbox"
                checked={always.has(t.key)}
                onChange={() => setAlways(prev => {
                  const next = new Set(prev);
                  if (next.has(t.key)) next.delete(t.key); else next.add(t.key);
                  return next;
                })}
                style={{ accentColor: 'var(--accent)', cursor: 'pointer' }}
              />
              <span>Always allow Norm to {t.label} without asking</span>
            </label>
          ))}
          <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)', marginLeft: '1.4rem' }}>
            Just for you. Change it any time in Settings → Preferences.
          </div>
        </div>
      )}

      {pending && !declining && (
        <div style={{
          display: 'flex', justifyContent: 'flex-end', gap: '0.5rem',
          padding: '0.75rem 0.9rem', borderTop: allowable(rows).length > 0 ? 'none' : LINE,
        }}>
          <Button variant="secondary" onClick={() => setDeclining(true)} disabled={loading}>
            Decline
          </Button>
          <Button variant="primary" onClick={approve} disabled={loading || count === 0}>
            {loading ? '…' : count === rows.length ? (rows.length === 1 ? 'Approve' : 'Approve all') : `Approve ${count} of ${rows.length}`}
          </Button>
        </div>
      )}

      {pending && declining && (
        <div style={{ padding: '0.75rem 0.9rem', borderTop: LINE }}>
          <textarea
            className="n-input"
            aria-label="Reason for declining"
            value={reason}
            onChange={e => setReason(e.target.value)}
            placeholder="Why not? (optional — Norm reads this and learns from it)"
            rows={2}
            style={{ width: '100%' }}
          />
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem', marginTop: '0.5rem' }}>
            <Button variant="secondary" onClick={() => setDeclining(false)} disabled={loading}>
              Back
            </Button>
            <Button variant="danger" onClick={decline} disabled={loading}>
              {loading ? '…' : rows.length === 1 ? 'Decline' : 'Decline all'}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
