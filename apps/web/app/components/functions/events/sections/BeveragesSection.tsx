'use client';

/**
 * Beverages: the bar tab, who may raise it, restrictions (presets plus
 * anything typed), arrival drinks, table ordering with its limit, and notes.
 */
import { useId, useState } from 'react';
import { Plus, X } from 'lucide-react';
import Badge from '../../../ui/Badge';
import Button from '../../../ui/Button';
import Icon from '../../../ui/Icon';
import { fnSend } from '../../shared/api';
import { money, parseMoney } from '../../shared/format';
import {
  RESTRICTION_PRESETS,
  SECTION_BADGE,
  addRestriction,
  centsToInput,
  changedFields,
  parseRestrictions,
  sectionBadge,
  serializeRestrictions,
  textOrNull,
} from '../logic';
import { Fact, FactGrid, Field, FieldGrid, MoneyInput, SectionCard, inputStyle } from '../ui';
import type { EventDetail, EventFields } from '../types';
import { useEditor } from './kit';
import type { SectionCtx, Write } from './kit';
import SectionActions from './SectionActions';

interface BeveragesDraft {
  bar_tab: string;
  authorised: string;
  restrictions: string[];
  arrival_drinks: string;
  table_ordering: boolean;
  table_limit: string;
  notes: string;
}

function draftOf(d: EventDetail): BeveragesDraft {
  const e = d.event;
  return {
    bar_tab: centsToInput(e.bar_tab_cents ?? 0),
    authorised: e.bar_tab_authorised_people ?? '',
    restrictions: parseRestrictions(e.bar_tab_restrictions),
    arrival_drinks: e.arrival_drinks ?? '',
    table_ordering: !!e.table_ordering,
    table_limit: centsToInput(e.table_ordering_limit_cents),
    notes: e.bar_tab_notes ?? '',
  };
}

function fieldsOf(dr: BeveragesDraft): Partial<EventFields> {
  return {
    // The bar tab can't be empty (a 422): blank means no tab, 0.
    bar_tab_cents: parseMoney(dr.bar_tab) ?? 0,
    bar_tab_authorised_people: textOrNull(dr.authorised),
    bar_tab_restrictions: serializeRestrictions(dr.restrictions),
    arrival_drinks: textOrNull(dr.arrival_drinks),
    table_ordering: dr.table_ordering,
    table_ordering_limit_cents: dr.table_ordering ? parseMoney(dr.table_limit) : null,
    bar_tab_notes: textOrNull(dr.notes),
  };
}

