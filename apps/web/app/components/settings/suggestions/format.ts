// Pure helpers for the Suggestions admin page: showing and reading values,
// and saying how suggestions are picked in plain words.
import type { PromptRow, SettingRow, ValueKind } from './types';

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

/** A stored value as the field shows it: fractions as percents. */
export function toField(kind: ValueKind, value: unknown): string {
  if (typeof value !== 'number') return '';
  if (kind === 'percent') return String(Math.round(value * 1000) / 10);
  return String(value);
}

/** A field's text back to the stored value; null when it isn't a number. */
export function fromField(kind: ValueKind, text: string): number | null {
  const n = Number(text.trim());
  if (text.trim() === '' || !Number.isFinite(n)) return null;
  return kind === 'percent' ? Math.round(n * 10) / 1000 : n;
}

/** A value with its unit, for "default: …" hints. */
export function shown(kind: ValueKind, value: unknown): string {
  if (typeof value === 'boolean') return value ? 'on' : 'off';
  if (typeof value !== 'number') return String(value ?? '—');
  switch (kind) {
    case 'percent': return `${Math.round(value * 1000) / 10}%`;
    case 'dollars': return `$${value.toLocaleString()}`;
    case 'days': return `${value} day${value === 1 ? '' : 's'}`;
    case 'hours': return `${value} h`;
    case 'times': return `×${value}`;
    default: return String(value);
  }
}

/** When a catalogue prompt shows, in words. */
export function when(p: Pick<PromptRow, 'days' | 'part' | 'month_start'>): string {
  const bits: string[] = [];
  if (p.days && p.days.length) bits.push(p.days.length === 7 ? 'every day' : p.days.map(d => DAYS[d]).join(', '));
  if (p.part) bits.push(`${p.part}s`);
  if (p.month_start) bits.push('first week of the month');
  return bits.length ? bits.join(' · ') : 'any time';
}

/** A new custom prompt's id: member.words_of_the_prompt. */
export function promptId(member: string, text: string, taken: Set<string>): string {
  const base = text.toLowerCase().replace(/\{venue\}/g, '').replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 40) || 'prompt';
  let id = `${member}.${base}`;
  for (let i = 2; taken.has(id); i++) id = `${member}.${base}_${i}`;
  return id;
}

/** How Home picks, from the live settings — the page's opening paragraph. */
export function howItPicks(settings: SettingRow[]): string[] {
  const v = (name: string) => settings.find(s => s.name === name)?.value;
  const out: string[] = [];
  if (v('insights_enabled') === false) {
    out.push('Insights are switched off: Home shows only waiting work and catalogue prompts.');
  } else {
    out.push(
      `Home shows 4 suggestions on a desktop and 2 on a phone. Insights come first — up to ${v('insight_slots_desktop')} on a desktop and ${v('insight_slots_phone')} on a phone` +
      (v('area_diversity') ? ', from different areas (sales, wages, cost of goods) while there is a choice.' : '.'),
    );
  }
  out.push(
    (v('pending_slot') ? 'Then one waiting-on-you item when there is real work (invoices that need a person). ' : '') +
    'Then catalogue prompts, one per AI agent before any agent gets a second.',
  );
  out.push(
    `An insight scores by its detector's weight and its dollars a week, plus ${v('venue_boost')} at the venue the person is in. ` +
    `A catalogue prompt scores by its weight — ×${v('regular_boost')} if they ask it often, ×${v('discover_boost')} for something new — plus ${v('timely_bonus')} when it's its time.`,
  );
  out.push(
    `An insight is shown to a person at most once in ${v('repeat_days')} days unless its value moves ${shown('percent', v('repeat_move'))}; a dismissed suggestion stays away ${v('dismiss_days')} days.`,
  );
  out.push('Every suggestion must work for the person: the AI agent hired, the App on, the permission held and the venue’s connection healthy.');
  return out;
}
