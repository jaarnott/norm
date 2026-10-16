/**
 * The Enquiries list's filters: what the toolbar holds, how it maps onto
 * `GET /api/functions/enquiries` params, and the KPI tiles' presets. The
 * tiles must filter to exactly the enquiries their counts cover, so each
 * preset mirrors the API's KPI rule (reports.enquiry_kpis): open = stage new /
 * replied / quoted; needs a reply = next action reply_overdue; follow-ups due
 * = requested / day 7 / day 14 that are due today or overdue.
 */
import { OPEN_STAGES } from './labels';

export type SortKey =
  | 'created_desc' | 'created_asc' | 'due' | 'preferred_date' | 'last_activity'
  | 'contact_asc' | 'contact_desc' | 'subject_asc' | 'subject_desc' | 'guests_asc' | 'guests_desc'
  | 'stage_asc' | 'stage_desc' | 'value_asc' | 'value_desc';

/** A sortable column of the list. Some sort both ways, some (as the API
 *  offers them) only one way. */
export type SortColumn = 'created' | 'subject' | 'contact' | 'preferred_date' | 'guests' | 'value' | 'last_activity' | 'due' | 'stage';

const TWO_WAY: Partial<Record<SortColumn, 'asc' | 'desc'>> = {
  // the direction a first click sorts in
  created: 'desc', subject: 'asc', contact: 'asc', guests: 'desc', value: 'desc', stage: 'asc',
};

/** Which way the list is sorted by this column, or null when it isn't. */
export function sortState(current: SortKey, column: SortColumn): 'asc' | 'desc' | null {
  if (column === 'preferred_date' || column === 'due') return current === column ? 'asc' : null;
  if (column === 'last_activity') return current === column ? 'desc' : null;
  if (current === `${column}_asc`) return 'asc';
  if (current === `${column}_desc`) return 'desc';
  return null;
}

/** The sort after clicking a column header: a new column sorts its natural
 *  way; clicking it again flips a two-way column, or goes back to newest
 *  first for a one-way one. */
export function nextSort(current: SortKey, column: SortColumn): SortKey {
  const first = TWO_WAY[column];
  const state = sortState(current, column);
  if (!first) return state ? 'created_desc' : (column as SortKey);
  if (!state) return `${column}_${first}` as SortKey;
  return `${column}_${state === 'asc' ? 'desc' : 'asc'}` as SortKey;
}

/** '' = any. `stage`: a stage or 'open'. `nextAction`: a next-action kind,
 *  'due' (anything due today or overdue) or 'follow_ups_due'. */
export interface ListFilters {
  q: string;
  stage: string;
  nextAction: string;
  source: string;
  bookingType: string;
  createdFrom: string;
  createdTo: string;
  preferredFrom: string;
  preferredTo: string;
  includeArchived: boolean;
  sort: SortKey;
}

export const DEFAULT_FILTERS: ListFilters = {
  q: '',
  stage: '',
  nextAction: '',
  source: '',
  bookingType: '',
  createdFrom: '',
  createdTo: '',
  preferredFrom: '',
  preferredTo: '',
  includeArchived: false,
  sort: 'created_desc',
};

export const SORTS: SortKey[] = [
  'created_desc', 'created_asc', 'due', 'preferred_date', 'last_activity', 'contact_asc', 'contact_desc',
  'subject_asc', 'subject_desc', 'guests_asc', 'guests_desc', 'stage_asc', 'stage_desc', 'value_asc', 'value_desc',
];

const FOLLOW_UP_KINDS = ['requested', 'day_7', 'day_14'];

export type KpiPreset = 'open' | 'needs_reply' | 'follow_ups_due';

/** The filters a KPI tile applies (everything else back to its default,
 *  except the search and the sort). */
export function presetFilters(preset: KpiPreset, current: ListFilters): ListFilters {
  const base: ListFilters = { ...DEFAULT_FILTERS, q: current.q, sort: current.sort, stage: 'open' };
  if (preset === 'needs_reply') return { ...base, nextAction: 'reply_overdue' };
  if (preset === 'follow_ups_due') return { ...base, nextAction: 'follow_ups_due' };
  return base;
}

