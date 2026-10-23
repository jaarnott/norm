/**
 * The Weekly Report's emails — an automated task per schedule, pure logic.
 *
 * A weekly report is an ordinary Norm automated task (`/api/automated-tasks`):
 * a prompt, a weekly schedule and a tool list, run by the scheduler as the
 * person who set it up. Recipients live in the prompt, as they do in every
 * task made in chat ("email felipe@… the kitchen labour report"); this page
 * writes the prompt from one template, with two lines it can find again:
 *
 *     Send to: anna@venue.co.nz, ben@venue.co.nz
 *     Venues: all
 *
 * Changing who gets it rewrites only those lines, so anything added to the
 * instructions in the task's conversation stays. A task whose instructions no
 * longer carry them gets the standard template back when it's saved here
 * (the editor says so first).
 */

import type { AutomatedTask } from '../../../types';

export const REPORT_TITLE = 'Weekly Report';

/** The prompt's first line. The page finds its tasks by it (or by the title). */
export const MARKER = 'Weekly Report — made on the Weekly Report page, which finds this task by this first line. Keep it.';

export const DESCRIPTION =
  "Last week's sales against budget and last year by venue, wages, top sellers and cost of goods, emailed to the people listed.";

/** What a run may call. Bare action names, as tasks made in chat carry them. */
export const TOOL_FILTER = [
  'get_daily_performance',
  'get_sales',
  'get_cogs_detail_for_period',
  'send_report_email',
  'search_tool_result',
];

export const DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'] as const;
export type Day = (typeof DAYS)[number];

export interface ReportSettings {
  recipients: string[];
  /** 'all', or one venue's name as Loaded's tools take it. */
  venues: string;
}

export interface ScheduleSettings {
  day: Day;
  hour: number;
  minute: number;
}

const SEND_TO = /^Send to:[ \t]*(.*)$/m;
const VENUES = /^Venues:[ \t]*(.*)$/m;
const EMAIL = /[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;
const ONE_EMAIL = /^[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$/;

/** The task's full instructions. */
export function buildPrompt(s: ReportSettings): string {
  const venues = s.venues && s.venues.toLowerCase() !== 'all' ? s.venues : 'all';
  const one = venues !== 'all';
  return [
    MARKER,
    `Send to: ${s.recipients.join(', ')}`,
    `Venues: ${venues}`,
    '',
    `Every run, email last week's trading report to the addresses on the "Send to" line, for the venues on the "Venues" line ("all" means every venue). Use only figures the tools return, and never work out dates yourself: pass the period "last week" and use the window the tools return.`,
    '',
    `1. Call get_daily_performance with venues ${one ? `["${venues}"]` : '"all"'}, period "last week" and interval "week".`,
    `2. Call get_sales with the same venues, period "last week", breakdown "items" and top 10.`,
    `3. Call get_cogs_detail_for_period with period "last week" once for each venue in step 1's by_venue (one venue per call).`,
    '4. Write the report in markdown, in this order:',
    '   - A heading "Weekly Report" and the trading week, as the window the tools returned states it.',
    `   - One sentence on ${one ? 'the venue' : 'the group'}: sales against budget, and against last year${one ? '' : ' counting only venues that have a last-year figure (a venue that was not trading a year ago is left out of that comparison)'}.`,
    `   - "Sales by venue": a table Venue | Sales | Budget | vs budget | Last year | vs last year | Wages | Wage %, one row per venue from by_venue${one ? '' : ', then a Group row from summary whose vs last year counts only venues with a last-year figure'}. Sales, Budget and Last year include GST; Wages is actual_cost; Wage % is the tool's wage_pct (labour cost over sales ex GST). Show "—" where a venue has no budget or no last-year figure.`,
    '   - "Top sellers": a table # | Item | Qty | Sales with the rows from step 2, leaving out "(others)". Note under it that item sales are before discounts.',
    `   - "Cost of goods": a table Venue | Sales ex GST | Cost | Cost %, from each venue's summary.column_sums (revenue and cost; Cost % is cost ÷ revenue)${one ? '' : ', then a Group row adding the venues up'}.`,
    '   Money in whole dollars with thousands separators ($12,345). Percentages to one decimal place, signed for vs budget and vs last year (+5.7%, −6.5%).',
    `5. Call send_report_email with to set to the addresses on the "Send to" line (comma-separated), subject "Weekly Report — " followed by the trading week, and content_markdown holding the whole report. content_markdown is required: without it nothing is sent.`,
    '6. Reply with one line saying who it was sent to.',
  ].join('\n');
}

/** What a task's prompt says, and whether it is still the standard template
 *  (both lines present, so they can be changed in place). */
export function parseSettings(prompt: string | null | undefined): ReportSettings & { standard: boolean } {
  const text = prompt || '';
  const to = SEND_TO.exec(text);
  const venues = VENUES.exec(text);
  const recipients = to ? splitRecipients(to[1]).valid : unique(text.match(EMAIL) ?? []);
  return {
    recipients,
    venues: venues?.[1]?.trim() || 'all',
    standard: !!to && !!venues && text.includes(MARKER),
  };
}

/** The prompt with new settings: the two lines replaced in place, or the
 *  standard template when they're gone. */
export function applySettings(prompt: string | null | undefined, s: ReportSettings): string {
  const current = parseSettings(prompt);
  if (!current.standard || !prompt) return buildPrompt(s);
  const venues = s.venues && s.venues.toLowerCase() !== 'all' ? s.venues : 'all';
  // The steps name the venues too (step 1); a change of venue rebuilds them.
  if (venues.toLowerCase() !== current.venues.toLowerCase()) return buildPrompt(s);
  return prompt
    .replace(SEND_TO, () => `Send to: ${s.recipients.join(', ')}`)
    .replace(VENUES, () => `Venues: ${venues}`);
}

/** A task this page manages. */
export function isWeeklyReportTask(task: Pick<AutomatedTask, 'title' | 'prompt'>): boolean {
  if ((task.prompt || '').includes(MARKER)) return true;
  return (task.title || '').trim().toLowerCase() === REPORT_TITLE.toLowerCase();
}

function unique(list: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of list) {
    const key = raw.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(raw);
  }
  return out;
}

/** "a@x.co, b@y.co\nc@z" → valid addresses (deduplicated) and the rest. */
export function splitRecipients(text: string): { valid: string[]; invalid: string[] } {
  const parts = (text || '')
    .split(/[\s,;]+/)
    .map((p) => p.trim().replace(/^<|>$/g, ''))
    .filter(Boolean);
  const valid = unique(parts.filter((p) => ONE_EMAIL.test(p)));
  const invalid = parts.filter((p) => !ONE_EMAIL.test(p));
  return { valid, invalid };
}

/** The task's schedule_config read back, defaulting as the scheduler does
 *  (Monday, 9:00 — task_scheduler.compute_next_run_at). */
export function readSchedule(config: Record<string, unknown> | null | undefined): ScheduleSettings {
  const c = config || {};
  const raw = String(c.day_of_week ?? 'monday').slice(0, 3).toLowerCase();
  const day = DAYS.find((d) => d.startsWith(raw)) ?? 'monday';
  const hour = Number.isInteger(Number(c.hour)) ? Number(c.hour) : 9;
  const minute = Number.isInteger(Number(c.minute)) ? Number(c.minute) : 0;
  return { day, hour, minute };
}

/** schedule_config for a weekly task; keeps any other keys (the timezone). */
export function scheduleConfig(base: Record<string, unknown> | null | undefined, s: ScheduleSettings): Record<string, unknown> {
  return { ...(base || {}), day_of_week: s.day, hour: s.hour, minute: s.minute };
}

export function timeValue(s: Pick<ScheduleSettings, 'hour' | 'minute'>): string {
  return `${String(s.hour).padStart(2, '0')}:${String(s.minute).padStart(2, '0')}`;
}

export function fromTimeValue(v: string): { hour: number; minute: number } | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(v || '');
  if (!m) return null;
  const hour = +m[1];
  const minute = +m[2];
  if (hour > 23 || minute > 59) return null;
  return { hour, minute };
}