export default function BeveragesSection({ ctx }: { ctx: SectionCtx }) {
  const f = { tab: useId(), auth: useId(), add: useId(), drinks: useId(), limit: useId(), notes: useId() };
  const { detail, on } = ctx;
  const ev = detail.event;
  const ed = useEditor('beverages', ctx, () => draftOf(detail));
  const dr = ed.draft;
  const [custom, setCustom] = useState('');
  const badge = SECTION_BADGE[sectionBadge(detail, 'beverages')];
  const restrictions = parseRestrictions(ev.bar_tab_restrictions);

  const save = () => {
    const fields = changedFields(fieldsOf(ed.base), fieldsOf(dr));
    if (!Object.keys(fields).length) {
      ed.cancel();
      return;
    }
    const write: Write = async (note) => fnSend<EventDetail>('PATCH', `/events/${ev.id}`, { version: detail.event.version, note, ...fields });
    void ed.submit(write);
  };

  const anyOn = ['beverages.bar_tab', 'beverages.authorised_people', 'beverages.restrictions', 'beverages.arrival_drinks', 'beverages.table_ordering', 'beverages.notes'].some(on);

  return (
    <SectionCard
      id="fn-event-beverages"
      title="Beverages"
      badge={<Badge tone={badge.tone}>{badge.label}</Badge>}
      actions={!ed.editing && (<><SectionActions ctx={ctx} section="beverages" />{anyOn && ed.editButton}</>)}
      footer={ed.footer(save)}
    >
      {!anyOn ? (
        <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>This venue doesn’t ask about beverages for {ev.booking_label?.toLowerCase() || 'this booking type'}.</div>
      ) : !ed.editing ? (
        <FactGrid>
          {on('beverages.bar_tab') && <Fact label="Bar tab">{(ev.bar_tab_cents ?? 0) > 0 ? money(ev.bar_tab_cents, ctx.currency) : 'No bar tab'}</Fact>}
          {on('beverages.authorised_people') && <Fact label="Authorised to raise it">{ev.bar_tab_authorised_people}</Fact>}
          {on('beverages.restrictions') && (
            <Fact label="Restrictions" wide>
              {restrictions.length ? (
                <span style={{ display: 'inline-flex', flexWrap: 'wrap', gap: 4 }}>{restrictions.map((r) => <Badge key={r}>{r}</Badge>)}</span>
              ) : null}
            </Fact>
          )}
          {on('beverages.arrival_drinks') && <Fact label="Arrival drinks">{ev.arrival_drinks}</Fact>}
          {on('beverages.table_ordering') && (
            <Fact label="Table ordering">
              {ev.table_ordering ? `Yes${ev.table_ordering_limit_cents ? ` — limit ${money(ev.table_ordering_limit_cents, ctx.currency)}` : ''}` : 'No'}
            </Fact>
          )}
          {on('beverages.notes') && <Fact label="Notes" wide>{ev.bar_tab_notes}</Fact>}
        </FactGrid>
      ) : (
        <FieldGrid>
          {on('beverages.bar_tab') && (
            <Field label="Bar tab" htmlFor={f.tab} hint="Leave at 0 for no bar tab.">
              <MoneyInput id={f.tab} value={dr.bar_tab} onChange={(v) => ed.update({ bar_tab: v })} />
            </Field>
          )}
          {on('beverages.authorised_people') && (
            <Field label="Authorised to raise it" htmlFor={f.auth}>
              <input id={f.auth} className="n-input" style={inputStyle} value={dr.authorised} placeholder="e.g. Jo Smith, Sam Lee" onChange={(e) => ed.update({ authorised: e.target.value })} />
            </Field>
          )}
          {on('beverages.restrictions') && (
            <Field label="Restrictions" htmlFor={f.add} wide>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
                {dr.restrictions.map((r) => (
                  <span key={r} className="n-badge n-badge--accent" style={{ paddingRight: 4 }}>
                    {r}
                    <button type="button" aria-label={`Remove ${r}`} onClick={() => ed.update({ restrictions: dr.restrictions.filter((x) => x !== r) })}
                      style={{ display: 'inline-flex', border: 'none', background: 'none', padding: 0, color: 'inherit', cursor: 'pointer' }}>
                      <Icon icon={X} size={12} />
                    </button>
                  </span>
                ))}
                {!dr.restrictions.length && <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>None chosen</span>}
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
                {RESTRICTION_PRESETS.filter((p) => !dr.restrictions.some((x) => x.toLowerCase() === p.toLowerCase())).map((p) => (
                  <Button key={p} size="sm" variant="quiet" icon={Plus} onClick={() => ed.update({ restrictions: addRestriction(dr.restrictions, p) })}>{p}</Button>
                ))}
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                <input id={f.add} className="n-input" style={{ flex: 1, minWidth: 0 }} value={custom} placeholder="Something else, e.g. No shots after 10pm"
                  onChange={(e) => setCustom(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter' && custom.trim()) { e.preventDefault(); ed.update({ restrictions: addRestriction(dr.restrictions, custom) }); setCustom(''); } }} />
                <Button size="sm" disabled={!custom.trim()} onClick={() => { ed.update({ restrictions: addRestriction(dr.restrictions, custom) }); setCustom(''); }}>Add</Button>
              </div>
            </Field>
          )}
          {on('beverages.arrival_drinks') && (
            <Field label="Arrival drinks" htmlFor={f.drinks}>
              <input id={f.drinks} className="n-input" style={inputStyle} value={dr.arrival_drinks} placeholder="e.g. Sparkling on arrival" onChange={(e) => ed.update({ arrival_drinks: e.target.value })} />
            </Field>
          )}
          {on('beverages.table_ordering') && (
            <Field label="Table ordering" htmlFor={f.limit}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <span role="group" aria-label="Table ordering" style={{ display: 'inline-flex', border: '1px solid var(--line-strong)', borderRadius: 'var(--radius)', overflow: 'hidden' }}>
                  {[true, false].map((v) => (
                    <button key={String(v)} type="button" aria-pressed={dr.table_ordering === v} onClick={() => ed.update({ table_ordering: v })}
                      style={{
                        height: 32, padding: '0 14px', border: 'none', cursor: 'pointer', font: 'inherit', fontSize: 'var(--fs-sm)',
                        fontWeight: dr.table_ordering === v ? 600 : 500,
                        background: dr.table_ordering === v ? 'var(--selected)' : 'var(--bg)', color: 'var(--text)',
                      }}>
                      {v ? 'Yes' : 'No'}
                    </button>
                  ))}
                </span>
                {dr.table_ordering && (
                  <span style={{ flex: '1 1 120px' }}>
                    <MoneyInput id={f.limit} value={dr.table_limit} onChange={(v) => ed.update({ table_limit: v })} placeholder="Limit" ariaLabel="Table ordering limit" />
                  </span>
                )}
              </div>
            </Field>
          )}
          {on('beverages.notes') && (
            <Field label="Notes" htmlFor={f.notes} wide>
              <textarea id={f.notes} className="n-input" rows={2} style={inputStyle} value={dr.notes} onChange={(e) => ed.update({ notes: e.target.value })} />
            </Field>
          )}
        </FieldGrid>
      )}
      {ed.errorStrip}
    </SectionCard>
  );
}
