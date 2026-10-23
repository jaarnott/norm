'use client';

/**
 * The Attendance screen's tables and cards (by person, by day) and the flag
 * badges they share. Presentational: everything here reads the rows that
 * model.ts built.
 */

import React from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import Badge from '../../ui/Badge';
import Icon from '../../ui/Icon';
import { formatMoney, formatNumber } from '../../../lib/format';
import {
  flagTotal, minutesLabel, shareLabel, spansLabel,
  type DayRow, type Flags, type PersonDay, type PersonRow,
} from './model';

export const hours = (n: number) => formatNumber(n, 1);
export const money = (n: number) => formatMoney(n, 0);

/** Any finished clock-in (one still open has no hours yet). */
const hasWorked = (days: PersonDay[]) => days.some((d) => d.clocked.some((c) => c.end !== null));

/** Worked minus rostered so far; a dash when there's nothing to compare yet. */
export function diffCell(r: { workedHours: number; rosteredSoFarHours: number }): string {
  if (!r.workedHours && !r.rosteredSoFarHours) return '—';
  return diffLabel(r.workedHours, r.rosteredSoFarHours);
}

export function diffLabel(worked: number, rostered: number): string {
  const d = Math.round((worked - rostered) * 10) / 10;
  if (d === 0) return '0.0';
  return `${d > 0 ? '+' : '−'}${formatNumber(Math.abs(d), 1)}`;
}

// ── badges ────────────────────────────────────────────────────────────────

/** One day's flags, said in full ("Late 12 min", "Missed 9:15 AM – 10:00 AM"). */
export function DayBadges({ d }: { d: PersonDay }) {
  const out: React.ReactNode[] = [];
  if (d.status === 'no_show') out.push(<Badge key="ns" tone="error" title="Rostered, never clocked in">No-show</Badge>);
  if (d.status === 'not_in') out.push(<Badge key="ni" tone="error" title="The shift has started and they haven't clocked in">Not clocked in</Badge>);
  if (d.status === 'unrostered') out.push(<Badge key="ur" tone="info" title="Clocked in with no shift rostered">Not rostered</Badge>);
  if (d.status === 'on_shift') out.push(<Badge key="on" tone="ok">On shift</Badge>);
  if (d.status === 'upcoming') out.push(<Badge key="up">Still to come</Badge>);
  if (d.missedBefore.length) out.push(<Badge key="mb" tone="warn" title="Rostered, but over before they clocked in">Missed {spansLabel(d.missedBefore)}</Badge>);
  if (d.lateMins !== null) out.push(<Badge key="la" tone="warn">Late {minutesLabel(d.lateMins)}</Badge>);
  if (d.earlyMins !== null) out.push(<Badge key="ea" tone="warn">Left {minutesLabel(d.earlyMins)} early</Badge>);
  if (d.missedAfter.length) out.push(<Badge key="ma" tone="warn" title="Rostered, but started after they clocked out">Missed {spansLabel(d.missedAfter)}</Badge>);
  if (d.over) out.push(<Badge key="ov" tone="warn" title="Worked more than 20% over the rostered hours">Over roster</Badge>);
  if (d.leaveHours > 0) out.push(<Badge key="lv" title="Booked leave — not counted as worked">Leave {hours(d.leaveHours)} h</Badge>);
  if (d.ahead) out.push(<Badge key="ah" title={AHEAD}>Entered ahead</Badge>);
  return out.length ? <span style={{ display: 'inline-flex', flexWrap: 'wrap', gap: 4 }}>{out}</span> : null;
}

const DIFF_TITLE = 'Hours worked minus hours rostered, counting only rostered shifts that have finished';
const AHEAD = 'Clock-ins are already in Loaded for a time that hasn\u2019t come yet';
const several = (n: number) => (n > 1 ? ` ×${n}` : '');

