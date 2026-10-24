'use client';

/**
 * Entertainers — the Performers tab: every performer with their lane and
 * where they are in onboarding; filter chips for the "awaiting acceptance"
 * list and the missing-declaration list (the §15 gate), each with its
 * count; and a performer's record (details, band members, documents,
 * emails). Organisation-wide, so the page hides its venue picker here; the
 * page's header holds Add performer and Settings. Venue access opens it;
 * `payroll:write` unlocks accept, shares and bank/IRD.
 * `?page=entertainers&tab=performers&performer=<id>` opens a record.
 */
import { useCallback, useEffect, useState } from 'react';
import { Search } from 'lucide-react';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import PageState from '../../ui/PageState';
import { useBreakpoint } from '../../../hooks/useBreakpoint';
import { entGet, errorText } from './api';
import { when } from './format';
import { LANE_SHORT, performerChips, registerRows } from './logic';
import type { RegisterTab } from './logic';
import { ChipRow, FilterChip, LaneBadge, Notice, RowCard, Toggle } from './ui';
import AddPerformerDialog from './register/AddPerformerDialog';
import PerformerDialog from './register/PerformerDialog';
import type { EntTabProps } from './workspace';
import type { Lane, Performer } from './types';

export default function EntertainersRegister({ meta, reload, param, setParams, primaryOpen, closePrimary }: EntTabProps) {
  const { isMobile } = useBreakpoint();
  const [rows, setRows] = useState<Performer[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [list, setList] = useState<RegisterTab>('all');
  const [q, setQ] = useState('');
  const [lane, setLane] = useState<Lane | 'all'>('all');
  const [needsReview, setNeedsReview] = useState(false);
  const [includeInactive, setIncludeInactive] = useState(false);
  // The open record is the page's `performer` param (the deep link).
  const openId = param('performer');
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    entGet<{ performers: Performer[] }>('/performers', { include_inactive: true }).then(
      (res) => { if (alive) { setRows(res.performers); setListError(null); } },
      (e: unknown) => { if (alive) setListError(errorText(e)); },
    );
    return () => { alive = false; };
  }, [meta, tick]);

  const refresh = useCallback(() => { setTick((t) => t + 1); reload(); }, [reload]);

  const open = (id: string | null) => setParams({ performer: id ?? undefined });

  const all = rows ?? [];
  const awaiting = all.filter((p) => p.is_active && p.onboarding_status === 'submitted').length;
  const missing = all.filter((p) => p.is_active && p.lane === 'payroll_contractor' && !p.has_declaration).length;
  const shown = registerRows(all, list, q, lane, needsReview, includeInactive || list !== 'all');

  const active = all.filter((p) => p.is_active).length;

  return (
    <div style={{ width: '100%', minWidth: 0 }}>
      {/* The tab's toolbar: which list (chips with their counts — no tabs
          inside tabs), then search and the narrowing filters. */}
      <div style={{ marginBottom: 12 }}>
        <ChipRow label="Performer lists">
          <FilterChip active={list === 'all'} onClick={() => setList('all')}>All performers {rows !== null && <Badge tone="neutral">{active}</Badge>}</FilterChip>
          <FilterChip active={list === 'awaiting'} onClick={() => setList('awaiting')}>Awaiting acceptance {awaiting > 0 && <Badge tone="accent">{awaiting}</Badge>}</FilterChip>
          <FilterChip active={list === 'declarations'} onClick={() => setList('declarations')}>Missing declaration {missing > 0 && <Badge tone="error">{missing}</Badge>}</FilterChip>
        </ChipRow>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginTop: 10 }}>
          <span style={{ position: 'relative', flex: '1 1 200px', maxWidth: isMobile ? undefined : 320, minWidth: 0 }}>
            <Icon icon={Search} size={16} tone="muted" style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
            <input className="n-input" aria-label="Search performers" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: '100%', paddingLeft: 32 }} />
          </span>
          <div style={{ flex: isMobile ? '1 1 100%' : '0 1 auto', minWidth: 0 }}>
            <ChipRow label="Lane">
              <FilterChip active={lane === 'all'} onClick={() => setLane('all')}>All lanes</FilterChip>
              {meta.lanes.map((l) => <FilterChip key={l} active={lane === l} onClick={() => setLane(l)}>{LANE_SHORT[l]}</FilterChip>)}
            </ChipRow>
          </div>
          <Toggle checked={needsReview} onChange={setNeedsReview}>Needs review</Toggle>
          {list === 'all' && <Toggle checked={includeInactive} onChange={setIncludeInactive}>Show inactive</Toggle>}
        </div>
      </div>

      {list === 'declarations' && (
        <div style={{ marginBottom: 12 }}>
          <Notice tone="info">Active withholding-tax performers with no IR330C on record. Each needs the declaration signed on their link, or a signed copy uploaded to their record.</Notice>
        </div>
      )}

      {listError ? (
        <PageState kind="error" title="Couldn’t load the performers" detail={listError} action={<Button size="sm" onClick={refresh}>Try again</Button>} />
      ) : rows === null ? (
        <PageState kind="loading" title="Loading performers…" />
      ) : shown.length === 0 ? (
        <PageState kind="empty" title={all.length === 0 ? 'No performers yet' : 'Nobody matches'} detail={all.length === 0 ? 'Add a performer and Norm sends them their onboarding link.' : undefined} />
      ) : isMobile ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {shown.map((p) => (
            <RowCard key={p.id} onClick={() => open(p.id)} ariaLabel={`Open ${p.name}`}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ fontWeight: 600, flex: '1 1 auto' }}>{p.name}</span>
                <LaneBadge lane={p.lane} />
              </div>
              <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', marginTop: 2, overflowWrap: 'anywhere' }}>{[p.email, p.phone].filter(Boolean).join(' · ') || 'No contact details'}</div>
              <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 6 }}>
                {performerChips(p).map((c) => <Badge key={c.key} tone={c.tone}>{c.label}</Badge>)}
              </div>
            </RowCard>
          ))}
        </div>
      ) : (
        <div className="n-card" style={{ overflowX: 'auto' }}>
          <table className="n-table" style={{ minWidth: 720 }}>
            <thead>
              <tr>
                <th>Performer</th>
                <th>Lane</th>
                <th>Status</th>
                <th>Contact</th>
                <th>Link sent</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((p) => (
                <tr key={p.id} onClick={() => open(p.id)} style={{ cursor: 'pointer' }}>
                  <td>
                    <button type="button" className="n-btn n-btn--link" style={{ fontWeight: 600, color: 'var(--text)', textDecoration: 'none' }} onClick={(e) => { e.stopPropagation(); open(p.id); }}>{p.name}</button>
                    {(p.trading_name || p.company_name) && <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{p.trading_name || p.company_name}</div>}
                  </td>
                  <td><LaneBadge lane={p.lane} /></td>
                  <td>
                    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                      {performerChips(p).map((c) => <Badge key={c.key} tone={c.tone}>{c.label}</Badge>)}
                    </div>
                  </td>
                  <td style={{ color: 'var(--text-soft)', fontSize: 'var(--fs-sm)' }}>
                    <div style={{ overflowWrap: 'anywhere' }}>{p.email || '—'}</div>
                    {p.phone && <div>{p.phone}</div>}
                  </td>
                  <td style={{ color: 'var(--text-soft)', fontSize: 'var(--fs-sm)', whiteSpace: 'nowrap' }}>
                    {p.onboarding_status === 'invited' && p.invite_sent_at ? `${when(p.invite_sent_at)}${p.invite_reminders_sent ? ` · ${p.invite_reminders_sent} reminder${p.invite_reminders_sent === 1 ? '' : 's'}` : ''}` : p.onboarding_status === 'invited' ? 'Not sent' : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {primaryOpen && (
        <AddPerformerDialog
          performerTypes={meta.performer_types}
          onCreated={(p) => { setRows((r) => [p, ...(r ?? [])]); reload(); }}
          onClose={closePrimary}
        />
      )}
      {openId && (
        <PerformerDialog
          performerId={openId}
          meta={meta}
          onClose={() => open(null)}
          onChanged={(p) => setRows((r) => (r ?? []).map((x) => (x.id === p.id ? p : x)))}
        />
      )}
    </div>
  );
}
