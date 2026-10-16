'use client';

/**
 * Norm Functions — Dashboard: what needs doing today (replies owed,
 * follow-ups, Day 7 / call-and-close, quote answers, client edits, events
 * coming up unconfirmed, the importer's "Orbit said confirmed" reviews), the
 * open pipeline, the next four weeks' expected sales and conversion. One call
 * (`GET /api/functions/dashboard?venue=`); every number is the API's.
 */
import { useMemo, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { ChevronRight, Plus } from 'lucide-react';
import type { DisplayBlockProps } from '../display/DisplayBlockRenderer';
import Chart, { DEFAULT_COLORS } from '../display/Chart';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import Icon from '../ui/Icon';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
import VenueSelect from '../ui/VenueSelect';
import ModeBanner from './shared/ModeBanner';
import { dateLabel, money } from './shared/format';
import { useFunctionsMeta } from './shared/meta';
import { FnHeader, Muted, READ_ONLY_HINT, Section, StatTile, TileRow, useFnQuery, useWidth } from './enquiries/bits';
import { dayOf, pctLabel, stageLabel, stageTone } from './enquiries/labels';
import { openFnPage } from './enquiries/nav';
import { useVenueFilter } from './enquiries/useVenueFilter';
import {
  GROUP_LABELS, attentionBadge, attentionGroup, attentionNote, attentionTarget, attentionWhen, groupCounts, type AttentionGroup,
} from './dashboard/attention';
import type { Dashboard } from './reports/types';

const SHOWN = 10;
const TWO_COLUMNS_FROM = 900;

export default function FunctionsDashboard({ props }: DisplayBlockProps) {
  const page = !!props?.persistVenue;
  const { meta, error: metaError, reload } = useFunctionsMeta();
  const [venue, setVenue, venueOptions] = useVenueFilter(meta);
  const [group, setGroup] = useState<AttentionGroup | 'all'>('all');
  const [showAll, setShowAll] = useState(false);
  const [weekTable, setWeekTable] = useState(false);
  const [boxRef, width] = useWidth();
  const wide = width >= TWO_COLUMNS_FROM;

  const query = useFnQuery<Dashboard>(meta ? '/dashboard' : null, { venue: venue === 'all' ? undefined : venue });
  const { data, error, loading } = query;

  const items = useMemo(() => data?.needs_attention ?? [], [data]);
  const groups = useMemo(() => groupCounts(items), [items]);
  const filtered = group === 'all' ? items : items.filter((i) => attentionGroup(i.kind) === group);
  const shown = showAll ? filtered : filtered.slice(0, SHOWN);

  if (metaError) {
    return (
      <div>
        {page && <PageHeader title="Functions dashboard" />}
        <PageState kind="error" title="Couldn’t open Functions" detail={metaError} action={<Button size="sm" onClick={reload}>Try again</Button>} />
      </div>
    );
  }
  if (!meta) {
    return (
      <div>
        {page && <PageHeader title="Functions dashboard" />}
        <PageState kind="loading" title="Loading…" />
      </div>
    );
  }

  const currency = meta.company.currency;
  const tz = data?.timezone || meta.company.default_timezone;
  const header = (
    <FnHeader
      page={page}
      title="Functions dashboard"
      meta={data ? `Today, ${dateLabel(data.today)}` : undefined}
      actions={
        <>
          {venueOptions.length > 1 && <VenueSelect venues={venueOptions} value={venue} onChange={setVenue} allowAll testId="fn-dashboard-venue" />}
          <Button
            variant="primary"
            icon={Plus}
            onClick={() => openFnPage('enquiries', { new: 1 })}
            disabled={!meta.can_write}
            title={meta.can_write ? 'Record a new enquiry' : READ_ONLY_HINT}
          >
            New enquiry
          </Button>
        </>
      }
    />
  );

  if (error && !data) {
    return (
      <div ref={boxRef}>
        <ModeBanner mode={meta.mode} />
        {header}
        <PageState kind="error" title="Couldn’t load the dashboard" detail={error} action={<Button size="sm" onClick={query.retry}>Try again</Button>} />
      </div>
    );
  }
  if (!data) {
    return (
      <div ref={boxRef}>
        <ModeBanner mode={meta.mode} />
        {header}
        <PageState kind="loading" title="Loading the dashboard…" />
      </div>
    );
  }

  const conv = data.conversion;
  const tiles = (
    <TileRow min={170}>
      <StatTile
        label="Enquiries, last 28 days"
        value={data.enquiries_last_28_days.count}
        changePct={data.enquiries_last_28_days.change_pct}
        changeLabel={`vs ${data.enquiries_last_28_days.prior} the 28 days before`}
        onClick={() => openFnPage('reports', { view: 'enquiries' })}
        testId="fn-dash-received"
      />
      <StatTile
        label="Open enquiries"
        value={data.open_enquiries.open}
        sub={`${money(data.open_enquiries.open_value_cents, currency, 0)} open value`}
        onClick={() => openFnPage('enquiries', { stage: 'new,replied,quoted' })}
        testId="fn-dash-open"
      />
      <StatTile
        label="Next 4 weeks"
        value={money(data.next_4_weeks.expected_spend_cents, currency, 0)}
        sub={`${data.next_4_weeks.events} ${data.next_4_weeks.events === 1 ? 'event' : 'events'} · expected sales`}
        onClick={() => openFnPage('reports', { view: 'sales' })}
        testId="fn-dash-next4"
      />
      <StatTile
        label="Conversion"
        value={pctLabel(conv.rate_pct)}
        sub={`${conv.confirmed} won, ${conv.lost} lost · ${conv.lag_weeks}-week lag`}
        onClick={() => openFnPage('reports', { view: 'conversion' })}
        testId="fn-dash-conversion"
      />
      <StatTile
        label="Booked events"
        value={data.booked_events.count}
        sub={`${money(data.booked_events.expected_spend_cents, currency, 0)} expected`}
        onClick={() => openFnPage('events')}
        testId="fn-dash-booked"
      />
    </TileRow>
  );

  const attention = (
    <Section title="Needs attention" meta={items.length ? `${items.length}` : undefined} padded={false} testId="fn-needs-attention">
      {groups.length > 1 && (
        <div role="group" aria-label="Show" style={{ display: 'flex', gap: 6, flexWrap: 'wrap', padding: '10px 16px 0' }}>
          <Chip active={group === 'all'} onClick={() => { setGroup('all'); setShowAll(false); }}>All {items.length}</Chip>
          {groups.map((g) => (
            <Chip key={g.group} active={group === g.group} onClick={() => { setGroup(g.group); setShowAll(false); }}>{GROUP_LABELS[g.group]} {g.count}</Chip>
          ))}
        </div>
      )}
      {items.length === 0 ? (
        <div style={{ padding: '14px 16px' }}><Muted>Nothing needs attention right now.</Muted></div>
      ) : (
        <ul style={{ listStyle: 'none', margin: 0, padding: '6px 8px 8px' }}>
          {shown.map((it, i) => {
            const target = attentionTarget(it);
            const badge = attentionBadge(it);
            const when = attentionWhen(it, data.today);
            return (
              <li key={`${it.kind}:${it.enquiry_id}:${it.event_id}:${i}`}>
                <button
                  type="button"
                  className="n-row"
                  onClick={() => target && openFnPage(target.page, target.params)}
                  disabled={!target}
                  style={{ alignItems: 'flex-start', fontWeight: 400 }}
                  data-testid="fn-attention-item"
                >
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <Badge tone={badge.tone}>{badge.label}</Badge>
                      <span style={{ fontWeight: 500, minWidth: 0, overflowWrap: 'anywhere' }}>{it.title || it.contact_name || 'Untitled'}</span>
                    </span>
                    <span style={{ display: 'block', marginTop: 3, fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
                      {[when, it.contact_name, it.venue_name].filter(Boolean).join(' · ')}
                    </span>
                    {attentionNote(it) && <span style={{ display: 'block', marginTop: 2, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>{attentionNote(it)}</span>}
                  </span>
                  <Icon icon={ChevronRight} size={16} tone="muted" style={{ marginTop: 2 }} />
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {filtered.length > SHOWN && (
        <div style={{ padding: '0 16px 12px' }}>
          <Button size="sm" variant="quiet" onClick={() => setShowAll((s) => !s)}>{showAll ? 'Show fewer' : `Show all ${filtered.length}`}</Button>
        </div>
      )}
    </Section>
  );

  const openTotal = Math.max(1, ...Object.values(data.pipeline.open));
  const pipeline = (
    <Section title="Open pipeline" meta="By stage, now">
      <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
        {(['new', 'replied', 'quoted'] as const).map((s) => {
          const n = data.pipeline.open[s] ?? 0;
          return (
            <li key={s}>
              <button type="button" className="n-row" onClick={() => openFnPage('enquiries', { stage: s })} style={{ fontWeight: 400, padding: '6px 8px' }}>
                <span style={{ width: 76, flex: '0 0 auto' }}><Badge tone={stageTone(s)}>{stageLabel(s)}</Badge></span>
                {/* bar length = this stage's share of the largest stage */}
                <span aria-hidden style={{ flex: 1, minWidth: 0, height: 8, borderRadius: 4, background: 'var(--surface-alt)' }}>
                  <span style={{ display: 'block', height: '100%', width: `${(n / openTotal) * 100}%`, borderRadius: 4, background: DEFAULT_COLORS[0] }} />
                </span>
                <span style={{ width: 44, flex: '0 0 auto', textAlign: 'right', fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{n}</span>
              </button>
            </li>
          );
        })}
      </ul>
      <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--line-soft)' }}>
        <div className="n-eyebrow" style={{ marginBottom: 6 }}>Received in the last 28 days, by stage now</div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {(['new', 'replied', 'quoted', 'confirmed', 'lost'] as const).map((s) => (
            <span key={s} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 'var(--fs-sm)' }}>
              <Badge tone={stageTone(s)}>{stageLabel(s)}</Badge>
              <span style={{ fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{data.pipeline.last_28_days[s] ?? 0}</span>
            </span>
          ))}
        </div>
      </div>
    </Section>
  );

  const weeks = data.next_4_weeks.weeks;
  const salesChart = (
    <div>
      <Chart
        data={{ rows: weeks.map((w) => ({ week: `w/c ${dateLabel(w.week_start, { weekday: false, year: false })}`, sales: w.cents / 100 })) }}
        props={{
          chart_type: 'bar',
          title: 'Expected sales, next 4 weeks',
          x_axis: { key: 'week', label: 'Week' },
          // The chart's formatter takes a format object; the spec types it as a string.
          y_axis: { key: 'sales', label: 'Expected sales', format: { type: 'currency', decimals: 0 } as unknown as string },
          series: [{ key: 'sales', label: 'Expected sales', color: DEFAULT_COLORS[0] }],
        }}
        hideAddToReport
        height={220}
      />
      <div style={{ marginTop: 6 }}>
        <Button size="sm" variant="quiet" onClick={() => setWeekTable((t) => !t)} aria-expanded={weekTable}>
          {weekTable ? 'Hide the weekly numbers' : 'Show the weekly numbers'}
        </Button>
      </div>
      {weekTable && (
        <table className="n-table" aria-label="Expected sales by week" style={{ marginTop: 6, fontSize: 'var(--fs-sm)' }}>
          <thead>
            <tr><th scope="col">Week</th><th scope="col" className="num">Events</th><th scope="col" className="num">Expected sales</th></tr>
          </thead>
          <tbody>
            {weeks.map((w) => (
              <tr key={w.week_start}>
                <td>w/c {dateLabel(w.week_start, { year: false })}</td>
                <td className="num">{w.events}</td>
                <td className="num">{money(w.cents, currency, 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );

  const conversion = (
    <Section
      title="Conversion by venue"
      meta={`Received ${dayOf(conv.cohort_from, tz)} – ${dayOf(conv.cohort_to, tz)}`}
      actions={<Button size="sm" variant="quiet" onClick={() => openFnPage('reports', { view: 'conversion' })}>Details</Button>}
      padded={false}
    >
      <div style={{ overflowX: 'auto' }}>
        <table className="n-table" aria-label="Conversion by venue" style={{ fontSize: 'var(--fs-sm)' }}>
          <thead>
            <tr>
              <th scope="col">Venue</th>
              <th scope="col" className="num">Enquiries</th>
              <th scope="col" className="num">Won</th>
              <th scope="col" className="num">Lost</th>
              <th scope="col" className="num">Rate</th>
            </tr>
          </thead>
          <tbody>
            {conv.by_venue.map((v) => (
              <tr key={v.venue_id ?? 'group'}>
                <td>{v.venue_name ?? 'Group-wide'}</td>
                <td className="num">{v.enquiries}</td>
                <td className="num">{v.confirmed}</td>
                <td className="num">{v.lost}</td>
                <td className="num" style={{ fontWeight: 600 }}>{pctLabel(v.rate_pct)}</td>
              </tr>
            ))}
            {conv.by_venue.length === 0 && <tr><td colSpan={5} style={{ color: 'var(--muted)' }}>No enquiries in this window.</td></tr>}
          </tbody>
        </table>
      </div>
    </Section>
  );

  return (
    <div ref={boxRef}>
      <ModeBanner mode={meta.mode} />
      {header}
      <div style={{ opacity: loading ? 0.55 : 1, transition: 'opacity 0.15s' }} aria-busy={loading || undefined}>
        {error && <PageState kind="error" title="Couldn’t refresh the dashboard" detail={error} />}
        {tiles}
        {wide ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 3fr) minmax(300px, 2fr)', gap: 16, alignItems: 'start', marginTop: 16 }}>
            <div style={column}>{attention}</div>
            <div style={column}>{pipeline}{salesChart}{conversion}</div>
          </div>
        ) : (
          <div style={{ ...column, marginTop: 16 }}>{attention}{pipeline}{salesChart}{conversion}</div>
        )}
      </div>
    </div>
  );
}

const column: CSSProperties = { display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 };

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      style={{
        padding: '4px 10px', borderRadius: 999, cursor: 'pointer', whiteSpace: 'nowrap', fontFamily: 'inherit',
        fontSize: 'var(--fs-sm)', fontWeight: active ? 600 : 500,
        border: `1px solid ${active ? 'var(--brand-soft)' : 'var(--line)'}`,
        background: active ? 'var(--accent-soft)' : 'var(--surface-alt)',
        color: active ? 'var(--accent)' : 'var(--text)',
      }}
    >
      {children}
    </button>
  );
}