/** A rollup's flags as counts ("Late ×2"). */
export function CountBadges({ flags, leaveHours, onShift, ahead }: { flags: Flags; leaveHours: number; onShift?: boolean; ahead?: boolean }) {
  const out: React.ReactNode[] = [];
  if (flags.noShow) out.push(<Badge key="ns" tone="error" title="Rostered, never clocked in">No-show{several(flags.noShow)}</Badge>);
  if (flags.late) out.push(<Badge key="la" tone="warn">Late{several(flags.late)}</Badge>);
  if (flags.early) out.push(<Badge key="ea" tone="warn">Left early{several(flags.early)}</Badge>);
  if (flags.unrostered) out.push(<Badge key="ur" tone="info" title="Clocked in with no shift rostered">Not rostered{several(flags.unrostered)}</Badge>);
  if (flags.over) out.push(<Badge key="ov" tone="warn" title="Worked more than 20% over the rostered hours">Over roster{several(flags.over)}</Badge>);
  if (onShift) out.push(<Badge key="on" tone="ok">On shift</Badge>);
  if (leaveHours > 0) out.push(<Badge key="lv" title="Booked leave — not counted as worked">Leave {hours(leaveHours)} h</Badge>);
  if (ahead) out.push(<Badge key="ah" title={AHEAD}>Entered ahead</Badge>);
  return out.length ? <span style={{ display: 'inline-flex', flexWrap: 'wrap', gap: 4 }}>{out}</span> : null;
}

/** A single day's row reads better in full; several days as counts. */
function RowBadges({ days, flags, leaveHours }: { days: PersonDay[]; flags: Flags; leaveHours: number }) {
  if (days.length === 1) return <DayBadges d={days[0]} />;
  return <CountBadges flags={flags} leaveHours={leaveHours} onShift={days.some((d) => d.status === 'on_shift')} ahead={days.some((d) => d.ahead)} />;
}

// ── shared bits ───────────────────────────────────────────────────────────

function Chevron({ open }: { open: boolean }) {
  return <Icon icon={open ? ChevronDown : ChevronRight} size="dense" tone="muted" />;
}

const toggleStyle: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'flex-start', gap: 6, padding: 0, border: 'none', background: 'none',
  font: 'inherit', color: 'inherit', textAlign: 'left', cursor: 'pointer',
};

const subTable: React.CSSProperties = { width: '100%', borderCollapse: 'collapse', fontSize: 'var(--fs-sm)' };
const subTh: React.CSSProperties = { padding: '6px 8px', textAlign: 'left', fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--muted)', borderBottom: '1px solid var(--line)', whiteSpace: 'nowrap' };
const subTd: React.CSSProperties = { padding: '7px 8px', borderBottom: '1px solid var(--line-soft)', verticalAlign: 'top' };
const num: React.CSSProperties = { textAlign: 'right', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' };

/** Rostered and clocked times for one person-day, one stretch per line. */
function Times({ d, kind }: { d: PersonDay; kind: 'rostered' | 'clocked' }) {
  const spans = kind === 'rostered' ? d.rostered : d.clocked;
  if (!spans.length) return <span style={{ color: 'var(--muted)' }}>{kind === 'rostered' ? 'Not rostered' : d.status === 'leave' ? 'On leave' : '—'}</span>;
  return (
    <span style={{ display: 'inline-flex', flexDirection: 'column' }}>
      {spans.map((s) => <span key={s.start} style={{ whiteSpace: 'nowrap' }}>{spansLabel([s])}</span>)}
    </span>
  );
}

/** Phone: "Rostered" and "Clocked" with their times lined up beside them. */
function TimesGrid({ d }: { d: PersonDay }) {
  const label: React.CSSProperties = { color: 'var(--muted)' };
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '1px 8px', marginTop: 4, fontSize: 'var(--fs-xs)', color: 'var(--text-soft)' }}>
      <span style={label}>Rostered</span>
      <Times d={d} kind="rostered" />
      <span style={label}>Clocked</span>
      <Times d={d} kind="clocked" />
    </div>
  );
}

