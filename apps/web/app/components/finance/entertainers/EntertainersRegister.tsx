'use client';

/**
 * Entertainers — the Register: every performer with their lane and where
 * they are in onboarding, the "awaiting acceptance" list and the missing-
 * declaration list (the §15 gate), "Add performer → send link", and a
 * performer's record (details, band members, documents, emails). Venue
 * access opens it; `payroll:write` unlocks accept, shares and bank/IRD.
 * `?page=ent-register&performer=<id>` opens a record.
 */
import { useCallback, useEffect, useState } from 'react';
import { Plus, Search, Settings } from 'lucide-react';
import type { DisplayBlockProps } from '../../display/DisplayBlockRenderer';
import Badge from '../../ui/Badge';
import Button from '../../ui/Button';
import Icon from '../../ui/Icon';
import PageHeader from '../../ui/PageHeader';
import PageState from '../../ui/PageState';
import Tabs from '../../ui/Tabs';
import { useBreakpoint } from '../../../hooks/useBreakpoint';
import { ToastHost } from '../shared/Toasts';
import { entGet, errorText } from './api';
import { when } from './format';
import { LANE_SHORT, performerChips, registerRows } from './logic';
import type { RegisterTab } from './logic';
import { ChipRow, FilterChip, LaneBadge, Notice, RowCard, Toggle } from './ui';
import { invalidateEntMeta, readPageParam, setPageParam, useEntMeta } from './useMeta';
import AddPerformerDialog from './register/AddPerformerDialog';
import PerformerDialog from './register/PerformerDialog';
import SettingsDialog from './settings/SettingsDialog';
import type { Lane, Performer } from './types';

export function ModeBanner({ mode }: { mode: 'off' | 'shadow' | 'live' | undefined }) {
  if (mode !== 'shadow') return null;
  return (
    <div style={{ marginBottom: 12 }}>
      <Notice tone="warn">Shadow mode — Orbit is still the live system. Bookings and approvals made here stay in Norm; emails go only to the payroll contact, and nothing is paid from here yet.</Notice>
    </div>
  );
}

export function OffState({ title }: { title: string }) {
  return (
    <>
      <PageHeader title={title} />
      <PageState kind="empty" title="Entertainers isn’t switched on for this organisation" detail="A platform admin turns it on (shadow first, then live)." />
    </>
  );
}

export default function EntertainersRegister({ props }: DisplayBlockProps) {
  const isPage = !!props?.persistVenue;
  const { meta, error, off, loading, reload } = useEntMeta();
  const { isMobile } = useBreakpoint();
  const [rows, setRows] = useState<Performer[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [tab, setTab] = useState<RegisterTab>('all');
  const [q, setQ] = useState('');
  const [lane, setLane] = useState<Lane | 'all'>('all');
  const [needsReview, setNeedsReview] = useState(false);
  const [includeInactive, setIncludeInactive] = useState(false);
  const [openId, setOpenId] = useState<string | null>(() => (isPage ? readPageParam('performer') : null));
  const [adding, setAdding] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!meta) return;
    let alive = true;
    entGet<{ performers: Performer[] }>('/performers', { include_inactive: true }).then(
      (res) => { if (alive) { setRows(res.performers); setListError(null); } },
      (e: unknown) => { if (alive) setListError(errorText(e)); },
    );
    return () => { alive = false; };
  }, [meta, tick]);

  const refresh = useCallback(() => { setTick((t) => t + 1); reload(); }, [reload]);

  const open = (id: string | null) => {
    setOpenId(id);
    if (isPage) setPageParam('performer', id);
  };

  if (off) return <OffState title="Entertainers" />;
  if (loading) {
    return (<><PageHeader title="Entertainers" /><PageState kind="loading" title="Loading…" /></>);
  }
  if (!meta) {
    return (<><PageHeader title="Entertainers" /><PageState kind="error" title="Couldn’t load entertainers" detail={error ?? undefined} action={<Button size="sm" onClick={reload}>Try again</Button>} /></>);
  }

  const all = rows ?? [];
  const awaiting = all.filter((p) => p.is_active && p.onboarding_status === 'submitted').length;
  const missing = all.filter((p) => p.is_active && p.lane === 'payroll_contractor' && !p.has_declaration).length;
  const shown = registerRows(all, tab, q, lane, needsReview, includeInactive || tab !== 'all');

  return (
    <div style={{ width: '100%' }}>
      <PageHeader
        title="Entertainers"
        meta={`${all.filter((p) => p.is_active).length} active performers`}
        actions={
          <>
            {meta.can_edit_settings && <Button icon={Settings} onClick={() => setSettingsOpen(true)}>Settings</Button>}
            <Button variant="primary" icon={Plus} onClick={() => setAdding(true)}>Add performer</Button>
          </>
        }
      >
        <Tabs
          label="Register lists"
          value={tab}
          onChange={(id) => setTab(id as RegisterTab)}
          items={[
            { id: 'all', label: 'All performers' },
            { id: 'awaiting', label: <>Awaiting acceptance {awaiting > 0 && <Badge tone="accent">{awaiting}</Badge>}</> },
            { id: 'declarations', label: <>Missing declaration {missing > 0 && <Badge tone="error">{missing}</Badge>}</> },
          ]}
        />
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginTop: 10 }}>
          <span style={{ position: 'relative', flex: '1 1 200px', maxWidth: 320 }}>
            <Icon icon={Search} size={16} tone="muted" style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
            <input className="n-input" aria-label="Search performers" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: '100%', paddingLeft: 32 }} />
          </span>
          <ChipRow label="Lane">
            <FilterChip active={lane === 'all'} onClick={() => setLane('all')}>All lanes</FilterChip>
            {meta.lanes.map((l) => <FilterChip key={l} active={lane === l} onClick={() => setLane(l)}>{LANE_SHORT[l]}</FilterChip>)}
          </ChipRow>
          <Toggle checked={needsReview} onChange={setNeedsReview}>Needs review</Toggle>
          {tab === 'all' && <Toggle checked={includeInactive} onChange={setIncludeInactive}>Show inactive</Toggle>}
        </div>
      </PageHeader>

      <ModeBanner mode={meta.mode} />

      {tab === 'declarations' && (
        <div style={{ marginBottom: 12 }}>
          <Notice tone="info">Active withholding-tax performers with no IR330C on record. Each needs the declaration signed on their link, or a signed copy uploaded to their record.</Notice>
        </div>
      )}

      {listError ? (
        <PageState kind="error" title="Couldn’t load the register" detail={listError} action={<Button size="sm" onClick={refresh}>Try again</Button>} />
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

      {adding && (
        <AddPerformerDialog
          performerTypes={meta.performer_types}
          onCreated={(p) => { setRows((r) => [p, ...(r ?? [])]); reload(); }}
          onClose={() => setAdding(false)}
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
      {settingsOpen && (
        <SettingsDialog
          meta={meta}
          onSaved={() => { setSettingsOpen(false); invalidateEntMeta(); reload(); }}
          onClose={() => setSettingsOpen(false)}
        />
      )}
      {isPage && <ToastHost />}
    </div>
  );
}