/** Which tile the current filters are exactly (for its selected look). */
export function activePreset(f: ListFilters): KpiPreset | null {
  const rest = f.source || f.bookingType || f.createdFrom || f.createdTo || f.preferredFrom || f.preferredTo || f.includeArchived;
  if (rest || f.stage !== 'open') return null;
  if (f.nextAction === '') return 'open';
  if (f.nextAction === 'reply_overdue') return 'needs_reply';
  if (f.nextAction === 'follow_ups_due') return 'follow_ups_due';
  return null;
}

/** How many filters beyond search / stage / next action are set (the "More
 *  filters" count). */
export function extraFilterCount(f: ListFilters): number {
  return [f.source, f.bookingType, f.createdFrom || f.createdTo, f.preferredFrom || f.preferredTo, f.includeArchived]
    .filter(Boolean).length;
}

/** Query params for `GET /enquiries`. `venue`: 'all', 'unassigned' or a venue id. */
export function listParams(f: ListFilters, venue: string, offset: number, limit: number): Record<string, unknown> {
  const p: Record<string, unknown> = { sort: f.sort, limit, offset };
  if (venue && venue !== 'all') p.venue = venue;
  if (f.q.trim()) p.q = f.q.trim();
  if (f.stage === 'open') p.stage = OPEN_STAGES;
  else if (f.stage) p.stage = f.stage;
  if (f.nextAction === 'due') p.due_only = true;
  else if (f.nextAction === 'follow_ups_due') {
    p.next_action = FOLLOW_UP_KINDS;
    p.due_only = true;
  } else if (f.nextAction) p.next_action = f.nextAction;
  if (f.source) p.source = f.source;
  if (f.bookingType) p.booking_type = f.bookingType;
  if (f.createdFrom) p.created_from = f.createdFrom;
  if (f.createdTo) p.created_to = f.createdTo;
  if (f.preferredFrom) p.preferred_from = f.preferredFrom;
  if (f.preferredTo) p.preferred_to = f.preferredTo;
  if (f.includeArchived) p.include_archived = true;
  return p;
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** Filters from a deep link (`?stage=new,replied,quoted&next_action=…&due_only=1`).
 *  Unknown values are ignored. */
export function filtersFromParams(get: (name: string) => string | null): ListFilters {
  const f: ListFilters = { ...DEFAULT_FILTERS };
  const list = (name: string) => (get(name) || '').split(',').map((s) => s.trim()).filter(Boolean);
  const stages = list('stage');
  if (stages.length === 1 && [...OPEN_STAGES, 'confirmed', 'lost', 'open'].includes(stages[0])) f.stage = stages[0];
  else if (stages.length === OPEN_STAGES.length && OPEN_STAGES.every((s) => stages.includes(s))) f.stage = 'open';
  const actions = list('next_action');
  const due = get('due_only') === '1' || get('due_only') === 'true';
  if (due && actions.length === FOLLOW_UP_KINDS.length && FOLLOW_UP_KINDS.every((k) => actions.includes(k))) {
    f.nextAction = 'follow_ups_due';
  } else if (actions.length === 1 && ['requested', 'reply_overdue', 'day_14', 'day_7', 'none'].includes(actions[0])) {
    f.nextAction = actions[0];
  } else if (due) {
    f.nextAction = 'due';
  }
  const q = get('q');
  if (q) f.q = q;
  for (const [param, key] of [['created_from', 'createdFrom'], ['created_to', 'createdTo'], ['preferred_from', 'preferredFrom'], ['preferred_to', 'preferredTo']] as const) {
    const v = get(param);
    if (v && ISO_DATE.test(v)) f[key] = v;
  }
  const sort = get('sort');
  if (sort && (SORTS as string[]).includes(sort)) f.sort = sort as SortKey;
  return f;
}