/** The lines under an open row: one per day (person view) or per person (day view). */
function DetailLines({ lines, first }: { lines: PersonDay[]; first: 'day' | 'person' }) {
  return (
    <table style={subTable}>
      <thead>
        <tr>
          <th style={subTh}>{first === 'day' ? 'Day' : 'Person'}</th>
          <th style={subTh}>Rostered</th>
          <th style={subTh}>Clocked</th>
          <th style={{ ...subTh, ...num }}>Rostered h</th>
          <th style={{ ...subTh, ...num }}>Worked h</th>
          <th style={{ ...subTh, ...num }}>Cost</th>
          <th style={subTh}>Flags</th>
        </tr>
      </thead>
      <tbody>
        {lines.map((d) => (
          <tr key={d.key}>
            <td style={{ ...subTd, whiteSpace: 'nowrap', fontWeight: 500 }}>
              {first === 'day' ? d.dayLabel : d.name}
              {first === 'person' && d.role && <span style={{ display: 'block', fontSize: 'var(--fs-xs)', color: 'var(--muted)', fontWeight: 400 }}>{d.role}</span>}
            </td>
            <td style={subTd}><Times d={d} kind="rostered" /></td>
            <td style={subTd}><Times d={d} kind="clocked" /></td>
            <td style={{ ...subTd, ...num }}>{d.rostered.length ? hours(d.rosteredHours) : '—'}</td>
            <td style={{ ...subTd, ...num }}>{hasWorked([d]) ? hours(d.workedHours) : '—'}</td>
            <td style={{ ...subTd, ...num }}>{hasWorked([d]) ? money(d.workedCost) : '—'}</td>
            <td style={subTd}><DayBadges d={d} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Phone: the same lines as small blocks. */
function DetailBlocks({ lines, first }: { lines: PersonDay[]; first: 'day' | 'person' }) {
  return (
    <ul style={{ listStyle: 'none', margin: '8px 0 0', padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
      {lines.map((d) => (
        <li key={d.key} style={{ borderTop: '1px solid var(--line-soft)', paddingTop: 8, fontSize: 'var(--fs-sm)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
            <span style={{ fontWeight: 600 }}>{first === 'day' ? d.dayLabel : d.name}</span>
            <span style={{ color: 'var(--text-soft)', whiteSpace: 'nowrap' }}>
              {d.clocked.length && d.rostered.length
                ? `${hours(d.workedHours)} h of ${hours(d.rosteredHours)} h`
                : d.clocked.length ? `${hours(d.workedHours)} h` : d.rostered.length ? `rostered ${hours(d.rosteredHours)} h` : ''}
            </span>
          </div>
          <TimesGrid d={d} />
          <div style={{ marginTop: 4 }}><DayBadges d={d} /></div>
        </li>
      ))}
    </ul>
  );
}

// ── by person ─────────────────────────────────────────────────────────────

export function PersonTable({ rows, singleDay, open, onToggle, onlyFlagged }: {
  rows: PersonRow[];
  singleDay: boolean;
  open: Set<string>;
  onToggle: (key: string) => void;
  onlyFlagged: boolean;
}) {
  return (
    <table className="n-table">
      <thead>
        <tr>
          <th>Person</th>
          {singleDay && <th>Rostered</th>}
          {singleDay && <th>Clocked</th>}
          <th className="num">Rostered h</th>
          <th className="num">Worked h</th>
          <th className="num" title={DIFF_TITLE}>Difference</th>
          <th className="num">Labour cost</th>
          <th>Flags</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((p) => {
          const isOpen = !singleDay && open.has(p.key);
          const day = singleDay ? p.days[0] : null;
          const lines = onlyFlagged ? p.days.filter((d) => flagTotal(d.flags) > 0) : p.days;
          return (
            <React.Fragment key={p.key}>
              <tr>
                <td style={{ verticalAlign: 'top' }}>
                  {singleDay ? (
                    <span style={{ fontWeight: 600 }}>{p.name}</span>
                  ) : (
                    <button type="button" style={toggleStyle} aria-expanded={isOpen} onClick={() => onToggle(p.key)}>
                      <span style={{ marginTop: 2 }}><Chevron open={isOpen} /></span>
                      <span style={{ fontWeight: 600 }}>{p.name}</span>
                    </button>
                  )}
                  {p.role && <span style={{ display: 'block', paddingLeft: singleDay ? 0 : 20, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{p.role}</span>}
                </td>
                {day && <td style={{ verticalAlign: 'top', fontSize: 'var(--fs-sm)' }}><Times d={day} kind="rostered" /></td>}
                {day && <td style={{ verticalAlign: 'top', fontSize: 'var(--fs-sm)' }}><Times d={day} kind="clocked" /></td>}
                <td className="num" style={{ verticalAlign: 'top' }}>{p.days.some((d) => d.rostered.length) ? hours(p.rosteredHours) : '—'}</td>
                <td className="num" style={{ verticalAlign: 'top' }}>{hasWorked(p.days) ? hours(p.workedHours) : '—'}</td>
                <td className="num" style={{ verticalAlign: 'top', color: 'var(--text-soft)' }}>{diffCell(p)}</td>
                <td className="num" style={{ verticalAlign: 'top' }}>{hasWorked(p.days) ? money(p.workedCost) : '—'}</td>
                <td style={{ verticalAlign: 'top' }}><RowBadges days={p.days} flags={p.flags} leaveHours={p.leaveHours} /></td>
              </tr>
              {isOpen && (
                <tr>
                  <td colSpan={6} style={{ padding: '4px 8px 12px 20px', background: 'var(--surface)' }}>
                    <DetailLines lines={lines} first="day" />
                  </td>
                </tr>
              )}
            </React.Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

export function PersonCards({ rows, singleDay, open, onToggle, onlyFlagged }: {
  rows: PersonRow[];
  singleDay: boolean;
  open: Set<string>;
  onToggle: (key: string) => void;
  onlyFlagged: boolean;
}) {
  return (
    <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
      {rows.map((p) => {
        const isOpen = !singleDay && open.has(p.key);
        const day = singleDay ? p.days[0] : null;
        const lines = onlyFlagged ? p.days.filter((d) => flagTotal(d.flags) > 0) : p.days;
        const head = (
          <>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'baseline' }}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontWeight: 600, minWidth: 0 }}>
                {!singleDay && <Chevron open={isOpen} />}
                {p.name}
              </span>
              <span style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>{hours(p.workedHours)} h</span>
            </div>
            <div style={{ marginTop: 2, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
              {[p.role, `rostered ${hours(p.rosteredHours)} h`, money(p.workedCost)].filter(Boolean).join(' · ')}
            </div>
            {day && <TimesGrid d={day} />}
            <div style={{ marginTop: 6 }}><RowBadges days={p.days} flags={p.flags} leaveHours={p.leaveHours} /></div>
          </>
        );
        return (
          <li key={p.key} className="n-card" style={{ padding: '10px 12px' }}>
            {singleDay ? head : (
              <button type="button" style={{ ...toggleStyle, display: 'block', width: '100%' }} aria-expanded={isOpen} onClick={() => onToggle(p.key)}>
                {head}
              </button>
            )}
            {isOpen && <DetailBlocks lines={lines} first="day" />}
          </li>
        );
      })}
    </ul>
  );
}

// ── by day ────────────────────────────────────────────────────────────────

export interface DaySales { sales_ex_gst: number | null }

export function DayTable({ rows, sales, open, onToggle, onlyFlagged }: {
  rows: DayRow[];
  sales: Map<string, DaySales> | null;
  open: Set<string>;
  onToggle: (key: string) => void;
  onlyFlagged: boolean;
}) {
  return (
    <table className="n-table">
      <thead>
        <tr>
          <th>Day</th>
          <th className="num">Rostered h</th>
          <th className="num">Worked h</th>
          <th className="num" title={DIFF_TITLE}>Difference</th>
          <th className="num">Labour cost</th>
          <th className="num">Sales ex GST</th>
          <th className="num">Labour %</th>
          <th>Flags</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((d) => {
          const isOpen = open.has(d.dayKey);
          const s = sales?.get(d.dayKey)?.sales_ex_gst ?? null;
          const lines = onlyFlagged ? d.people.filter((x) => flagTotal(x.flags) > 0) : d.people;
          return (
            <React.Fragment key={d.dayKey}>
              <tr>
                <td>
                  <button type="button" style={toggleStyle} aria-expanded={isOpen} onClick={() => onToggle(d.dayKey)}>
                    <span style={{ marginTop: 2 }}><Chevron open={isOpen} /></span>
                    <span style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>{d.dayLabel}</span>
                  </button>
                  <span style={{ display: 'block', paddingLeft: 20, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
                    {d.people.length} {d.people.length === 1 ? 'person' : 'people'}
                  </span>
                </td>
                <td className="num">{hours(d.rosteredHours)}</td>
                <td className="num">{hours(d.workedHours)}</td>
                <td className="num" style={{ color: 'var(--text-soft)' }}>{diffCell(d)}</td>
                <td className="num">{money(d.workedCost)}</td>
                <td className="num">{sales === null ? '…' : s !== null ? money(s) : '—'}</td>
                <td className="num">{(sales !== null && shareLabel(d.workedCost, s)) || '—'}</td>
                <td><CountBadges flags={d.flags} leaveHours={d.leaveHours} onShift={d.people.some((x) => x.status === 'on_shift')} ahead={d.people.some((x) => x.ahead)} /></td>
              </tr>
              {isOpen && (
                <tr>
                  <td colSpan={8} style={{ padding: '4px 8px 12px 20px', background: 'var(--surface)' }}>
                    {lines.length ? <DetailLines lines={lines} first="person" /> : <span style={{ color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>Nothing flagged this day.</span>}
                  </td>
                </tr>
              )}
            </React.Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

export function DayCards({ rows, sales, open, onToggle, onlyFlagged }: {
  rows: DayRow[];
  sales: Map<string, DaySales> | null;
  open: Set<string>;
  onToggle: (key: string) => void;
  onlyFlagged: boolean;
}) {
  return (
    <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
      {rows.map((d) => {
        const isOpen = open.has(d.dayKey);
        const s = sales?.get(d.dayKey)?.sales_ex_gst ?? null;
        const share = sales !== null ? shareLabel(d.workedCost, s) : null;
        const lines = onlyFlagged ? d.people.filter((x) => flagTotal(x.flags) > 0) : d.people;
        return (
          <li key={d.dayKey} className="n-card" style={{ padding: '10px 12px' }}>
            <button type="button" style={{ ...toggleStyle, display: 'block', width: '100%' }} aria-expanded={isOpen} onClick={() => onToggle(d.dayKey)}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'baseline' }}>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontWeight: 600 }}>
                  <Chevron open={isOpen} />
                  {d.dayLabel}
                </span>
                <span style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>{hours(d.workedHours)} h</span>
              </div>
              <div style={{ marginTop: 2, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
                {[`rostered ${hours(d.rosteredHours)} h`, money(d.workedCost), share ? `${share} of sales` : null].filter(Boolean).join(' · ')}
              </div>
              <div style={{ marginTop: 6 }}><CountBadges flags={d.flags} leaveHours={d.leaveHours} onShift={d.people.some((x) => x.status === 'on_shift')} ahead={d.people.some((x) => x.ahead)} /></div>
            </button>
            {isOpen && (lines.length ? <DetailBlocks lines={lines} first="person" /> : (
              <div style={{ marginTop: 8, fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>Nothing flagged this day.</div>
            ))}
          </li>
        );
      })}
    </ul>
  );
}
