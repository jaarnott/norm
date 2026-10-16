/**
 * Web-form builder helpers: the fixed field set (forms.py FORM_FIELD_TYPES),
 * field keys, choice lists, and the light checks run before a save — the
 * API repeats every one of them (forms._clean_fields) and is the authority.
 */
import type { ChoiceOption, FormField, FormFieldType } from './types';

export const FIELD_TYPES: { value: FormFieldType; label: string }[] = [
  { value: 'name', label: 'Name (first and last)' },
  { value: 'email', label: 'Email' },
  { value: 'phone', label: 'Phone' },
  { value: 'text', label: 'Short text' },
  { value: 'long_text', label: 'Long text' },
  { value: 'number', label: 'Number' },
  { value: 'date', label: 'Date' },
  { value: 'dates', label: 'Several dates' },
  { value: 'time_range', label: 'Start and finish time' },
  { value: 'choice', label: 'Choice (pick one)' },
  { value: 'checkbox', label: 'Tick boxes' },
  { value: 'section', label: 'Section heading' },
];

/** What an answer may fill on the enquiry a live form creates (forms.MAPS_TO). */
export const MAPS_TO: { value: string; label: string }[] = [
  { value: 'contact.name', label: 'Contact name' },
  { value: 'contact.email', label: 'Contact email' },
  { value: 'contact.phone', label: 'Contact phone' },
  { value: 'contact.company', label: 'Contact company' },
  { value: 'guests', label: 'Guests' },
  { value: 'preferred_date', label: 'Preferred date' },
  { value: 'preferred_dates', label: 'Preferred dates' },
  { value: 'preferred_time', label: 'Preferred time' },
  { value: 'booking_type', label: 'Booking type' },
  { value: 'subject', label: 'Enquiry subject' },
  { value: 'client_note', label: 'Client note' },
];

export const KEY_PATTERN = /^[a-z][a-z0-9_]{0,39}$/;

export function typeLabel(type: string): string {
  return FIELD_TYPES.find((t) => t.value === type)?.label ?? type;
}

export function hasChoices(type: string): boolean {
  return type === 'choice' || type === 'checkbox';
}

/** "Event date (approx.)" → "event_date_approx", unique among `taken`. */
export function keyFromLabel(label: string, taken: Iterable<string>): string {
  let base = label
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 36);
  if (!base || !/^[a-z]/.test(base)) base = `field${base ? `_${base}` : ''}`.slice(0, 36);
  const used = new Set(taken);
  if (!used.has(base)) return base;
  for (let n = 2; ; n += 1) {
    const candidate = `${base}_${n}`;
    if (!used.has(candidate)) return candidate;
  }
}

/** One choice per line; "value | Label" sets a value different from the label. */
export function choicesToText(options: FormField['options']): string {
  if (!Array.isArray(options)) return '';
  return options.map((o) => (o.value && o.value !== o.label ? `${o.value} | ${o.label}` : o.label)).join('\n');
}

export function textToChoices(text: string): ChoiceOption[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const bar = line.indexOf('|');
      if (bar === -1) return { value: line, label: line };
      const value = line.slice(0, bar).trim();
      const label = line.slice(bar + 1).trim();
      return { value: value || label, label: label || value };
    });
}

/** The problems the API would refuse, phrased for the builder. */
export function fieldProblems(fields: Pick<FormField, 'key' | 'label' | 'type' | 'options'>[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  fields.forEach((f, i) => {
    const name = f.label.trim() || `Field ${i + 1}`;
    if (!f.label.trim()) out.push(`Field ${i + 1} needs a label.`);
    if (f.type !== 'section' || f.key) {
      if (!KEY_PATTERN.test(f.key)) {
        out.push(`${name}: the key must start with a letter and use lowercase letters, numbers and underscores.`);
      } else if (seen.has(f.key)) {
        out.push(`Two fields share the key “${f.key}”.`);
      }
      seen.add(f.key);
    }
    if (f.type === 'choice' && !(Array.isArray(f.options) && f.options.length)) {
      out.push(`${name} needs at least one choice.`);
    }
  });
  if (fields.length > 60) out.push('A form can have up to 60 fields.');
  return out;
}

/** A submitted answer as text (name objects, date lists, time ranges, ticks). */
export function answerText(value: unknown): string {
  if (value === null || value === undefined || value === '') return '';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (Array.isArray(value)) return value.map(answerText).filter(Boolean).join(', ');
  if (typeof value === 'object') {
    const v = value as Record<string, unknown>;
    if ('first' in v || 'last' in v) return [v.first, v.last].filter(Boolean).join(' ');
    if ('start' in v || 'end' in v || 'finish' in v) {
      return [v.start, v.end ?? v.finish].filter(Boolean).join(' – ');
    }
    return Object.values(v).map(answerText).filter(Boolean).join(', ');
  }
  return String(value);
}

/** Where a lead came from, in a few words: campaign tags, an ad click, the
 *  referring site, or "Direct". */
export function sourceSummary(attribution: Record<string, string> | null | undefined): string {
  const a = attribution ?? {};
  if (a.utm_source) return [a.utm_source, a.utm_medium, a.utm_campaign].filter(Boolean).join(' · ');
  if (a.gclid || a.gbraid || a.wbraid) return 'Google Ads';
  if (a.referrer) {
    try {
      return new URL(a.referrer).hostname.replace(/^www\./, '');
    } catch {
      return a.referrer;
    }
  }
  return 'Direct';
}