export function dayName(day: Day): string {
  return day.charAt(0).toUpperCase() + day.slice(1);
}

/** "Mondays at 8:00am". */
export function scheduleLabel(s: ScheduleSettings): string {
  const h12 = s.hour % 12 === 0 ? 12 : s.hour % 12;
  return `${dayName(s.day)}s at ${h12}:${String(s.minute).padStart(2, '0')}${s.hour < 12 ? 'am' : 'pm'}`;
}

/** The request that sets a new weekly report up (POST /api/automated-tasks).
 *  It is created as a draft; the page then switches it on (…/resume). */
export function createBody(s: ReportSettings, when: ScheduleSettings, timezone: string | null | undefined) {
  return {
    title: REPORT_TITLE,
    description: DESCRIPTION,
    agent_slug: 'reports',
    prompt: buildPrompt(s),
    schedule_type: 'weekly',
    schedule_config: scheduleConfig(timezone ? { timezone } : {}, when),
    tool_filter: TOOL_FILTER,
  };
}

/** The request that changes one (PUT /api/automated-tasks/{id}). A task with
 *  a tool list gets the report's tools added to it (one made in chat and
 *  named "Weekly Report" may not carry them); a task with none (every tool)
 *  is left alone — the API leaves a field it isn't sent unchanged. */
export function updateBody(task: Pick<AutomatedTask, 'prompt' | 'schedule_config' | 'tool_filter'>, s: ReportSettings, when: ScheduleSettings) {
  const body: { prompt: string; schedule_type: string; schedule_config: Record<string, unknown>; tool_filter?: string[] } = {
    prompt: applySettings(task.prompt, s),
    schedule_type: 'weekly',
    schedule_config: scheduleConfig(task.schedule_config, when),
  };
  if (Array.isArray(task.tool_filter) && TOOL_FILTER.some((t) => !task.tool_filter!.includes(t))) {
    body.tool_filter = [...task.tool_filter, ...TOOL_FILTER.filter((t) => !task.tool_filter!.includes(t))];
  }
  return body;
}
