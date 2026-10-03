'use client';

import { useState, type CSSProperties } from 'react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
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

const TONES: Record<Tone, { bg: string; fg: string }> = {
  pending: { bg: '#f5f0ea', fg: '#a08060' },
  good: { bg: '#d4edda', fg: '#155724' },
  bad: { bg: '#f8d7da', fg: '#842029' },
  warn: { bg: '#fff3cd', fg: '#7a5b00' },
  muted: { bg: '#e2e3e5', fg: '#666' },
};

function Badge({ label, tone }: { label: string; tone: Tone }) {
  return (
    <span style={{
      fontSize: '0.65rem', fontWeight: 600, padding: '2px 8px', borderRadius: 4,
      backgroundColor: TONES[tone].bg, color: TONES[tone].fg, whiteSpace: 'nowrap',
    }}>
      {label}
    </span>
  );
}

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
      display: 'flex', gap: '0.55rem', alignItems: 'flex-start',
      padding: '0.55rem 0.7rem', borderTop: '1px solid #f0ebe5',
      opacity: !pending && row.status === 'rejected' ? 0.6 : 1,
    }}>
      {pending && (
        <input
          type="checkbox"
          checked={ticked}
          onChange={onTick}
          aria-label={`Approve ${p?.target || row.summary}`}
          style={{ marginTop: 3, accentColor: '#a08060', cursor: 'pointer' }}
        />
      )}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.4rem', flexWrap: 'wrap' }}>
          <span style={{ fontSize: '0.8rem', fontWeight: 600, color: '#333' }}>
            {p?.target || row.summary}
          </span>
          {p?.venue && <span style={{ fontSize: '0.68rem', color: '#999' }}>{p.venue}</span>}
          {state && <span style={{ marginLeft: 'auto' }}><Badge {...state} /></span>}
        </div>

        {changes.length > 0 && (
          <div style={{
            display: 'grid', gridTemplateColumns: 'minmax(90px, max-content) 1fr',
            columnGap: '0.7rem', rowGap: '0.15rem', marginTop: '0.3rem', fontSize: '0.74rem',
          }}>
            {changes.map((c, i) => (
              <div key={i} style={{ display: 'contents' }}>
                <span style={{ color: '#888', overflowWrap: 'anywhere' }}>{c.field}</span>
                <span style={{ color: '#333', overflowWrap: 'anywhere' }}>
                  {c.before !== undefined && (
                    <>
                      <span style={{ color: '#999', textDecoration: 'line-through' }}>{c.before}</span>
                      <span style={{ color: '#bbb', margin: '0 0.3rem' }}>→</span>
                    </>
                  )}
                  <span style={{ fontWeight: 500 }}>{c.after}</span>
                </span>
              </div>
            ))}
          </div>
        )}
        {p?.more_changes ? (
          <div style={{ fontSize: '0.68rem', color: '#999', marginTop: '0.2rem' }}>
            and {p.more_changes} more change{p.more_changes === 1 ? '' : 's'}
          </div>
        ) : null}
        {p?.note && (
          <div style={{
            fontSize: '0.72rem', color: '#777', marginTop: '0.3rem', fontStyle: 'italic',
            display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden',
          }}>
            {p.note}
          </div>
        )}
        {(p?.warnings ?? []).map((w, i) => (
          <div key={i} style={{ fontSize: '0.7rem', color: '#7a5b00', marginTop: '0.25rem' }}>⚠ {w}</div>
        ))}
        {row.outcome_note && row.outcome !== 'done' && (
          <div style={{ fontSize: '0.7rem', color: row.outcome === 'failed' ? '#842029' : '#7a5b00', marginTop: '0.25rem' }}>
            {row.outcome_note}
          </div>
        )}

        {isAdmin && row.input_params && (
          <>
            <button
              onClick={() => setDetails(d => !d)}
              style={{
                background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'inherit',
                fontSize: '0.66rem', color: '#bbb', padding: '0.25rem 0 0',
              }}
            >
              {details ? 'Hide technical details' : `Technical details · ${row.connector_name}.${row.action}`}
            </button>
            {details && (
              <pre style={{
                fontSize: '0.66rem', color: '#888', backgroundColor: '#faf8f5',
                padding: '0.4rem', borderRadius: 6, marginTop: '0.3rem',
                overflow: 'auto', maxHeight: 250, lineHeight: 1.4,
                whiteSpace: 'pre-wrap', wordBreak: 'break-word', border: '1px solid #f0ebe5',
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

  const label = cardLabel(status, rows);
  const tone: Tone = pending ? 'pending' : label === 'Declined' || label.startsWith('Not done') ? 'muted' : 'good';

  const button = (primary: boolean, disabled: boolean): CSSProperties => ({
    padding: '0.35rem 1rem', fontSize: '0.75rem', fontWeight: 500, fontFamily: 'inherit',
    border: primary ? 'none' : '1px solid #e2ddd7', borderRadius: 6,
    backgroundColor: primary ? '#a08060' : '#fff', color: primary ? '#fff' : '#888',
    cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.6 : 1,
  });

  return (
    <div style={{
      border: `1px solid ${pending ? '#e2ddd7' : '#e2e3e5'}`, borderRadius: 10,
      backgroundColor: pending ? '#faf8f5' : '#f7f7f8', marginTop: '0.5rem', overflow: 'hidden',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.7rem 0.9rem 0.5rem' }}>
        <Badge label={label} tone={tone} />
        <span style={{ fontSize: '0.7rem', color: '#999' }}>
          {rows.length} change{rows.length === 1 ? '' : 's'}
        </span>
      </div>

      {groups.map(group => {
        const ids = group.rows.map(r => r.id);
        const all = ids.every(id => ticked.has(id));
        const open = expanded.has(group.title);
        const shown = open ? group.rows : group.rows.slice(0, FIRST_ROWS);
        return (
          <div key={group.title} style={{ backgroundColor: '#fff', borderTop: '1px solid #f0ebe5' }}>
            <div style={{
              display: 'flex', alignItems: 'center', gap: '0.5rem',
              padding: '0.45rem 0.7rem', fontSize: '0.72rem', fontWeight: 600, color: '#a08060',
            }}>
              {pending && group.rows.length > 1 && (
                <input
                  type="checkbox"
                  checked={all}
                  onChange={() => toggle(ids, !all)}
                  aria-label={`Select all: ${group.title}`}
                  style={{ accentColor: '#a08060', cursor: 'pointer' }}
                />
              )}
              <span>{group.title}</span>
              {group.rows.length > 1 && <span style={{ fontWeight: 400, color: '#bbb' }}>{group.rows.length}</span>}
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
                  width: '100%', background: 'none', border: 'none', borderTop: '1px solid #f0ebe5',
                  padding: '0.4rem', fontSize: '0.7rem', color: '#a08060', cursor: 'pointer', fontFamily: 'inherit',
                }}
              >
                {open ? 'Show fewer' : `Show all ${group.rows.length}`}
              </button>
            )}
          </div>
        );
      })}

      {pending && !declining && allowable(rows).length > 0 && (
        <div style={{ padding: '0.55rem 0.9rem 0', borderTop: '1px solid #f0ebe5' }}>
          {allowable(rows).map(t => (
            <label key={t.key} style={{ display: 'flex', gap: '0.45rem', alignItems: 'baseline', fontSize: '0.74rem', color: '#555', cursor: 'pointer', marginBottom: '0.25rem' }}>
              <input
                type="checkbox"
                checked={always.has(t.key)}
                onChange={() => setAlways(prev => {
                  const next = new Set(prev);
                  if (next.has(t.key)) next.delete(t.key); else next.add(t.key);
                  return next;
                })}
                style={{ accentColor: '#a08060', cursor: 'pointer' }}
              />
              <span>Always allow Norm to {t.label} without asking</span>
            </label>
          ))}
          <div style={{ fontSize: '0.66rem', color: '#aaa', marginLeft: '1.25rem' }}>
            Just for you. Change it any time in Settings → Preferences.
          </div>
        </div>
      )}

      {pending && !declining && (
        <div style={{
          display: 'flex', justifyContent: 'flex-end', gap: '0.4rem',
          padding: '0.6rem 0.9rem', borderTop: allowable(rows).length > 0 ? 'none' : '1px solid #f0ebe5',
        }}>
          <button onClick={() => setDeclining(true)} disabled={loading} style={button(false, loading)}>
            Decline
          </button>
          <button onClick={approve} disabled={loading || count === 0} style={button(true, loading || count === 0)}>
            {loading ? '…' : count === rows.length ? (rows.length === 1 ? 'Approve' : 'Approve all') : `Approve ${count} of ${rows.length}`}
          </button>
        </div>
      )}

      {pending && declining && (
        <div style={{ padding: '0.6rem 0.9rem', borderTop: '1px solid #f0ebe5' }}>
          <textarea
            value={reason}
            onChange={e => setReason(e.target.value)}
            placeholder="Why not? (optional — Norm reads this and learns from it)"
            rows={2}
            style={{
              width: '100%', boxSizing: 'border-box', fontFamily: 'inherit', fontSize: '0.75rem',
              border: '1px solid #e2ddd7', borderRadius: 6, padding: '0.4rem', resize: 'vertical',
            }}
          />
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.4rem', marginTop: '0.4rem' }}>
            <button onClick={() => setDeclining(false)} disabled={loading} style={button(false, loading)}>
              Back
            </button>
            <button onClick={decline} disabled={loading} style={button(true, loading)}>
              {loading ? '…' : rows.length === 1 ? 'Decline' : 'Decline all'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
